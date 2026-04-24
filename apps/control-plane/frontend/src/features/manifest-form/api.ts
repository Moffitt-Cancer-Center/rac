// pattern: Imperative Shell — network I/O against backend asset endpoints.
import { acquireApiToken } from '@/lib/msal';

const apiBase = import.meta.env.VITE_API_BASE_URL || '/api';

// ─── Types ─────────────────────────────────────────────────────────────────────

export interface SasCredentials {
  upload_url: string;
  blob_path: string;
  expires_at: string;
  max_size_bytes: number;
}

export interface FinalizeUploadBody {
  name: string;
  blob_path: string;
  declared_sha256: string;
  declared_size_bytes?: number;
  mount_path: string;
}

export interface AssetRecord {
  id: string;
  submission_id: string;
  name: string;
  kind: 'upload' | 'external_url' | 'shared_reference';
  mount_path: string;
  blob_uri?: string | null;
  sha256?: string | null;
  size_bytes?: number | null;
  status: 'ready' | 'hash_mismatch' | 'pending' | 'unreachable';
  expected_sha256?: string | null;
  actual_sha256?: string | null;
  url?: string | null;
  created_at: string;
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
 * Mint a SAS token for a direct-to-Blob upload.
 */
export async function mintUploadSas(
  submissionId: string,
  name: string,
  mountPath: string,
  maxSizeBytes: number,
): Promise<SasCredentials> {
  const headers = await authHeaders();
  const idempotencyKey = crypto.randomUUID();
  const resp = await fetch(
    `${apiBase}/submissions/${submissionId}/assets/uploads/sas`,
    {
      method: 'POST',
      headers: { ...headers, 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({
        name,
        mount_path: mountPath,
        max_size_bytes: maxSizeBytes,
      }),
    },
  );

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({})) as { message?: string };
    throw new Error(body.message ?? `HTTP ${resp.status}`);
  }

  return resp.json() as Promise<SasCredentials>;
}

/**
 * Notify the server that a direct-to-Blob upload has completed.
 * Server re-hashes the blob and inserts the asset row.
 */
export async function finalizeUpload(
  submissionId: string,
  body: FinalizeUploadBody,
): Promise<AssetRecord> {
  const headers = await authHeaders();
  const idempotencyKey = crypto.randomUUID();
  const resp = await fetch(
    `${apiBase}/submissions/${submissionId}/assets/uploads/finalize`,
    {
      method: 'POST',
      headers: { ...headers, 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(body),
    },
  );

  if (!resp.ok) {
    const errBody = await resp.json().catch(() => ({})) as {
      message?: string;
      code?: string;
    };
    const err = new Error(errBody.message ?? `HTTP ${resp.status}`) as Error & {
      status: number;
      code?: string;
    };
    err.status = resp.status;
    err.code = errBody.code;
    throw err;
  }

  return resp.json() as Promise<AssetRecord>;
}

/**
 * List all assets for a submission.
 */
export async function listAssets(submissionId: string): Promise<AssetRecord[]> {
  const headers = await authHeaders();
  const resp = await fetch(
    `${apiBase}/submissions/${submissionId}/assets`,
    { headers },
  );

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({})) as { message?: string };
    throw new Error(body.message ?? `HTTP ${resp.status}`);
  }

  const data = await resp.json() as AssetRecord[] | { items: AssetRecord[] };
  return Array.isArray(data) ? data : data.items;
}
