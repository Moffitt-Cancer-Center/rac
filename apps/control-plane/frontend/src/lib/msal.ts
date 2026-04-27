// pattern: Imperative Shell

import {
  PublicClientApplication,
  InteractionRequiredAuthError,
} from '@azure/msal-browser';

const tenantId = import.meta.env.VITE_TENANT_ID;
const clientId = import.meta.env.VITE_FRONTEND_CLIENT_ID;
// API scope identifier. Tenants with strict policies (the default Entra rule
// "all newly added URIs must contain a tenant verified domain, tenant ID, or
// app ID") reject vanity URIs like `api://rac-control-plane`, so the API
// app reg's identifierUri is typically `api://<api-app-id>`. Pass either
// the full scope (e.g. `api://<guid>/submit`) via VITE_API_SCOPE, or just
// the API app's client ID via VITE_API_APP_ID and the canonical
// `api://<id>/submit` form is built here.
const apiScope = import.meta.env.VITE_API_SCOPE
  || (import.meta.env.VITE_API_APP_ID
    ? `api://${import.meta.env.VITE_API_APP_ID}/submit`
    : '');

if (!tenantId || !clientId || !apiScope) {
  throw new Error('Missing required env vars: VITE_TENANT_ID, VITE_FRONTEND_CLIENT_ID, and one of VITE_API_SCOPE or VITE_API_APP_ID');
}

export const msalInstance = new PublicClientApplication({
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin,
  },
});

/**
 * Acquires a Bearer token targeting the RAC API.
 * Attempts silent acquisition first, falls back to popup on InteractionRequiredAuthError.
 */
export async function acquireApiToken(): Promise<string> {
  const account = msalInstance.getAllAccounts()[0];

  const request = {
    scopes: [apiScope],
    account: account ?? undefined,
  };

  try {
    const result = await msalInstance.acquireTokenSilent(request);
    return result.accessToken;
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      const result = await msalInstance.acquireTokenPopup(request);
      return result.accessToken;
    }
    throw error;
  }
}
