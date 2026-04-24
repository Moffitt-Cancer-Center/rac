// pattern: Functional Core — static display component; no I/O.

export function AssetTileSharedReference() {
  return (
    <div
      className="rounded border border-gray-300 bg-gray-50 p-4 opacity-50"
      data-testid="asset-tile-shared-reference"
      title="Coming in v2. See our docs for the preview."
    >
      <h3 className="font-bold text-gray-500">Shared Reference</h3>
      <p className="text-sm text-gray-500 mt-1">
        Coming in v2. See our docs for the preview.
      </p>
      <button
        type="button"
        disabled
        aria-disabled="true"
        className="mt-3 px-4 py-2 text-sm rounded bg-gray-300 text-gray-500 cursor-not-allowed"
        title="Coming in v2"
      >
        Add Shared Reference
      </button>
    </div>
  );
}
