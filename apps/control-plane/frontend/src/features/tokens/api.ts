// pattern: Imperative Shell — API client for reviewer token endpoints.
import { acquireApiToken } from '@/lib/msal';
import {
  tokenListResponseSchema,
  tokenCreateResponseSchema,
} from './types';
import type { TokenListResponse, TokenCreateResponse } from './types';

const apiBase = import.meta.env.VITE_API_BASE_URL || window.location.origin + '/api';

function toCamel(s: string): string {
  return s.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
}

function convertKeysToCamel(obj: unknown): unknown {
  if (Array.isArray(obj)) {
    return obj.map(convertKeysToCamel);
  }
  if (obj !== null && typeof obj === 'object') {
    const result: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      result[toCamel(k)] = convertKeysToCamel(v);
    }
    return result;
  }
  return obj;
}

async function authHeaders(
  extras: Record<string, string> = {},
): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'X-Request-Id': crypto.randomUUID(),
    ...extras,
  };
}

/**
 * POST /apps/{appId}/tokens — mint a reviewer token.
 *
 * The JWT is returned ONCE in this response. Callers must copy the visit_url
 * immediately; the raw JWT cannot be retrieved again.
 */
export async function mintToken(
  appId: string,
  params: { reviewerLabel: string; ttlDays: number },
  idempotencyKey: string,
): Promise<TokenCreateResponse> {
  const headers = await authHeaders({
    'Content-Type': 'application/json',
    'Idempotency-Key': idempotencyKey,
  });
  const resp = await fetch(`${apiBase}/apps/${appId}/tokens`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      reviewer_label: params.reviewerLabel,
      ttl_days: params.ttlDays,
    }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error(String(body['message'] ?? `HTTP ${resp.status}`));
  }
  const raw = await resp.json();
  const camel = convertKeysToCamel(raw);
  return tokenCreateResponseSchema.parse(camel);
}

/**
 * GET /apps/{appId}/tokens — list reviewer tokens (without JWT).
 */
export async function listTokens(
  appId: string,
  includeRevoked = false,
): Promise<TokenListResponse> {
  const headers = await authHeaders();
  const qs = includeRevoked ? '?include_revoked=true' : '';
  const resp = await fetch(`${apiBase}/apps/${appId}/tokens${qs}`, { headers });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error(String(body['message'] ?? `HTTP ${resp.status}`));
  }
  const raw = await resp.json();
  const camel = convertKeysToCamel(raw);
  return tokenListResponseSchema.parse(camel);
}

/**
 * DELETE /apps/{appId}/tokens/{jti} — revoke a reviewer token.
 */
export async function revokeToken(
  appId: string,
  jti: string,
  idempotencyKey: string,
): Promise<void> {
  const headers = await authHeaders({ 'Idempotency-Key': idempotencyKey });
  const resp = await fetch(`${apiBase}/apps/${appId}/tokens/${jti}`, {
    method: 'DELETE',
    headers,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error(String(body['message'] ?? `HTTP ${resp.status}`));
  }
}
