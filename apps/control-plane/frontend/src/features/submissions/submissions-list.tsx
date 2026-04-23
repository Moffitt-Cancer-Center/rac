import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listSubmissions } from '@/lib/api';
import type { SubmissionResponse } from './schemas';

const STATUS_DISPLAY: Record<string, string> = {
  awaiting_scan: 'Awaiting Scan',
  pipeline_error: 'Pipeline Error',
  scan_rejected: 'Scan Rejected',
  needs_user_action: 'Needs User Action',
  needs_assistance: 'Needs Assistance',
  awaiting_research_review: 'Awaiting Research Review',
  research_rejected: 'Research Rejected',
  awaiting_it_review: 'Awaiting IT Review',
  it_rejected: 'IT Rejected',
  approved: 'Approved',
  deployed: 'Deployed',
};

interface SubmissionsListProps {
  pageSize?: number;
}

export function SubmissionsList({ pageSize = 10 }: SubmissionsListProps) {
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string>('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['submissions', page, statusFilter],
    queryFn: () =>
      listSubmissions({
        page,
        pageSize,
        status: statusFilter || undefined,
      }),
  });

  const submissions = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <div>
          <label htmlFor="status-filter" className="block text-sm font-medium">
            Status Filter
          </label>
          <select
            id="status-filter"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(0);
            }}
            className="mt-1 rounded-md border border-gray-300 px-3 py-2"
          >
            <option value="">All Statuses</option>
            {Object.entries(STATUS_DISPLAY).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 p-4">
          <p className="text-sm text-red-800">
            {error instanceof Error ? error.message : 'Failed to load submissions'}
          </p>
        </div>
      )}

      {isLoading ? (
        <div className="text-center py-8">
          <p className="text-gray-600">Loading submissions...</p>
        </div>
      ) : submissions.length === 0 ? (
        <div className="text-center py-8">
          <p className="text-gray-600">No submissions found</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse border border-gray-300">
            <thead className="bg-gray-100">
              <tr>
                <th className="border border-gray-300 px-4 py-2 text-left">Slug</th>
                <th className="border border-gray-300 px-4 py-2 text-left">Status</th>
                <th className="border border-gray-300 px-4 py-2 text-left">Created</th>
                <th className="border border-gray-300 px-4 py-2 text-left">Department</th>
              </tr>
            </thead>
            <tbody>
              {submissions.map((submission) => (
                <tr key={submission.id} className="hover:bg-gray-50">
                  <td className="border border-gray-300 px-4 py-2 font-mono text-sm">
                    {submission.slug}
                  </td>
                  <td className="border border-gray-300 px-4 py-2">
                    <span
                      className={`inline-block rounded-full px-3 py-1 text-sm font-semibold ${getStatusBadgeColor(submission.status)}`}
                    >
                      {STATUS_DISPLAY[submission.status] || submission.status}
                    </span>
                  </td>
                  <td className="border border-gray-300 px-4 py-2 text-sm">
                    {new Date(submission.createdAt).toLocaleDateString()}
                  </td>
                  <td className="border border-gray-300 px-4 py-2">
                    {submission.deptFallback}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            onClick={() => setPage(Math.max(0, page - 1))}
            disabled={page === 0}
            className="rounded-md border border-gray-300 px-3 py-1 disabled:text-gray-400"
          >
            Previous
          </button>

          <span className="text-sm">
            Page {page + 1} of {totalPages}
          </span>

          <button
            onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
            disabled={page >= totalPages - 1}
            className="rounded-md border border-gray-300 px-3 py-1 disabled:text-gray-400"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

function getStatusBadgeColor(status: string): string {
  switch (status) {
    case 'approved':
    case 'deployed':
      return 'bg-green-100 text-green-800';
    case 'pipeline_error':
    case 'scan_rejected':
    case 'research_rejected':
    case 'it_rejected':
      return 'bg-red-100 text-red-800';
    case 'awaiting_scan':
    case 'needs_user_action':
    case 'needs_assistance':
    case 'awaiting_research_review':
    case 'awaiting_it_review':
      return 'bg-yellow-100 text-yellow-800';
    default:
      return 'bg-gray-100 text-gray-800';
  }
}
