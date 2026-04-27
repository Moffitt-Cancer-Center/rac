@description('Azure region for all resources')
param location string

@description('Deployment environment: dev | staging | prod')
param racEnv string

@description('Application Gateway name')
param appGwName string

@description('App Gateway subnet ID')
param appGwSubnetId string

@description('Parent DNS domain, e.g., rac.moffitt.org')
param parentDomain string

@description('TLS certificate Key Vault secret ID (full versioned secret URI)')
@secure()
param tlsCertKvSecretId string

@description('App Gateway managed identity resource ID')
param appGwMiResourceId string

@description('Shim internal FQDN to use as the backend pool target (e.g. rac-shim-dev.internal.xxx.azurecontainerapps.io). Leave empty on first deploy — the placeholder FQDN is preserved until the shim ACA app is deployed.')
param shimFqdn string = ''

@description('Control plane internal FQDN to use as a second backend pool. Leave empty on first deploy — the cp.<parentDomain> listener / routing rule is gated on this being non-empty so the gateway can deploy before the control plane exists.')
param controlPlaneFqdn string = ''

@description('Hostname (subdomain prefix) the control plane is reachable at, e.g. "cp" → cp.<parentDomain>. AppGw uses this to build the specific-host listener that takes precedence over the *.<parentDomain> wildcard listener.')
param controlPlaneHostnamePrefix string = 'cp'

@description('Resource tags')
param tags object

// Public IP for Application Gateway
resource publicIP 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-appgw-${racEnv}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  zones: [
    '1'
    '2'
    '3'
  ]
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    idleTimeoutInMinutes: 4
    dnsSettings: {
      domainNameLabel: 'appgw-rac-${racEnv}-${uniqueString(resourceGroup().id)}'
    }
  }
}

// Variables for resource naming and paths
var appGwId = resourceId('Microsoft.Network/applicationGateways', appGwName)

// WAF Policy resource
resource wafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2023-11-01' = {
  name: 'waf-appgw-${racEnv}'
  location: location
  tags: tags
  properties: {
    policySettings: {
      fileUploadLimitInMb: 100
      state: 'Enabled'
      mode: 'Prevention'
      requestBodyCheck: true
      maxRequestBodySizeInKb: 128
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
        }
      ]
    }
  }
}

// Application Gateway
resource appGateway 'Microsoft.Network/applicationGateways@2023-11-01' = {
  name: appGwName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appGwMiResourceId}': {}
    }
  }
  properties: {
    sku: {
      name: 'WAF_v2'
      tier: 'WAF_v2'
      capacity: 2
    }
    gatewayIPConfigurations: [
      {
        name: 'appGatewayIpConfig'
        properties: {
          subnet: {
            id: appGwSubnetId
          }
        }
      }
    ]
    frontendIPConfigurations: [
      {
        name: 'appGatewayFrontendIP'
        properties: {
          publicIPAddress: {
            id: publicIP.id
          }
        }
      }
    ]
    frontendPorts: [
      {
        name: 'appGatewayFrontendPort443'
        properties: {
          port: 443
        }
      }
    ]
    backendAddressPools: concat(
      [
        {
          name: 'appGatewayBackendPool'
          properties: {
            backendAddresses: [
              {
                // When shimFqdn is supplied (post-Phase-6 deploy), route to the shim's
                // ACA internal FQDN.  On first deploy (shimFqdn empty), the placeholder
                // is preserved so the gateway can be provisioned before the shim exists.
                // Use ${location} so the placeholder follows the deploy region rather
                // than being pinned to eastus.
                fqdn: empty(shimFqdn) ? 'shim.internal.${location}.azurecontainerapps.io' : shimFqdn
              }
            ]
          }
        }
      ],
      empty(controlPlaneFqdn) ? [] : [
        {
          name: 'controlPlaneBackendPool'
          properties: {
            backendAddresses: [
              {
                fqdn: controlPlaneFqdn
              }
            ]
          }
        }
      ]
    )
    backendHttpSettingsCollection: concat(
      [
        {
          name: 'appGatewayBackendHttpSettings'
          properties: {
            port: 443
            protocol: 'Https'
            cookieBasedAffinity: 'Disabled'
            requestTimeout: 120
            pickHostNameFromBackendAddress: true
            probeEnabled: true
            probe: {
              // Custom probe to /_shim/health. The default probe (GET /) hits
              // the shim's `_handle` route which performs slug-from-host lookup
              // against the probe's Host header (the ACA internal FQDN), fails,
              // and returns a 404 — making AppGw mark the backend Unhealthy.
              id: '${appGwId}/probes/shimHealthProbe'
            }
          }
        }
      ],
      empty(controlPlaneFqdn) ? [] : [
        {
          name: 'controlPlaneBackendHttpSettings'
          properties: {
            port: 443
            protocol: 'Https'
            cookieBasedAffinity: 'Disabled'
            requestTimeout: 120
            pickHostNameFromBackendAddress: true
            probeEnabled: true
            probe: {
              id: '${appGwId}/probes/controlPlaneHealthProbe'
            }
          }
        }
      ]
    )
    probes: concat(
      [
        {
          name: 'shimHealthProbe'
          properties: {
            protocol: 'Https'
            path: '/_shim/health'
            // Set the host explicitly to the shim's internal FQDN. The
            // pickHostNameFromBackendHttpSettings → pickHostNameFromBackendAddress
            // chain didn't propagate the host through to the probe in
            // practice on AppGw v2 — probe kept hitting the backend with a
            // mismatched Host and getting 404 from the ACA env's frontend.
            host: empty(shimFqdn) ? 'shim.internal.${location}.azurecontainerapps.io' : shimFqdn
            interval: 30
            timeout: 10
            unhealthyThreshold: 3
            match: {
              statusCodes: ['200-399']
            }
          }
        }
      ],
      empty(controlPlaneFqdn) ? [] : [
        {
          name: 'controlPlaneHealthProbe'
          properties: {
            protocol: 'Https'
            path: '/health'
            host: controlPlaneFqdn
            interval: 30
            timeout: 10
            unhealthyThreshold: 3
            match: {
              statusCodes: ['200-399']
            }
          }
        }
      ]
    )
    httpListeners: concat(
      [
        {
          name: 'appGatewayHttpsListener'
          properties: {
            frontendIPConfiguration: {
              id: '${appGwId}/frontendIPConfigurations/appGatewayFrontendIP'
            }
            frontendPort: {
              id: '${appGwId}/frontendPorts/appGatewayFrontendPort443'
            }
            protocol: 'Https'
            sslCertificate: {
              id: '${appGwId}/sslCertificates/appGatewaySslCert'
            }
            // Two hostnames:
            //   - The wildcard *.<parentDomain> handles reviewer URLs that go
            //     to the shim. The control-plane subdomain takes precedence
            //     via the controlPlaneHttpsListener below — AppGw v2 prefers
            //     specific hostnames over wildcards.
            //   - The PIP FQDN itself ensures the listener matches when FD's
            //     SNI = origin hostName (the AppGw PIP FQDN). Without this,
            //     SNI mismatch causes AppGw to fall through to its default 404.
            hostNames: [
              '*.${parentDomain}'
              publicIP.properties.dnsSettings.fqdn
            ]
            requireServerNameIndication: true
          }
        }
      ],
      empty(controlPlaneFqdn) ? [] : [
        {
          // Specific-host listener for cp.<parentDomain>. AppGw v2 prefers
          // a specific hostname over a wildcard when both could match, so
          // requests for cp.<parentDomain> land here while everything else
          // (e.g. <slug>.<parentDomain>) falls through to the shim listener.
          name: 'controlPlaneHttpsListener'
          properties: {
            frontendIPConfiguration: {
              id: '${appGwId}/frontendIPConfigurations/appGatewayFrontendIP'
            }
            frontendPort: {
              id: '${appGwId}/frontendPorts/appGatewayFrontendPort443'
            }
            protocol: 'Https'
            sslCertificate: {
              id: '${appGwId}/sslCertificates/appGatewaySslCert'
            }
            hostNames: [
              '${controlPlaneHostnamePrefix}.${parentDomain}'
            ]
            requireServerNameIndication: true
          }
        }
      ]
    )
    requestRoutingRules: concat(
      [
        {
          name: 'appGatewayRoutingRule'
          properties: {
            ruleType: 'Basic'
            // Lower priority value = higher precedence. Keep the shim rule at
            // 100 and put the control-plane rule at 50 so it's evaluated first.
            priority: 100
            httpListener: {
              id: '${appGwId}/httpListeners/appGatewayHttpsListener'
            }
            backendAddressPool: {
              id: '${appGwId}/backendAddressPools/appGatewayBackendPool'
            }
            backendHttpSettings: {
              id: '${appGwId}/backendHttpSettingsCollection/appGatewayBackendHttpSettings'
            }
          }
        }
      ],
      empty(controlPlaneFqdn) ? [] : [
        {
          name: 'controlPlaneRoutingRule'
          properties: {
            ruleType: 'Basic'
            priority: 50
            httpListener: {
              id: '${appGwId}/httpListeners/controlPlaneHttpsListener'
            }
            backendAddressPool: {
              id: '${appGwId}/backendAddressPools/controlPlaneBackendPool'
            }
            backendHttpSettings: {
              id: '${appGwId}/backendHttpSettingsCollection/controlPlaneBackendHttpSettings'
            }
          }
        }
      ]
    )
    sslCertificates: [
      {
        name: 'appGatewaySslCert'
        properties: {
          keyVaultSecretId: tlsCertKvSecretId
        }
      }
    ]
    firewallPolicy: {
      id: wafPolicy.id
    }
  }
}

@description('Application Gateway resource ID')
output appGatewayId string = appGateway.id

@description('Application Gateway public IP FQDN')
output appGatewayPublicFqdn string = publicIP.properties.dnsSettings.fqdn

@description('Application Gateway public IP address')
output appGatewayPublicIp string = publicIP.properties.ipAddress
