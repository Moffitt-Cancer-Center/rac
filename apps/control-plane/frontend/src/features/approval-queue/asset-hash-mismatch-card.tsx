// pattern: Functional Core — pure display component; no I/O.
/**
 * AssetHashMismatchCard: shown in the IT approval view when an asset
 * has status='hash_mismatch' (AC8.3).
 *
 * Displays:
 * - Asset name
 * - Expected sha256 (declared by researcher)
 * - Actual sha256 (computed by server after fetch)
 * - Source URL (the external URL)
 * - Retry button (admin only — wired up in future improvement; currently disabled)
 */

interface AssetHashMismatchCardProps {
  /** The asset's logical name. */
  assetName: string;
  /** The sha256 declared by the researcher in the manifest. */
  expectedSha256: string;
  /** The sha256 computed by the server when it fetched the URL. */
  actualSha256: string;
  /** The external URL the asset was fetched from. */
  sourceUrl: string;
  /** If true, show the retry button (admin role). */
  isAdmin?: boolean;
  /** Called when the admin clicks Retry. No-op until the retry endpoint is wired. */
  onRetry?: () => void;
}

export function AssetHashMismatchCard({
  assetName,
  expectedSha256,
  actualSha256,
  sourceUrl,
  isAdmin = false,
  onRetry,
}: AssetHashMismatchCardProps) {
  return (
    <div
      role="region"
      aria-label={`Hash mismatch for asset ${assetName}`}
      data-testid="asset-hash-mismatch-card"
      className="rounded border border-red-300 bg-red-50 p-4 space-y-3"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-red-900">
            SHA-256 Mismatch — {assetName}
          </h3>
          <p className="text-sm text-red-700 mt-0.5">
            The downloaded file does not match the declared checksum.
          </p>
        </div>
        <span
          aria-label="hash mismatch badge"
          className="flex-shrink-0 px-2 py-0.5 rounded-full bg-red-200 text-red-800 text-xs font-medium"
        >
          hash_mismatch
        </span>
      </div>

      {/* Hash comparison */}
      <dl className="space-y-2 text-sm">
        <div>
          <dt className="font-medium text-gray-700">Expected (declared)</dt>
          <dd
            data-testid="expected-sha256"
            className="font-mono text-xs text-gray-900 break-all mt-0.5 bg-white px-2 py-1 rounded border border-gray-200"
          >
            {expectedSha256}
          </dd>
        </div>
        <div>
          <dt className="font-medium text-gray-700">Actual (computed)</dt>
          <dd
            data-testid="actual-sha256"
            className="font-mono text-xs text-red-900 break-all mt-0.5 bg-white px-2 py-1 rounded border border-red-200"
          >
            {actualSha256}
          </dd>
        </div>
        <div>
          <dt className="font-medium text-gray-700">Source URL</dt>
          <dd className="mt-0.5">
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline break-all text-xs font-mono"
            >
              {sourceUrl}
            </a>
          </dd>
        </div>
      </dl>

      {/* Retry (admin only) */}
      {isAdmin && (
        <div className="pt-1">
          <button
            type="button"
            onClick={onRetry}
            className="px-3 py-1.5 text-sm rounded bg-red-600 text-white hover:bg-red-700 disabled:bg-gray-400"
          >
            Retry Fetch
          </button>
          <p className="text-xs text-gray-500 mt-1">
            Re-fetches the URL and re-verifies the hash.
          </p>
        </div>
      )}
    </div>
  );
}
