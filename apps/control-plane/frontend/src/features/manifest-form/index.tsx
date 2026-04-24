// pattern: Functional Core (form structure + validation); submit handler is the shell boundary.
import { useState, useRef } from 'react';
import { formManifestSchema } from './types';
import type { FormManifest, Asset } from './types';
import { AssetTileUpload } from './asset-tile-upload';
import { AssetTileExternalUrl } from './asset-tile-external-url';
import { AssetTileSharedReference } from './asset-tile-shared-reference';

// ─── Types ─────────────────────────────────────────────────────────────────────

type AddAssetMode = 'upload' | 'external_url' | null;

interface EnvVar {
  key: string;
  value: string;
}

interface Props {
  submissionId: string;
  initial?: Partial<FormManifest>;
  onSubmit: (manifest: FormManifest) => Promise<void>;
}

// ─── Constants ─────────────────────────────────────────────────────────────────

const CPU_OPTIONS = [0.25, 0.5, 1.0, 2.0] as const;
const MEMORY_OPTIONS = [0.5, 1.0, 2.0, 4.0, 8.0] as const;

// ─── Component ─────────────────────────────────────────────────────────────────

export function ManifestForm({ submissionId, initial, onSubmit }: Props) {
  const [targetPort, setTargetPort] = useState<number>(
    initial?.targetPort ?? 8080,
  );
  const [cpuCores, setCpuCores] = useState<number>(
    initial?.cpuCores ?? 0.5,
  );
  const [memoryGb, setMemoryGb] = useState<number>(
    initial?.memoryGb ?? 1.0,
  );
  const [envVars, setEnvVars] = useState<EnvVar[]>(
    Object.entries(initial?.envVars ?? {}).map(([key, value]) => ({
      key,
      value,
    })),
  );
  const [assets, setAssets] = useState<Asset[]>(initial?.assets ?? []);
  const [addMode, setAddMode] = useState<AddAssetMode>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // ─── Env var helpers ──────────────────────────────────────────────────────

  function addEnvVar() {
    setEnvVars((prev) => [...prev, { key: '', value: '' }]);
  }

  function removeEnvVar(index: number) {
    setEnvVars((prev) => prev.filter((_, i) => i !== index));
  }

  function updateEnvVar(index: number, field: 'key' | 'value', val: string) {
    setEnvVars((prev) =>
      prev.map((ev, i) => (i === index ? { ...ev, [field]: val } : ev)),
    );
  }

  // ─── Asset helpers ────────────────────────────────────────────────────────

  function handleAssetReady(asset: Asset) {
    setAssets((prev) => [...prev, asset]);
    setAddMode(null);
  }

  // ─── Submit ───────────────────────────────────────────────────────────────

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSubmitError(null);

    // Build envVars record (skip blank keys)
    const envVarsRecord: Record<string, string> = {};
    for (const ev of envVars) {
      if (ev.key.trim()) {
        envVarsRecord[ev.key.trim()] = ev.value;
      }
    }

    const raw = {
      targetPort,
      cpuCores,
      memoryGb,
      envVars: envVarsRecord,
      assets,
    };

    const parsed = formManifestSchema.safeParse(raw);
    if (!parsed.success) {
      const firstError = parsed.error.errors[0];
      setSubmitError(firstError?.message ?? 'Invalid form data');
      return;
    }

    setIsSubmitting(true);
    try {
      await onSubmit(parsed.data);
    } catch (err: unknown) {
      const e = err as { message?: string };
      setSubmitError(e.message ?? 'Submission failed');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form onSubmit={(e) => { void handleSubmit(e); }} className="space-y-8">
      {/* ── Resources section ── */}
      <section aria-label="Resources">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Resources</h2>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label htmlFor="target-port" className="block text-sm font-medium text-gray-700">
              Target Port
            </label>
            <input
              id="target-port"
              type="number"
              min={1}
              max={65535}
              value={targetPort}
              onChange={(e) => setTargetPort(Number(e.target.value))}
              className="mt-1 block w-full rounded border border-gray-300 px-3 py-1.5 text-sm"
            />
          </div>

          <div>
            <label htmlFor="cpu-cores" className="block text-sm font-medium text-gray-700">
              CPU Cores
            </label>
            <select
              id="cpu-cores"
              value={cpuCores}
              onChange={(e) => setCpuCores(Number(e.target.value))}
              className="mt-1 block w-full rounded border border-gray-300 px-3 py-1.5 text-sm"
            >
              {CPU_OPTIONS.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="memory-gb" className="block text-sm font-medium text-gray-700">
              Memory (GB)
            </label>
            <select
              id="memory-gb"
              value={memoryGb}
              onChange={(e) => setMemoryGb(Number(e.target.value))}
              className="mt-1 block w-full rounded border border-gray-300 px-3 py-1.5 text-sm"
            >
              {MEMORY_OPTIONS.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>

      {/* ── Environment variables section ── */}
      <section aria-label="Environment Variables">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-900">Environment Variables</h2>
          <button
            type="button"
            onClick={addEnvVar}
            className="text-sm px-3 py-1 rounded border border-gray-300 hover:bg-gray-50"
          >
            + Add Variable
          </button>
        </div>

        {envVars.length === 0 && (
          <p className="text-sm text-gray-500 italic">No environment variables declared.</p>
        )}

        <div className="space-y-2">
          {envVars.map((ev, i) => (
            <div key={i} className="flex gap-2 items-center">
              <input
                type="text"
                aria-label={`Environment variable key ${i + 1}`}
                value={ev.key}
                onChange={(e) => updateEnvVar(i, 'key', e.target.value)}
                placeholder="KEY"
                className="flex-1 rounded border border-gray-300 px-3 py-1.5 text-sm font-mono"
              />
              <span className="text-gray-400">=</span>
              <input
                type="text"
                aria-label={`Environment variable value ${i + 1}`}
                value={ev.value}
                onChange={(e) => updateEnvVar(i, 'value', e.target.value)}
                placeholder="value"
                className="flex-1 rounded border border-gray-300 px-3 py-1.5 text-sm font-mono"
              />
              <button
                type="button"
                aria-label={`Remove variable ${i + 1}`}
                onClick={() => removeEnvVar(i)}
                className="text-red-500 hover:text-red-700 text-sm px-2"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* ── Assets section ── */}
      <section aria-label="Assets">
        <h2 className="text-lg font-semibold text-gray-900 mb-3">Assets</h2>

        {/* Existing assets */}
        {assets.length > 0 && (
          <ul className="space-y-2 mb-4">
            {assets.map((asset, i) => (
              <li
                key={i}
                className="flex items-center gap-3 rounded border border-green-200 bg-green-50 px-3 py-2 text-sm"
              >
                <span className="font-medium text-green-800">{asset.name}</span>
                <span className="text-green-600 text-xs uppercase tracking-wide">
                  {asset.kind}
                </span>
                <span className="text-green-700 font-mono text-xs">{asset.mountPath}</span>
              </li>
            ))}
          </ul>
        )}

        {/* Add asset controls */}
        {addMode === null && (
          <div className="flex gap-3 flex-wrap">
            <button
              type="button"
              data-testid="add-upload-btn"
              onClick={() => setAddMode('upload')}
              className="px-3 py-2 text-sm rounded border border-blue-300 text-blue-700 hover:bg-blue-50"
            >
              + Add Upload
            </button>
            <button
              type="button"
              data-testid="add-external-btn"
              onClick={() => setAddMode('external_url')}
              className="px-3 py-2 text-sm rounded border border-blue-300 text-blue-700 hover:bg-blue-50"
            >
              + Add External URL
            </button>
            <AssetTileSharedReference />
          </div>
        )}

        {/* Active add tile */}
        {addMode === 'upload' && (
          <div className="mt-3">
            <AssetTileUpload
              submissionId={submissionId}
              onAssetReady={(asset) => handleAssetReady({ ...asset, kind: 'upload' })}
            />
            <button
              type="button"
              onClick={() => setAddMode(null)}
              className="mt-2 text-sm text-gray-500 hover:underline"
            >
              Cancel
            </button>
          </div>
        )}

        {addMode === 'external_url' && (
          <div className="mt-3">
            <AssetTileExternalUrl
              onAssetReady={(asset) => handleAssetReady({ ...asset, kind: 'external_url' })}
            />
            <button
              type="button"
              onClick={() => setAddMode(null)}
              className="mt-2 text-sm text-gray-500 hover:underline"
            >
              Cancel
            </button>
          </div>
        )}
      </section>

      {/* ── Submit ── */}
      {submitError && (
        <div role="alert" className="rounded bg-red-50 p-3 text-sm text-red-800">
          {submitError}
        </div>
      )}

      <button
        type="submit"
        disabled={isSubmitting}
        className="w-full rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:bg-gray-400"
      >
        {isSubmitting ? 'Saving…' : 'Save Manifest'}
      </button>
    </form>
  );
}
