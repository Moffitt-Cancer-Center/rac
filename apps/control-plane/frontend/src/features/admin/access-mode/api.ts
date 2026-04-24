// pattern: Imperative Shell — API client for access-mode toggle endpoint.
import { acquireApiToken } from '@/lib/msal';

const apiBase = import.meta.env.VITE_API_BASE_URL || window.location.origin + '/api';

export type AccessModeRequest = {
  mode: 'token_required' | 'public';
  notes: string;
};

export type AccessModeResponse = {
  appId: string;
  accessMode: string;
  slug: string;
};

async function authHeaders(
  extras: Record<string, string> = {},
): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    'X-Request-Id': crypto.randomUUID(),
    ...extras,
  };
}

/**
 * POST /apps/{appId}/access-mode — set the access mode.
 */
export async function setAccessMode(
  appId: string,
  req: AccessModeRequest,
  idempotencyKey: string,
): Promise<AccessModeResponse> {
  const headers = await authHeaders({ 'Idempotency-Key': idempotencyKey });
  const resp = await fetch(`${apiBase}/apps/${appId}/access-mode`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ mode: req.mode, notes: req.notes }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error(String(body['message'] ?? `HTTP ${resp.status}`));
  }
  const data = await resp.json() as { app_id: string; access_mode: string; slug: string };
  return {
    appId: data.app_id,
    accessMode: data.access_mode,
    slug: data.slug,
  };
}
