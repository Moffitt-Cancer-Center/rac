// pattern: Imperative Shell — paginated access log viewer with filters.
import { useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { listAccessLog } from './api';
import type { AccessLogItem } from './types';

type AccessLogViewerProps = {
  appId: string;
};

function statusBadge(status: number | null) {
  if (status === null) return '—';
  const cls =
    status < 300
      ? 'bg-green-100 text-green-800'
      : status < 400
        ? 'bg-blue-100 text-blue-800'
        : status < 500
          ? 'bg-yellow-100 text-yellow-800'
          : 'bg-red-100 text-red-800';
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono font-medium ${cls}`}>
      {status}
    </span>
  );
}

function LogRow({ item }: { item: AccessLogItem }) {
  return (
    <tr className="hover:bg-gray-50">
      <td className="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">
        {new Date(item.createdAt).toLocaleString()}
      </td>
      <td className="px-3 py-2 text-xs">
        {item.accessMode ?? '—'}
      </td>
      <td className="px-3 py-2 text-xs font-mono font-semibold">
        {item.method ?? '—'}
      </td>
      <td className="px-3 py-2 text-xs font-mono truncate max-w-xs">
        {item.path ?? '—'}
      </td>
      <td className="px-3 py-2">{statusBadge(item.upstreamStatus)}</td>
      <td className="px-3 py-2 text-xs text-gray-500">
        {item.latencyMs !== null ? `${item.latencyMs}ms` : '—'}
      </td>
      <td className="px-3 py-2 text-xs text-gray-500">
        {item.reviewerLabel ?? '—'}
      </td>
      <td className="px-3 py-2 text-xs font-mono text-gray-400">
        {item.sourceIp ?? '—'}
      </td>
    </tr>
  );
}

export function AccessLogViewer({ appId }: AccessLogViewerProps) {
  const [modeFilter, setModeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [jtiFilter, setJtiFilter] = useState('');

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    error,
  } = useInfiniteQuery({
    queryKey: ['access-log', appId, modeFilter, statusFilter, jtiFilter],
    queryFn: ({ pageParam }) =>
      listAccessLog(appId, {
        before: pageParam as string | null,
        limit: 50,
        mode: modeFilter || null,
        status: statusFilter ? parseInt(statusFilter, 10) : null,
        jti: jtiFilter || null,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? null,
  });

  const allItems = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="space-y-4">
      {/* Filter controls */}
      <div className="flex flex-wrap gap-3">
        <div>
          <label htmlFor="al-mode" className="block text-xs text-gray-600 mb-1">
            Access Mode
          </label>
          <select
            id="al-mode"
            value={modeFilter}
            onChange={(e) => setModeFilter(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm"
          >
            <option value="">All</option>
            <option value="token_required">Token required</option>
            <option value="public">Public</option>
          </select>
        </div>
        <div>
          <label htmlFor="al-status" className="block text-xs text-gray-600 mb-1">
            HTTP Status
          </label>
          <input
            id="al-status"
            type="number"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            placeholder="e.g. 200"
            className="border border-gray-300 rounded px-2 py-1 text-sm w-24"
          />
        </div>
        <div>
          <label htmlFor="al-jti" className="block text-xs text-gray-600 mb-1">
            Reviewer Token JTI
          </label>
          <input
            id="al-jti"
            type="text"
            value={jtiFilter}
            onChange={(e) => setJtiFilter(e.target.value)}
            placeholder="UUID"
            className="border border-gray-300 rounded px-2 py-1 text-sm w-64 font-mono"
          />
        </div>
      </div>

      {isLoading && <p className="text-sm text-gray-500">Loading access log…</p>}

      {error && (
        <p className="text-sm text-red-600" role="alert">
          Failed to load access log: {(error as Error).message}
        </p>
      )}

      {!isLoading && allItems.length === 0 && (
        <p className="text-sm text-gray-500">No access log entries found.</p>
      )}

      {allItems.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Timestamp
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Mode
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Method
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Path
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Status
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Latency
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Reviewer
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Source IP
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {allItems.map((item) => (
                  <LogRow key={item.id} item={item} />
                ))}
              </tbody>
            </table>
          </div>

          {hasNextPage && (
            <button
              type="button"
              onClick={() => void fetchNextPage()}
              disabled={isFetchingNextPage}
              className="w-full border border-gray-300 rounded py-2 text-sm hover:bg-gray-50
                         disabled:opacity-50"
            >
              {isFetchingNextPage ? 'Loading…' : 'Load more'}
            </button>
          )}
        </>
      )}
    </div>
  );
}
