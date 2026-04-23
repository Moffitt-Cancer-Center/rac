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

// Front Door Premium profile
resource frontDoorProfile 'Microsoft.Cdn/profiles@2023-05-01' = {
  name: 'afd-rac-${racEnv}'
  location: 'global'
  tags: tags
  sku: {
    name: 'Premium_AzureFrontDoor'
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
resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2023-05-01' = {
  name: 'origin-appgw-${racEnv}'
  parent: originGroup
  properties: {
    hostName: appGatewayPublicFqdn
    httpPort: 80
    httpsPort: 443
    originHostHeader: appGatewayPublicFqdn
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
resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2023-05-01' = {
  name: 'route-wildcard-${racEnv}'
  parent: frontDoorEndpoint
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
    customDomains: [
      {
        id: customDomain.id
      }
    ]
  }
}

// Custom domain for wildcard
resource customDomain 'Microsoft.Cdn/profiles/customDomains@2023-05-01' = {
  name: 'domain-wildcard-${racEnv}'
  parent: frontDoorProfile
  properties: {
    hostName: '*.${parentDomain}'
    tlsSettings: {
      certificateType: 'ManagedCertificate'
      minimumTlsVersion: 'TLS12'
    }
  }
}

// WAF Policy
resource wafPolicy 'Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2022-05-01' = {
  name: 'waf-rac-${racEnv}'
  location: 'global'
  tags: tags
  properties: {
    policySettings: {
      enabledState: 'Enabled'
      mode: 'Prevention'
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'Microsoft_DefaultRuleSet'
          ruleSetVersion: '2.1'
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
