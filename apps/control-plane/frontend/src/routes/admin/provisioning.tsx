import { createFileRoute } from '@tanstack/react-router';
import { requireAuth } from '@/lib/auth-guard';
import { FailedProvisionsList } from '@/features/admin/provisioning/failed-provisions';

export const Route = createFileRoute('/admin/provisioning')({
  beforeLoad: requireAuth,
  component: ProvisioningAdminPage,
});

function ProvisioningAdminPage() {
  return (
    <div className="space-y-6">
      <FailedProvisionsList />
    </div>
  );
}
