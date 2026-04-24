// pattern: Imperative Shell — route component for admin cost dashboard
/**
 * /admin/cost route: renders the CostDashboard.
 *
 * Verifies: rac-v1.AC11.2 (UI), rac-v1.AC11.3 (UI)
 */

import { createFileRoute } from '@tanstack/react-router';
import { CostDashboard } from '@/features/admin/cost-dashboard';

export const Route = createFileRoute('/admin/cost')({
  component: AdminCostPage,
});

function AdminCostPage() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Cost Dashboard</h2>
      <CostDashboard />
    </div>
  );
}
