// pattern: Imperative Shell — fetches and renders the approval queue list
/**
 * ApprovalQueue: Lists pending submissions for the current viewer's role.
 *
 * - research_approver role: sees awaiting_research_review submissions
 * - it_approver role: sees awaiting_it_review submissions
 * - admin (it_approver): sees both
 *
 * Clicking a row navigates to the detail review route.
 *
 * Verifies: rac-v1.AC2.2 (UI)
 */

import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { listSubmissionsByStatus, type SubmissionSummary } from './api';

// ─── Props ─────────────────────────────────────────────────────────────────────

interface ApprovalQueueProps {
  /**
   * Which statuses to show.
   * Determined by the route/parent based on current user's roles.
   */
  statusFilters: string[];
}

// ─── Row component ─────────────────────────────────────────────────────────────

function QueueRow({ submission }: { submission: SubmissionSummary }) {
  return (
    <tr className="hover:bg-gray-50 cursor-pointer">
      <td className="px-4 py-3 text-sm font-mono">
        <Link to="/approval-queue/$submissionId" params={{ submissionId: submission.id }}>
          {submission.slug}
        </Link>
      </td>
      <td className="px-4 py-3 text-sm">
        <span className="inline-block px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 text-xs font-medium">
          {submission.status}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-gray-600 truncate max-w-xs">
        {submission.github_repo_url}
      </td>
      <td className="px-4 py-3 text-sm text-gray-500">
        {new Date(submission.created_at).toLocaleDateString()}
      </td>
    </tr>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

/**
 * ApprovalQueue: rendered in /approval-queue route.
 *
 * Accepts statusFilters (e.g. ['awaiting_research_review']) from the route
 * based on the current user's role.
 */
export function ApprovalQueue({ statusFilters }: ApprovalQueueProps) {
  // Fetch submissions for all relevant statuses
  const queries = useQuery({
    queryKey: ['approval-queue', ...statusFilters],
    queryFn: async () => {
      const allResults = await Promise.all(
        statusFilters.map((status) => listSubmissionsByStatus(status)),
      );
      return allResults.flat();
    },
    retry: 1,
  });

  const { data: submissions, isLoading, error } = queries;

  if (isLoading) {
    return (
      <div className="text-sm text-gray-600" aria-live="polite">
        Loading approval queue…
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" className="rounded-md bg-red-50 p-4 text-sm text-red-800">
        {error instanceof Error ? error.message : 'Failed to load approval queue'}
      </div>
    );
  }

  if (!submissions || submissions.length === 0) {
    return (
      <div className="text-sm text-gray-600" aria-label="approval queue empty state">
        No submissions pending approval.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-gray-200">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-4 py-3 text-left font-semibold text-gray-600">App slug</th>
            <th className="px-4 py-3 text-left font-semibold text-gray-600">Status</th>
            <th className="px-4 py-3 text-left font-semibold text-gray-600">Repository</th>
            <th className="px-4 py-3 text-left font-semibold text-gray-600">Submitted</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {submissions.map((sub) => (
            <QueueRow key={sub.id} submission={sub} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
