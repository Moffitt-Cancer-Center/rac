// Shim ACA app — the single public entry point that token-checks every request
// before proxying to researcher apps.
//
// min-replicas=1: the shim cannot scale to zero; it is the entry point for all
// reviewer traffic.  App Gateway sends *.{parentDomain} traffic here.
//
// Verifies: rac-v1.AC6.2, rac-v1.AC7.*, rac-v1.AC10.1, rac-v1.AC12.1

@description('Azure region for the app resource')
param location string

@description('Deployment environment: dev | staging | prod')
param racEnv string

@description('ACA managed environment resource ID')
param managedEnvironmentId string

@description('Shim user-assigned managed identity resource ID (provisioned in Phase 1 managed-identity.bicep)')
param shimMiResourceId string

@description('Full image reference, e.g. racdevacr001.azurecr.io/rac-shim:v1.0')
param imageName string

@description('ACR login server, e.g. racdevacr001.azurecr.io')
param registryServer string

@description('Name of the Key Vault secret holding the shim database DSN (full DSN with password embedded)')
param databaseDsnSecretName string = 'shim-database-dsn'

@description('Name of the Key Vault secret holding the cookie HMAC secret')
param cookieHmacSecretName string = 'shim-cookie-hmac'

@description('Platform Key Vault URI, e.g. https://kv-rac-xxxx-dev.vault.azure.net/')
param kvUri string

@description('Parent DNS domain, e.g. rac.moffitt.org')
param parentDomain string

@description('OIDC issuer URI for reviewer token validation')
param issuer string

@description('Cookie domain for rac_session HttpOnly cookie (typically .{parentDomain})')
param cookieDomain string

@description('ACA internal suffix, e.g. internal.xxxxxxxx.eastus.azurecontainerapps.io')
param acaInternalSuffix string

@description('Institution display name shown in branded error pages')
param institutionName string

@description('Optional institution brand logo URL shown in interstitial and error pages')
param brandLogoUrl string = ''

@description('Enable Prometheus/OpenTelemetry metrics endpoint')
param metricsEnabled bool = true

@description('OTLP exporter endpoint (leave empty to disable OTLP export; local Prometheus still works)')
param otlpEndpoint string = ''

@description('Resource tags')
param tags object

// ---------------------------------------------------------------------------
// Container App
// ---------------------------------------------------------------------------

resource shimApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'rac-shim-${racEnv}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${shimMiResourceId}': {}
    }
  }
  properties: {
    environmentId: managedEnvironmentId
    configuration: {
      // Internal ingress: App Gateway is the public boundary; the shim itself
      // does not need an external ingress endpoint.
      //
      // TOPOLOGY REQUIREMENT: because external=false, the shim's FQDN is only
      // resolvable inside the VNet the ACA managed environment is deployed to.
      // The Application Gateway (modules/app-gateway.bicep) backend pool points
      // at this FQDN and therefore MUST be deployed in the same VNet (or one
      // peered with DNS resolution). If the two live in different VNets without
      // Private Link/peering, App Gateway backend health probes will fail and
      // all traffic will return 502. This is verified operationally in the
      // bootstrap runbook's post-deploy health-probe step.
      ingress: {
        external: false
        targetPort: 8080
        transport: 'http'
      }
      registries: [
        {
          server: registryServer
          identity: shimMiResourceId
        }
      ]
      // Key Vault secret references — the shim MI has Key Vault Secrets User.
      secrets: [
        {
          name: databaseDsnSecretName
          keyVaultUrl: '${kvUri}secrets/${databaseDsnSecretName}'
          identity: shimMiResourceId
        }
        {
          name: cookieHmacSecretName
          keyVaultUrl: '${kvUri}secrets/${cookieHmacSecretName}'
          identity: shimMiResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'shim'
          image: imageName
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(
            [
              // Secrets injected via secretRef — KV values resolved at runtime
              {
                name: 'RAC_SHIM_DATABASE_DSN'
                secretRef: databaseDsnSecretName
              }
              {
                name: 'RAC_SHIM_COOKIE_HMAC_SECRET'
                secretRef: cookieHmacSecretName
              }
              // Plain env vars
              {
                name: 'RAC_SHIM_KV_URI'
                value: kvUri
              }
              {
                name: 'RAC_SHIM_PARENT_DOMAIN'
                value: parentDomain
              }
              {
                name: 'RAC_SHIM_ISSUER'
                value: issuer
              }
              {
                name: 'RAC_SHIM_COOKIE_DOMAIN'
                value: cookieDomain
              }
              {
                name: 'RAC_SHIM_ACA_INTERNAL_SUFFIX'
                value: acaInternalSuffix
              }
              {
                name: 'RAC_SHIM_INSTITUTION_NAME'
                value: institutionName
              }
              {
                name: 'RAC_SHIM_BRAND_LOGO_URL'
                value: brandLogoUrl
              }
              {
                name: 'RAC_SHIM_ENV'
                value: racEnv
              }
              {
                name: 'RAC_SHIM_METRICS_ENABLED'
                value: string(metricsEnabled)
              }
            ],
            // Only include OTLP endpoint when non-empty so we don't pass an empty
            // string that triggers misconfigured exporter startup errors.
            empty(otlpEndpoint) ? [] : [
              {
                name: 'RAC_SHIM_OTLP_ENDPOINT'
                value: otlpEndpoint
              }
            ]
          )
        }
      ]
      // min-replicas=1: the shim is the entry point and must always be warm.
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the shim Container App')
output shimAppId string = shimApp.id

@description('Internal FQDN of the shim (used as App Gateway backend target)')
output shimFqdn string = shimApp.properties.configuration.ingress.fqdn
