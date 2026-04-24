// pattern: Imperative Shell
/**
 * API client for approval queue endpoints.
 *
 * - GET  /api/submissions?status_filter=<status>
 * - POST /api/submissions/{id}/approvals/{stage}
 */

import { acquireApiToken } from '@/lib/msal';

const apiBase = import.meta.env.VITE_API_BASE_URL || window.location.origin + '/api';

// ─── Types ─────────────────────────────────────────────────────────────────────

export type ApprovalStage = 'research' | 'it';
export type ApprovalDecision = 'approve' | 'reject' | 'request_changes';

export interface SubmissionSummary {
  id: string;
  slug: string;
  status: string;
  submitter_principal_id: string;
  github_repo_url: string;
  git_ref: string;
  dockerfile_path: string;
  pi_principal_id: string;
  dept_fallback: string;
  created_at: string;
  updated_at: string;
}

export interface ScanResultSummary {
  verdict: string;
  effective_severity: string;
  findings: unknown[];
  build_log_uri?: string | null;
  defender_timed_out?: boolean;
  image_digest?: string | null;
}

export interface ApprovalRequest {
  decision: ApprovalDecision;
  notes?: string;
}

export interface ApprovalResponse {
  id: string;
  slug: string;
  status: string;
  submitter_principal_id: string;
  github_repo_url: string;
  git_ref: string;
  dockerfile_path: string;
  pi_principal_id: string;
  dept_fallback: string;
  created_at: string;
  updated_at: string;
}

export interface ApiError {
  code: string;
  message: string;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

async function authHeaders(): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
}

// ─── Submission listing ────────────────────────────────────────────────────────

/**
 * List submissions filtered by status_filter.
 */
export async function listSubmissionsByStatus(
  statusFilter: string,
): Promise<SubmissionSummary[]> {
  const headers = await authHeaders();
  const params = new URLSearchParams({ status: statusFilter });
  const resp = await fetch(`${apiBase}/submissions?${params}`, { headers });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as ApiError).message ?? `HTTP ${resp.status}`);
  }

  const data = await resp.json();
  // API returns { items: [...], total: N }
  return (data.items ?? data) as SubmissionSummary[];
}

/**
 * Get a single submission with scan result details.
 */
export async function getSubmissionDetail(
  submissionId: string,
): Promise<SubmissionSummary> {
  const headers = await authHeaders();
  const resp = await fetch(`${apiBase}/submissions/${submissionId}`, {
    headers,
  });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as ApiError).message ?? `HTTP ${resp.status}`);
  }

  return resp.json() as Promise<SubmissionSummary>;
}

/**
 * Get scan result for a submission.
 */
export async function getScanResult(
  submissionId: string,
): Promise<ScanResultSummary | null> {
  const headers = await authHeaders();
  const resp = await fetch(`${apiBase}/submissions/${submissionId}/scan-result`, {
    headers,
  });

  if (resp.status === 404) return null;
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as ApiError).message ?? `HTTP ${resp.status}`);
  }

  return resp.json() as Promise<ScanResultSummary>;
}

// ─── Approval actions ──────────────────────────────────────────────────────────

/**
 * Submit an approval decision for a submission stage.
 *
 * @param submissionId - UUID of the submission
 * @param stage - 'research' or 'it'
 * @param request - { decision, notes? }
 * @param idempotencyKey - per-intent idempotency key (caller generates at intent boundary)
 */
export async function postApproval(
  submissionId: string,
  stage: ApprovalStage,
  request: ApprovalRequest,
  idempotencyKey: string,
): Promise<ApprovalResponse> {
  const headers = await authHeaders();
  const resp = await fetch(
    `${apiBase}/submissions/${submissionId}/approvals/${stage}`,
    {
      method: 'POST',
      headers: {
        ...headers,
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify({
        decision: request.decision,
        notes: request.notes ?? null,
      }),
    },
  );

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    const err = new Error(
      (body as ApiError).message ?? `HTTP ${resp.status}`,
    ) as Error & { status: number; apiError: ApiError };
    err.status = resp.status;
    err.apiError = body as ApiError;
    throw err;
  }

  return resp.json() as Promise<ApprovalResponse>;
}
