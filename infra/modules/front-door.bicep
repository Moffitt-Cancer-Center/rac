@description('Deployment environment: dev | staging | prod')
param racEnv string

@description('Parent DNS domain, e.g., rac.moffitt.org')
param parentDomain string

@description('App Gateway public FQDN')
param appGatewayPublicFqdn string

@description('App Gateway Private Link resource ID (empty if using public FQDN origin)')
param appGatewayPrivateLinkResourceId string = ''

@description('Azure region for shared private link location (empty to skip private link)')
param privateLinkLocation string = ''

@description('Resource tags')
param tags object

@description('Whether to attach the wildcard + apex custom domains. Defaults to false because Front Door ManagedCertificate does not support wildcard hostnames; the customer-cert path requires the FD profile MI to have Key Vault Secrets User on the source KV (granted in role-assignments). Set true after a real cert exists in KV and the role assignment has propagated (pass 2).')
param deployCustomDomain bool = false

@description('Resource ID of the Key Vault secret holding the TLS cert (e.g. /subscriptions/.../vaults/kv-rac-bootstrap-001/secrets/rac-dev-tls). Required when deployCustomDomain=true. Versionless — FD pins useLatestVersion=true so a re-issued cert is picked up automatically.')
param certKvSecretId string = ''

// Front Door Premium profile
//
// `identity: SystemAssigned` is required for the customer-cert flow: FD reads
// the cert from the source Key Vault using its system MI, which is then
// granted Key Vault Secrets User in role-assignments-bootstrap. Without an
// MI here, the secret-source resource fails with "Customer secret access
// denied".
resource frontDoorProfile 'Microsoft.Cdn/profiles@2023-05-01' = {
  name: 'afd-rac-${racEnv}'
  location: 'global'
  tags: tags
  sku: {
    name: 'Premium_AzureFrontDoor'
  }
  identity: {
    type: 'SystemAssigned'
  }
}

// Front Door endpoint
resource frontDoorEndpoint 'Microsoft.Cdn/profiles/afdEndpoints@2023-05-01' = {
  name: 'afd-endpoint-${racEnv}'
  parent: frontDoorProfile
  location: 'global'
  properties: {
    enabledState: 'Enabled'
  }
}

// Origin group
resource originGroup 'Microsoft.Cdn/profiles/originGroups@2023-05-01' = {
  name: 'og-appgw-${racEnv}'
  parent: frontDoorProfile
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
      additionalLatencyInMilliseconds: 50
    }
    sessionAffinityState: 'Disabled'
    trafficRestorationTimeToHealedOrNewEndpointsInMinutes: 10
  }
}

// Origin (using App Gateway public FQDN or shared private link)
//
// originHostHeader is intentionally NOT set: with no value, FD forwards the
// original Host header from the client request (e.g. cp.<parentDomain>),
// which is what AppGw's wildcard listener (`hostNames: ['*.<parentDomain>']`
// + requireServerNameIndication) needs to match. Setting originHostHeader
// to appGatewayPublicFqdn made AppGw see Host: <appgw-pip-fqdn>, which the
// wildcard listener doesn't match → AppGw returned 502 to FD.
resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2023-05-01' = {
  name: 'origin-appgw-${racEnv}'
  parent: originGroup
  properties: {
    hostName: appGatewayPublicFqdn
    httpPort: 80
    httpsPort: 443
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
    sharedPrivateLinkResource: !empty(appGatewayPrivateLinkResourceId) && !empty(privateLinkLocation) ? {
      privateLink: {
        id: appGatewayPrivateLinkResourceId
      }
      groupId: 'appgw'
      privateLinkLocation: privateLinkLocation
    } : null
  }
}

// Route: wildcard route to match *.parentDomain
//
// Explicit dependsOn on `origin`: Bicep infers a dependency on `originGroup`
// via the `originGroup.id` reference, but ARM's Front Door scheduler can
// schedule the route before the origin under that group is fully created,
// which trips "make sure originGroup is created successfully and at least
// one enabled origin is created". Forcing the route to wait on the origin
// itself transitively covers the group and removes the race.
resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2023-05-01' = {
  name: 'route-wildcard-${racEnv}'
  parent: frontDoorEndpoint
  dependsOn: [
    origin
  ]
  properties: {
    originGroup: {
      id: originGroup.id
    }
    supportedProtocols: [
      'Https'
    ]
    patternsToMatch: [
      '/*'
    ]
    forwardingProtocol: 'HttpsOnly'
    linkToDefaultDomain: 'Enabled'
    httpsRedirect: 'Enabled'
    enabledState: 'Enabled'
    customDomains: deployCustomDomain ? [
      {
        id: customDomainWildcard.id
      }
      {
        id: customDomainApex.id
      }
    ] : []
  }
}

// FD-side wrapper around the KV cert. FD profile MI must have Key Vault
// Secrets User on the source vault before this resource can be created;
// role-assignments-bootstrap.bicep handles that grant. `useLatestVersion=true`
// means we don't have to redeploy when the cert is renewed — FD picks up the
// new version automatically (it polls the KV).
resource fdCertSecret 'Microsoft.Cdn/profiles/secrets@2023-05-01' = if (deployCustomDomain) {
  name: 'cert-rac-${racEnv}'
  parent: frontDoorProfile
  properties: {
    parameters: {
      type: 'CustomerCertificate'
      useLatestVersion: true
      secretSource: {
        id: certKvSecretId
      }
    }
  }
}

// Custom domain for the wildcard. CustomerCertificate is required because FD
// ManagedCertificate does not issue wildcard certs.
resource customDomainWildcard 'Microsoft.Cdn/profiles/customDomains@2023-05-01' = if (deployCustomDomain) {
  name: 'domain-wildcard-${racEnv}'
  parent: frontDoorProfile
  properties: {
    hostName: '*.${parentDomain}'
    tlsSettings: {
      certificateType: 'CustomerCertificate'
      minimumTlsVersion: 'TLS12'
      secret: {
        id: fdCertSecret.id
      }
    }
  }
}

// Custom domain for the apex (parentDomain itself). Same cert covers both —
// the LE cert SANs include the apex and the wildcard.
resource customDomainApex 'Microsoft.Cdn/profiles/customDomains@2023-05-01' = if (deployCustomDomain) {
  name: 'domain-apex-${racEnv}'
  parent: frontDoorProfile
  properties: {
    hostName: parentDomain
    tlsSettings: {
      certificateType: 'CustomerCertificate'
      minimumTlsVersion: 'TLS12'
      secret: {
        id: fdCertSecret.id
      }
    }
  }
}

// WAF Policy. The sku.name MUST match the targeting Front Door profile's
// tier — frontDoorProfile above is Premium_AzureFrontDoor, so this must
// be the Premium WAF SKU. Without explicit sku, the policy defaults to
// Classic which Azure rejects on association with a Premium profile
// (errors as "Policy ArmResourceId has incorrect formatting").
// Front Door WAF policy names must be alphanumeric only — no hyphens
// allowed. (App Gateway WAF policies, a different resource type, allow
// hyphens.) Use a name without separators.
resource wafPolicy 'Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2022-05-01' = {
  name: 'wafrac${racEnv}'
  location: 'global'
  tags: tags
  sku: {
    name: 'Premium_AzureFrontDoor'
  }
  properties: {
    policySettings: {
      enabledState: 'Enabled'
      mode: 'Prevention'
    }
    managedRules: {
      // Microsoft_DefaultRuleSet 2.x uses per-rule actions (no ruleset-wide
      // default), and Azure rejects the policy if an unsupported default is
      // implied. 1.1 is the OWASP-based ruleset with a clear ruleset-level
      // Block action and is widely supported. Bump back to 2.x once we have
      // explicit per-rule actions configured.
      managedRuleSets: [
        {
          ruleSetType: 'Microsoft_DefaultRuleSet'
          ruleSetVersion: '1.1'
          ruleSetAction: 'Block'
        }
      ]
    }
  }
}

// Security policy associating WAF with endpoint
resource securityPolicy 'Microsoft.Cdn/profiles/securityPolicies@2023-05-01' = {
  name: 'secpol-${racEnv}'
  parent: frontDoorProfile
  properties: {
    parameters: {
      type: 'WebApplicationFirewall'
      wafPolicy: {
        id: wafPolicy.id
      }
      associations: [
        {
          domains: [
            {
              id: frontDoorEndpoint.id
            }
          ]
          patternsToMatch: [
            '/*'
          ]
        }
      ]
    }
  }
}

@description('Front Door Profile resource ID')
output frontDoorProfileId string = frontDoorProfile.id

@description('Front Door endpoint hostname')
output frontDoorEndpointHostname string = frontDoorEndpoint.properties.hostName

@description('WAF Policy resource ID')
output wafPolicyId string = wafPolicy.id

@description('Front Door profile system-assigned MI principal ID (for KV role assignments)')
output frontDoorMiPrincipalId string = frontDoorProfile.identity.principalId

@description('Wildcard custom domain validation token (TXT value to add at _dnsauth.<wildcard>) — empty when deployCustomDomain=false')
output wildcardDomainValidationToken string = customDomainWildcard.?properties.validationProperties.validationToken ?? ''

@description('Apex custom domain validation token (TXT value to add at _dnsauth.<apex>) — empty when deployCustomDomain=false')
output apexDomainValidationToken string = customDomainApex.?properties.validationProperties.validationToken ?? ''
