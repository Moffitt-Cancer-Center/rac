// pattern: Imperative Shell — API client for access log viewer.
import { acquireApiToken } from '@/lib/msal';
import { accessLogListResponseSchema } from './types';
import type { AccessLogListResponse } from './types';

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

async function authHeaders(): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'X-Request-Id': crypto.randomUUID(),
  };
}

export type AccessLogParams = {
  before?: string | null;
  limit?: number;
  mode?: string | null;
  jti?: string | null;
  status?: number | null;
};

/**
 * GET /apps/{appId}/access-log — paginated, filterable access log.
 */
export async function listAccessLog(
  appId: string,
  params: AccessLogParams = {},
): Promise<AccessLogListResponse> {
  const headers = await authHeaders();
  const qs = new URLSearchParams();
  if (params.before) qs.set('before', params.before);
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  if (params.mode) qs.set('mode', params.mode);
  if (params.jti) qs.set('jti', params.jti);
  if (params.status !== null && params.status !== undefined) {
    qs.set('status', String(params.status));
  }
  const url = `${apiBase}/apps/${appId}/access-log${qs.toString() ? '?' + qs.toString() : ''}`;
  const resp = await fetch(url, { headers });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error(String(body['message'] ?? `HTTP ${resp.status}`));
  }
  const raw = await resp.json();
  const camel = convertKeysToCamel(raw);
  return accessLogListResponseSchema.parse(camel);
}
