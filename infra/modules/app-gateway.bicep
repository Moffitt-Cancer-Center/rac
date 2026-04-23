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
              fqdn: 'shim.internal.eastus.azurecontainerapps.io' // Placeholder; will be parameterized in Phase 2
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
          httpListener: {
            id: '${appGwId}/httpListeners/appGatewayHttpsListener'
          }
          backendAddressPool: {
            id: '${appGwId}/backendAddressPools/appGatewayBackendPool'
          }
          backendHttpSettings: {
            id: '${appGwId}/backendHttpSettings/appGatewayBackendHttpSettings'
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
output appGatewayPublicFqdn string = publicIP.properties.dnsSettings != null && publicIP.properties.dnsSettings.fqdn != null ? publicIP.properties.dnsSettings.fqdn : publicIP.properties.ipAddress

@description('Application Gateway public IP address')
output appGatewayPublicIp string = publicIP.properties.ipAddress
