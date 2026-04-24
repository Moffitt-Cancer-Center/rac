// pattern: Imperative Shell
/**
 * API client for admin provisioning endpoints.
 *
 * - GET /api/admin/submissions/failed-provisions
 * - POST /api/admin/submissions/{id}/provisioning/retry
 */

import { acquireApiToken } from '@/lib/msal';

const apiBase = import.meta.env.VITE_API_BASE_URL || window.location.origin + '/api';

export interface FailedProvisionRow {
  submission_id: string;
  slug: string;
  pi_principal_id: string;
  last_failure_reason: string;
  failed_at: string;
  retry_count: number;
}

export interface RetryOutcomeResponse {
  submission_id: string;
  success: boolean;
  error_code: string | null;
  error_detail: string | null;
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
}

/**
 * Fetch the list of failed provisions awaiting retry.
 */
export async function listFailedProvisions(): Promise<FailedProvisionRow[]> {
  const headers = await authHeaders();
  const resp = await fetch(`${apiBase}/admin/submissions/failed-provisions`, {
    headers,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message ?? `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<FailedProvisionRow[]>;
}

/**
 * Retry provisioning for a specific submission.
 */
export async function retryProvisioning(
  submissionId: string,
  idempotencyKey: string,
): Promise<RetryOutcomeResponse> {
  const headers = await authHeaders();
  const resp = await fetch(
    `${apiBase}/admin/submissions/${submissionId}/provisioning/retry`,
    {
      method: 'POST',
      headers: {
        ...headers,
        'Idempotency-Key': idempotencyKey,
      },
    },
  );
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message ?? `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<RetryOutcomeResponse>;
}
