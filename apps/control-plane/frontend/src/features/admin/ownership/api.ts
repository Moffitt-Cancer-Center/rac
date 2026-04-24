// pattern: Imperative Shell
/**
 * API client for admin ownership endpoints.
 *
 * - GET  /api/admin/ownership/flags
 * - POST /api/admin/apps/{appId}/ownership/transfer
 */

import { acquireApiToken } from '@/lib/msal';

const apiBase = import.meta.env.VITE_API_BASE_URL || window.location.origin + '/api';

// ─── Types ─────────────────────────────────────────────────────────────────────

export interface OwnershipFlag {
  flag_id: string;
  app_id: string;
  app_slug: string;
  pi_principal_id: string;
  pi_display_name: string | null;
  reason: 'account_disabled' | 'not_found';
  flagged_at: string;
}

export interface TransferOwnershipRequest {
  new_pi_principal_id: string;
  new_dept_fallback: string;
  justification: string;
}

export interface TransferOwnershipResponse {
  id: string;
  slug: string;
  pi_principal_id: string;
  dept_fallback: string;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

async function authHeaders(): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
}

// ─── API functions ─────────────────────────────────────────────────────────────

/**
 * List open ownership flags (no review row).
 */
export async function listFlags(): Promise<OwnershipFlag[]> {
  const headers = await authHeaders();
  const resp = await fetch(`${apiBase}/admin/ownership/flags`, { headers });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message ?? `HTTP ${resp.status}`);
  }

  return resp.json() as Promise<OwnershipFlag[]>;
}

/**
 * Transfer ownership of an app to a new PI.
 */
export async function transferOwnership(
  appId: string,
  request: TransferOwnershipRequest,
  idempotencyKey: string,
): Promise<TransferOwnershipResponse> {
  const headers = await authHeaders();
  const resp = await fetch(`${apiBase}/admin/apps/${appId}/ownership/transfer`, {
    method: 'POST',
    headers: {
      ...headers,
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify(request),
  });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message ?? `HTTP ${resp.status}`);
  }

  return resp.json() as Promise<TransferOwnershipResponse>;
}
