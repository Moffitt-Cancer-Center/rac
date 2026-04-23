// pattern: Imperative Shell

import {
  PublicClientApplication,
  InteractionRequiredAuthError,
} from '@azure/msal-browser';

const tenantId = import.meta.env.VITE_TENANT_ID;
const clientId = import.meta.env.VITE_FRONTEND_CLIENT_ID;

if (!tenantId || !clientId) {
  throw new Error('Missing required env vars: VITE_TENANT_ID, VITE_FRONTEND_CLIENT_ID');
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
    scopes: ['api://rac-control-plane/submit'],
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
