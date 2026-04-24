// pattern: Imperative Shell — route component for admin ownership flags
/**
 * /admin/ownership route: renders the FlagsPanel.
 *
 * Verifies: rac-v1.AC9.2 (UI), rac-v1.AC9.3 (UI)
 */

import { createFileRoute } from '@tanstack/react-router';
import { FlagsPanel } from '@/features/admin/ownership/flags-panel';

export const Route = createFileRoute('/admin/ownership')({
  component: AdminOwnershipPage,
});

function AdminOwnershipPage() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Ownership Flags</h2>
      <FlagsPanel />
    </div>
  );
}
