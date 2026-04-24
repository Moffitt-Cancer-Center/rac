// pattern: Imperative Shell — API client for detection findings.

import { acquireApiToken } from '@/lib/msal';
import { findingsListSchema, findingDecisionSchema } from './types';
import type { FindingsList, FindingDecision, Decision } from './types';

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '/api';

// ─── Helpers ───────────────────────────────────────────────────────────────────

function resolveUrl(path: string): string {
  const base =
    apiBaseUrl.startsWith('http')
      ? apiBaseUrl
      : (typeof window !== 'undefined' ? window.location.origin : 'http://localhost') + apiBaseUrl;
  return `${base}${path}`;
}

/** Convert a single object's keys from snake_case to camelCase (shallow). */
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

async function authHeaders(extras: Record<string, string> = {}): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'X-Request-Id': crypto.randomUUID(),
    ...extras,
  };
}

// ─── API functions ─────────────────────────────────────────────────────────────

/**
 * GET /submissions/{submissionId}/findings
 * Returns list of findings with their latest decision joined.
 */
export async function listFindings(submissionId: string): Promise<FindingsList> {
  const headers = await authHeaders();
  const res = await fetch(resolveUrl(`/submissions/${submissionId}/findings`), { headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error(String(body['message'] ?? `Failed to load findings: ${res.status}`));
  }
  const raw = await res.json();
  const camel = convertKeysToCamel(raw);
  return findingsListSchema.parse(camel);
}

/**
 * POST /submissions/{submissionId}/findings/{findingId}/decisions
 * Records a decision (accept | override | auto_fix | dismiss) for a finding.
 *
 * An Idempotency-Key is required per the api.ts contract for all mutating calls.
 * The caller (NudgeCard / DecisionDialog) generates a per-intent UUID via
 * crypto.randomUUID() and passes it here so retries reuse the same key.
 */
export async function recordDecision(
  submissionId: string,
  findingId: string,
  decision: Decision,
  notes: string | undefined,
  idempotencyKey: string,
): Promise<FindingDecision> {
  const headers = await authHeaders({
    'Content-Type': 'application/json',
    'Idempotency-Key': idempotencyKey,
  });

  const body: Record<string, unknown> = { decision };
  if (notes !== undefined && notes.trim().length > 0) {
    body['notes'] = notes.trim();
  }

  const res = await fetch(
    resolveUrl(`/submissions/${submissionId}/findings/${findingId}/decisions`),
    { method: 'POST', headers, body: JSON.stringify(body) },
  );
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error(String(errBody['message'] ?? `Decision failed: ${res.status}`));
  }
  const raw = await res.json();
  const camel = convertKeysToCamel(raw);
  return findingDecisionSchema.parse(camel);
}
