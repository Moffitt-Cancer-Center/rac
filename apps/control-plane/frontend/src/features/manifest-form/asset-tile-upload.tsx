// pattern: Imperative Shell — orchestrates upload via fetch + XMLHttpRequest for progress.
import { useState, useRef } from 'react';
import { mintUploadSas, finalizeUpload } from './api';
import type { UploadAssetInput } from './types';

// ─── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Compute SHA-256 of a file using Web Crypto API.
 * Returns lowercase hex string.
 * Note: server re-hashes authoritatively; this is a UX cross-check only.
 */
export async function computeSha256(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const hashBuffer = await crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * PUT a file to an Azure Blob SAS URL using XMLHttpRequest for progress tracking.
 * Returns a Promise that resolves when the upload completes (HTTP 2xx).
 *
 * Exported for unit-test injection; callers should use the component's default.
 */
export function uploadToSasUrl(
  uploadUrl: string,
  file: File,
  onProgress: (pct: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        onProgress(Math.round((ev.loaded / ev.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Blob PUT failed: HTTP ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error('Network error during blob upload'));
    xhr.onabort = () => reject(new Error('Upload aborted'));

    xhr.open('PUT', uploadUrl);
    // Required for Azure Blob Storage SAS uploads
    xhr.setRequestHeader('x-ms-blob-type', 'BlockBlob');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.send(file);
  });
}

// ─── Component ─────────────────────────────────────────────────────────────────

type UploadStatus = 'idle' | 'hashing' | 'uploading' | 'finalizing' | 'ready' | 'error';

interface Props {
  submissionId: string;
  onAssetReady: (asset: UploadAssetInput) => void;
  /**
   * Injectable for testing: override sha256 computation.
   * Defaults to the real Web Crypto implementation.
   */
  _computeSha256?: (file: File) => Promise<string>;
  /**
   * Injectable for testing: override the SAS PUT step.
   * Defaults to the real XMLHttpRequest implementation.
   */
  _uploadToSasUrl?: (uploadUrl: string, file: File, onProgress: (pct: number) => void) => Promise<void>;
}

export function AssetTileUpload({
  submissionId,
  onAssetReady,
  _computeSha256 = computeSha256,
  _uploadToSasUrl = uploadToSasUrl,
}: Props) {
  const [name, setName] = useState('');
  const [mountPath, setMountPath] = useState('/mnt/');
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState<UploadStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const idempotencyKey = useRef(crypto.randomUUID());

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0] ?? null;
    setFile(selected);
    setStatus('idle');
    setError(null);
    setProgress(0);
    // Regenerate idempotency key on new file selection
    idempotencyKey.current = crypto.randomUUID();
  }

  async function handleUpload() {
    if (!file || !name.trim() || !mountPath.trim()) {
      setError('Name, mount path, and file are all required.');
      return;
    }

    setError(null);
    setProgress(0);

    try {
      // Step 1: Hash the file (client-side cross-check; server re-hashes)
      setStatus('hashing');
      const declaredSha256 = await _computeSha256(file);

      // Step 2: Mint SAS
      setStatus('uploading');
      const sas = await mintUploadSas(
        submissionId,
        name.trim(),
        mountPath.trim(),
        file.size,
      );

      // Step 3: PUT file directly to Blob Storage
      await _uploadToSasUrl(sas.upload_url, file, setProgress);
      setProgress(100);

      // Step 4: Finalize on the server
      setStatus('finalizing');
      await finalizeUpload(submissionId, {
        name: name.trim(),
        blob_path: sas.blob_path,
        declared_sha256: declaredSha256,
        declared_size_bytes: file.size,
        mount_path: mountPath.trim(),
      });

      // Step 5: Notify parent
      setStatus('ready');
      onAssetReady({ kind: 'upload', name: name.trim(), mountPath: mountPath.trim() });
    } catch (err: unknown) {
      setStatus('error');
      const e = err as { code?: string; message?: string };
      if (e.code === 'sha256_mismatch') {
        setError('SHA-256 mismatch: the uploaded file did not match the expected hash. Please try again.');
      } else {
        setError(e.message ?? 'Upload failed. Please try again.');
      }
    }
  }

  const isProcessing =
    status === 'hashing' || status === 'uploading' || status === 'finalizing';

  const statusLabel: Record<UploadStatus, string> = {
    idle: '',
    hashing: 'Computing checksum…',
    uploading: `Uploading… ${progress}%`,
    finalizing: 'Verifying…',
    ready: 'Upload complete',
    error: '',
  };

  return (
    <div
      className="rounded border border-gray-300 bg-white p-4 space-y-3"
      data-testid="asset-tile-upload"
    >
      <h3 className="font-semibold text-gray-800">Upload Asset</h3>

      <div>
        <label htmlFor={`upload-name-${idempotencyKey.current}`} className="block text-sm font-medium text-gray-700">
          Asset Name
        </label>
        <input
          id={`upload-name-${idempotencyKey.current}`}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. reference-genome"
          className="mt-1 block w-full rounded border border-gray-300 px-3 py-1.5 text-sm"
          disabled={isProcessing || status === 'ready'}
        />
      </div>

      <div>
        <label htmlFor={`upload-mount-${idempotencyKey.current}`} className="block text-sm font-medium text-gray-700">
          Mount Path
        </label>
        <input
          id={`upload-mount-${idempotencyKey.current}`}
          type="text"
          value={mountPath}
          onChange={(e) => setMountPath(e.target.value)}
          placeholder="/mnt/data/file.fa"
          className="mt-1 block w-full rounded border border-gray-300 px-3 py-1.5 text-sm font-mono"
          disabled={isProcessing || status === 'ready'}
        />
      </div>

      <div>
        <label htmlFor={`upload-file-${idempotencyKey.current}`} className="block text-sm font-medium text-gray-700">
          File
        </label>
        <input
          id={`upload-file-${idempotencyKey.current}`}
          data-testid="upload-file-input"
          type="file"
          onChange={handleFileChange}
          className="mt-1 block w-full text-sm text-gray-700"
          disabled={isProcessing || status === 'ready'}
        />
        {file && (
          <p className="mt-1 text-xs text-gray-500">
            {file.name} ({(file.size / 1024).toFixed(1)} KB)
          </p>
        )}
      </div>

      {/* Progress bar */}
      {(status === 'uploading') && (
        <div
          role="progressbar"
          aria-valuenow={progress}
          aria-valuemin={0}
          aria-valuemax={100}
          className="w-full bg-gray-200 rounded-full h-2"
        >
          <div
            className="bg-blue-600 h-2 rounded-full transition-all"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      {/* Status text */}
      {statusLabel[status] && (
        <p
          data-testid="upload-status"
          className={`text-sm ${status === 'ready' ? 'text-green-700' : 'text-gray-600'}`}
        >
          {statusLabel[status]}
        </p>
      )}

      {/* Error */}
      {error && (
        <p
          role="alert"
          data-testid="upload-error"
          className="text-sm text-red-700"
        >
          {error}
        </p>
      )}

      <button
        type="button"
        onClick={() => { void handleUpload(); }}
        disabled={isProcessing || status === 'ready' || !file}
        className="px-4 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
      >
        {isProcessing ? statusLabel[status] : status === 'ready' ? 'Uploaded' : 'Upload'}
      </button>
    </div>
  );
}
