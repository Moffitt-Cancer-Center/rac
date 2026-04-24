import { createFileRoute } from '@tanstack/react-router';
import { FailedProvisionsList } from '@/features/admin/provisioning/failed-provisions';

export const Route = createFileRoute('/admin/provisioning')({
  component: ProvisioningAdminPage,
});

function ProvisioningAdminPage() {
  return (
    <div className="space-y-6">
      <FailedProvisionsList />
    </div>
  );
}
