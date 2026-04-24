// pattern: Functional Core — pure form render; submit handler is the shell boundary.
import { useState } from 'react';
import type { ExternalUrlAssetInput } from './types';
import { externalUrlAssetSchema } from './types';

interface Props {
  onAssetReady: (asset: ExternalUrlAssetInput) => void;
}

export function AssetTileExternalUrl({ onAssetReady }: Props) {
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [sha256, setSha256] = useState('');
  const [mountPath, setMountPath] = useState('/mnt/');
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  // Validate sha256 length for live feedback: must be exactly 64 hex chars
  const sha256Valid = /^[0-9a-fA-F]{64}$/.test(sha256);
  const sha256Length = sha256.length;
  const sha256TooShort = sha256Length > 0 && sha256Length < 64;
  const sha256TooLong = sha256Length > 64;

  const canSubmit =
    name.trim().length > 0 &&
    url.trim().length > 0 &&
    sha256Valid &&
    mountPath.startsWith('/');

  function handleAdd() {
    setValidationError(null);
    const result = externalUrlAssetSchema.safeParse({
      kind: 'external_url',
      name: name.trim(),
      url: url.trim(),
      sha256: sha256.trim(),
      mountPath: mountPath.trim(),
    });

    if (!result.success) {
      const firstError = result.error.errors[0];
      setValidationError(firstError?.message ?? 'Invalid input');
      return;
    }

    setSubmitted(true);
    onAssetReady(result.data);
  }

  return (
    <div
      className="rounded border border-gray-300 bg-white p-4 space-y-3"
      data-testid="asset-tile-external-url"
    >
      <h3 className="font-semibold text-gray-800">External URL Asset</h3>

      <div>
        <label htmlFor="ext-name" className="block text-sm font-medium text-gray-700">
          Asset Name
        </label>
        <input
          id="ext-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. hg38-reference"
          className="mt-1 block w-full rounded border border-gray-300 px-3 py-1.5 text-sm"
          disabled={submitted}
        />
      </div>

      <div>
        <label htmlFor="ext-url" className="block text-sm font-medium text-gray-700">
          URL
        </label>
        <input
          id="ext-url"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com/data/file.fa.gz"
          className="mt-1 block w-full rounded border border-gray-300 px-3 py-1.5 text-sm font-mono"
          disabled={submitted}
        />
      </div>

      <div>
        <label htmlFor="ext-sha256" className="block text-sm font-medium text-gray-700">
          Declared SHA-256{' '}
          <span className="text-gray-500 font-normal">(64 hex chars)</span>
        </label>
        <input
          id="ext-sha256"
          type="text"
          value={sha256}
          onChange={(e) => setSha256(e.target.value.toLowerCase().trim())}
          placeholder="e3b0c44298fc1c149afb…"
          className={`mt-1 block w-full rounded border px-3 py-1.5 text-sm font-mono ${
            sha256TooShort || sha256TooLong
              ? 'border-red-400 bg-red-50'
              : sha256Valid
                ? 'border-green-400'
                : 'border-gray-300'
          }`}
          disabled={submitted}
          maxLength={64}
        />
        {(sha256TooShort || sha256TooLong) && (
          <p
            data-testid="sha256-length-error"
            className="mt-1 text-xs text-red-600"
          >
            {sha256TooShort
              ? `${sha256Length}/64 characters — must be exactly 64 hex characters`
              : `${sha256Length}/64 characters — too long`}
          </p>
        )}
        {sha256Valid && (
          <p className="mt-1 text-xs text-green-600">64/64 — valid</p>
        )}
      </div>

      <div>
        <label htmlFor="ext-mount" className="block text-sm font-medium text-gray-700">
          Mount Path
        </label>
        <input
          id="ext-mount"
          type="text"
          value={mountPath}
          onChange={(e) => setMountPath(e.target.value)}
          placeholder="/mnt/ref/genome.fa.gz"
          className="mt-1 block w-full rounded border border-gray-300 px-3 py-1.5 text-sm font-mono"
          disabled={submitted}
        />
      </div>

      {validationError && (
        <p role="alert" className="text-sm text-red-700">
          {validationError}
        </p>
      )}

      {submitted ? (
        <p className="text-sm text-green-700">Asset added. Backend will fetch and verify on submission.</p>
      ) : (
        <button
          type="button"
          data-testid="add-external-url-btn"
          onClick={handleAdd}
          disabled={!canSubmit}
          className="px-4 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
        >
          Add Asset
        </button>
      )}
    </div>
  );
}
