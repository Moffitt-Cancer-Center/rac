// pattern: Functional Core — typed API client for webhook subscription CRUD.

import { acquireApiToken } from './msal';

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '/api';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface WebhookSubscriptionResponse {
  id: string;
  name: string;
  callbackUrl: string;
  eventTypes: string[];
  enabled: boolean;
  consecutiveFailures: number;
  lastDeliveryAt: string | null;
  secretRotatedAt: string | null;
  updatedAt: string;
}

export interface WebhookSubscriptionCreateResponse extends WebhookSubscriptionResponse {
  /** One-shot HMAC secret — returned only on creation, never again. */
  secret: string;
}

export interface WebhookSubscriptionCreate {
  name: string;
  callbackUrl: string;
  eventTypes: string[];
}

export interface WebhookSubscriptionUpdate {
  name?: string;
  callbackUrl?: string;
  eventTypes?: string[];
  enabled?: boolean;
  resetConsecutiveFailures?: boolean;
}

// ─── snake_case <-> camelCase conversion ─────────────────────────────────────

function toCamel(s: string): string {
  return s.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
}

function convertKeysToCamel(obj: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    result[toCamel(k)] = v;
  }
  return result;
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

async function authHeaders(extras: Record<string, string> = {}): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'X-Request-Id': crypto.randomUUID(),
    ...extras,
  };
}

function adminUrl(path: string): string {
  // Use window.location.origin as base when apiBaseUrl is relative (dev/test);
  // in production VITE_API_BASE_URL is an absolute URL.
  const base =
    apiBaseUrl.startsWith('http')
      ? apiBaseUrl
      : (typeof window !== 'undefined' ? window.location.origin : 'http://localhost') + apiBaseUrl;
  return `${base}/admin${path}`;
}

// ─── API functions ────────────────────────────────────────────────────────────

export async function listWebhookSubscriptions(): Promise<WebhookSubscriptionResponse[]> {
  const headers = await authHeaders();
  const res = await fetch(adminUrl('/webhook-subscriptions'), { headers });
  if (!res.ok) {
    throw new Error(`Failed to list webhook subscriptions: ${res.status}`);
  }
  const body = (await res.json()) as unknown[];
  return body.map((item) =>
    convertKeysToCamel(item as Record<string, unknown>) as unknown as WebhookSubscriptionResponse
  );
}

export async function getWebhookSubscription(id: string): Promise<WebhookSubscriptionResponse> {
  const headers = await authHeaders();
  const res = await fetch(adminUrl(`/webhook-subscriptions/${id}`), { headers });
  if (!res.ok) {
    throw new Error(`Failed to get webhook subscription: ${res.status}`);
  }
  const body = (await res.json()) as Record<string, unknown>;
  return convertKeysToCamel(body) as unknown as WebhookSubscriptionResponse;
}

export async function createWebhookSubscription(
  data: WebhookSubscriptionCreate,
  options: { idempotencyKey: string }
): Promise<WebhookSubscriptionCreateResponse> {
  const headers = await authHeaders({
    'Content-Type': 'application/json',
    'Idempotency-Key': options.idempotencyKey,
  });
  const snakeBody: Record<string, unknown> = {
    name: data.name,
    callback_url: data.callbackUrl,
    event_types: data.eventTypes,
  };

  const res = await fetch(adminUrl('/webhook-subscriptions'), {
    method: 'POST',
    headers,
    body: JSON.stringify(snakeBody),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    throw new Error(String(err['message'] ?? `Creation failed: ${res.status}`));
  }
  const resp = (await res.json()) as Record<string, unknown>;
  return convertKeysToCamel(resp) as unknown as WebhookSubscriptionCreateResponse;
}

export async function updateWebhookSubscription(
  id: string,
  update: WebhookSubscriptionUpdate,
  options: { idempotencyKey: string }
): Promise<WebhookSubscriptionResponse> {
  const headers = await authHeaders({
    'Content-Type': 'application/json',
    'Idempotency-Key': options.idempotencyKey,
  });

  // Build snake_case patch body manually to avoid the array/bool issues
  const snakeBody: Record<string, unknown> = {};
  if (update.name !== undefined) snakeBody['name'] = update.name;
  if (update.callbackUrl !== undefined) snakeBody['callback_url'] = update.callbackUrl;
  if (update.eventTypes !== undefined) snakeBody['event_types'] = update.eventTypes;
  if (update.enabled !== undefined) snakeBody['enabled'] = update.enabled;
  if (update.resetConsecutiveFailures !== undefined)
    snakeBody['reset_consecutive_failures'] = update.resetConsecutiveFailures;

  const res = await fetch(adminUrl(`/webhook-subscriptions/${id}`), {
    method: 'PATCH',
    headers,
    body: JSON.stringify(snakeBody),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    throw new Error(String(err['message'] ?? `Update failed: ${res.status}`));
  }
  const resp = (await res.json()) as Record<string, unknown>;
  return convertKeysToCamel(resp) as unknown as WebhookSubscriptionResponse;
}

export async function deleteWebhookSubscription(
  id: string,
  options: { idempotencyKey: string }
): Promise<void> {
  const headers = await authHeaders({
    'Idempotency-Key': options.idempotencyKey,
  });
  const res = await fetch(adminUrl(`/webhook-subscriptions/${id}`), {
    method: 'DELETE',
    headers,
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`Delete failed: ${res.status}`);
  }
}
