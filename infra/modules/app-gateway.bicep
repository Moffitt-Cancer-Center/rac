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
    backendAddressPools: [
      {
        name: 'appGatewayBackendPool'
        properties: {
          backendAddresses: [
            {
              // When shimFqdn is supplied (post-Phase-6 deploy), route to the shim's
              // ACA internal FQDN.  On first deploy (shimFqdn empty), the placeholder
              // is preserved so the gateway can be provisioned before the shim exists.
              fqdn: empty(shimFqdn) ? 'shim.internal.eastus.azurecontainerapps.io' : shimFqdn
            }
          ]
        }
      }
    ]
    backendHttpSettingsCollection: [
      {
        name: 'appGatewayBackendHttpSettings'
        properties: {
          port: 443
          protocol: 'Https'
          cookieBasedAffinity: 'Disabled'
          requestTimeout: 120
          pickHostNameFromBackendAddress: true
          probeEnabled: false
        }
      }
    ]
    httpListeners: [
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
          hostName: '*.${parentDomain}'
          hostNames: [
            '*.${parentDomain}'
          ]
          requireServerNameIndication: true
        }
      }
    ]
    requestRoutingRules: [
      {
        name: 'appGatewayRoutingRule'
        properties: {
          ruleType: 'Basic'
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
    ]
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
