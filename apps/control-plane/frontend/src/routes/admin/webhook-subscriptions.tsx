import { createFileRoute } from '@tanstack/react-router';
import { WebhookSubscriptionsAdmin } from '@/features/admin/webhook-subscriptions';

export const Route = createFileRoute('/admin/webhook-subscriptions')({
  component: WebhookSubscriptionsPage,
});

function WebhookSubscriptionsPage() {
  return (
    <div className="space-y-6">
      <WebhookSubscriptionsAdmin />
    </div>
  );
}
