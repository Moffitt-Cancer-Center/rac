// Private DNS zone for an internal ACA env's default domain.
//
// Internal ACA envs (`vnetConfiguration.internal=true`) don't auto-create
// a private DNS zone — App Gateway and other VNet-resident resources
// can't resolve `*.{defaultDomain}` without one, so probes / proxy calls
// to ACA app internal FQDNs return 502 (DNS unresolvable).
//
// Lives in its own module because the zone's `name` is the env's
// default domain — a runtime-computed value. Bicep refuses to use such
// values as resource names within the same module that produced them
// (BCP120), but accepts them as module *parameters* (resolved at deploy
// time across module boundaries).

@description('ACA env default domain, e.g. "happyfield-f43dfd30.eastus2.azurecontainerapps.io". Pass aca-env.outputs.envDefaultDomain.')
param defaultDomain string

@description('ACA env static IP. Pass aca-env.outputs.envStaticIp.')
param staticIp string

@description('VNet resource ID to link the private DNS zone to.')
param vnetId string

@description('Resource tags applied to all resources')
param tags object

resource zone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: defaultDomain
  location: 'global'
  tags: tags
}

resource vnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: zone
  name: 'link-aca'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnetId
    }
  }
}

resource wildcardA 'Microsoft.Network/privateDnsZones/A@2020-06-01' = {
  parent: zone
  name: '*'
  properties: {
    ttl: 3600
    aRecords: [
      {
        ipv4Address: staticIp
      }
    ]
  }
}
