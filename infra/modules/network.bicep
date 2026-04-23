@description('Azure region for all resources')
param location string

@description('RAC deployment environment (dev, staging, prod)')
param racEnv string

@description('VNet third octet (10.X.0.0/16). Dev: 10, Staging: 20, Prod: 30')
param vnetOctet int

@description('Resource tags applied to all resources')
param tags object

var vnetName = 'vnet-rac-${racEnv}'
var vnetAddressSpace = '10.${vnetOctet}.0.0/16'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressSpace
      ]
    }
    subnets: [
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: '10.${vnetOctet}.0.0/21'
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
          serviceEndpoints: []
          privateLinkServiceNetworkPolicies: 'Enabled'
          privateEndpointNetworkPolicies: 'Enabled'
        }
      }
      {
        name: 'snet-appgw'
        properties: {
          addressPrefix: '10.${vnetOctet}.8.0/24'
          serviceEndpoints: []
          privateLinkServiceNetworkPolicies: 'Enabled'
          privateEndpointNetworkPolicies: 'Enabled'
        }
      }
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: '10.${vnetOctet}.9.0/24'
          serviceEndpoints: []
          privateLinkServiceNetworkPolicies: 'Enabled'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'snet-pg'
        properties: {
          addressPrefix: '10.${vnetOctet}.10.0/24'
          serviceEndpoints: []
          privateLinkServiceNetworkPolicies: 'Enabled'
          privateEndpointNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

@description('Virtual Network resource ID')
output vnetId string = vnet.id

@description('ACA subnet resource ID')
output acaSubnetId string = '${vnet.id}/subnets/snet-aca'

@description('App Gateway subnet resource ID')
output appGwSubnetId string = '${vnet.id}/subnets/snet-appgw'

@description('Private Endpoint subnet resource ID')
output peSubnetId string = '${vnet.id}/subnets/snet-pe'

@description('Postgres private endpoint subnet resource ID')
output pgSubnetId string = '${vnet.id}/subnets/snet-pg'
