// pattern: Functional Core — pure render based on props + local state; no external side effects.
/**
 * FailedProvisionsList: Admin page that shows submissions stuck in 'approved'
 * with provisioning failures and provides a per-row retry button.
 *
 * Behaviour:
 * - Fetches /api/admin/submissions/failed-provisions on mount and on refresh.
 * - Retry button is disabled during the API call (spinner shown).
 * - On success, refreshes the list.
 * - On error, shows the returned error message inline next to the row.
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listFailedProvisions, retryProvisioning } from './api';
import type { FailedProvisionRow } from './api';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// ─── Row component ────────────────────────────────────────────────────────────

interface RowProps {
  row: FailedProvisionRow;
  onRetried: () => void;
}

function FailedProvisionRow({ row, onRetried }: RowProps) {
  const [rowError, setRowError] = useState<string | null>(null);

  const retryMutation = useMutation({
    mutationFn: () =>
      retryProvisioning(row.submission_id, crypto.randomUUID()),
    onSuccess: () => {
      setRowError(null);
      onRetried();
    },
    onError: (err: unknown) => {
      setRowError(err instanceof Error ? err.message : 'Unknown error');
    },
  });

  return (
    <tr>
      <td className="px-3 py-2 font-mono text-sm">{row.slug}</td>
      <td className="px-3 py-2 text-sm text-gray-600 truncate max-w-xs" title={row.pi_principal_id}>
        {row.pi_principal_id}
      </td>
      <td className="px-3 py-2 text-sm text-red-700 max-w-sm">
        <span title={row.last_failure_reason}>
          {row.last_failure_reason.slice(0, 80)}
          {row.last_failure_reason.length > 80 ? '…' : ''}
        </span>
      </td>
      <td className="px-3 py-2 text-sm text-gray-500">{formatDate(row.failed_at)}</td>
      <td className="px-3 py-2 text-center text-sm">{row.retry_count}</td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          <button
            onClick={() => retryMutation.mutate()}
            disabled={retryMutation.isPending}
            aria-label={`Retry provisioning for ${row.slug}`}
            className="rounded bg-blue-600 px-3 py-1 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {retryMutation.isPending ? 'Retrying…' : 'Retry'}
          </button>
          {rowError && (
            <span role="alert" className="text-sm text-red-600">
              {rowError}
            </span>
          )}
        </div>
      </td>
    </tr>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

export function FailedProvisionsList() {
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['admin', 'failed-provisions'],
    queryFn: listFailedProvisions,
    refetchInterval: 30_000, // auto-refresh every 30 seconds
  });

  function handleRetried() {
    void queryClient.invalidateQueries({ queryKey: ['admin', 'failed-provisions'] });
  }

  if (isLoading) {
    return (
      <div className="p-6 text-gray-500" aria-live="polite">
        Loading failed provisions…
      </div>
    );
  }

  if (isError) {
    return (
      <div role="alert" className="p-6 text-red-600">
        Error loading failed provisions:{' '}
        {error instanceof Error ? error.message : 'Unknown error'}
      </div>
    );
  }

  const rows = data ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Failed Provisions</h2>
        <button
          onClick={() => refetch()}
          className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-600 hover:bg-gray-50"
        >
          Refresh
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="text-gray-500 text-sm">No failed provisions — all clear.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Slug</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">PI</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Last Failure</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Failed At</th>
                <th className="px-3 py-2 text-center text-xs font-medium text-gray-500 uppercase">Retries</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {rows.map((row) => (
                <FailedProvisionRow
                  key={row.submission_id}
                  row={row}
                  onRetried={handleRetried}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
