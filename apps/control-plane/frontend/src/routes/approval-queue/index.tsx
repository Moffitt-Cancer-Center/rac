// pattern: Imperative Shell — route component for approval queue list
/**
 * /approval-queue route: renders the ApprovalQueue list.
 *
 * Role-based status filters are resolved here based on the current user.
 * For now we show both review stages (admin sees all).
 *
 * Verifies: rac-v1.AC2.2 (UI)
 */

import { createFileRoute } from '@tanstack/react-router';
import { ApprovalQueue } from '@/features/approval-queue';

export const Route = createFileRoute('/approval-queue/')({
  component: ApprovalQueuePage,
});

function ApprovalQueuePage() {
  // TODO: derive from user roles; for now show both review stages
  const statusFilters = ['awaiting_research_review', 'awaiting_it_review'];

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Approval Queue</h2>
      <ApprovalQueue statusFilters={statusFilters} />
    </div>
  );
}
