// pattern: Imperative Shell — admin-only route for access mode toggle.
import { createFileRoute, redirect } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AccessModeToggleCard } from '@/features/admin/access-mode/toggle-card';

export const Route = createFileRoute('/apps/$appId/access-mode')({
  component: AppAccessModePage,
});

const qc = new QueryClient();

function AppAccessModePage() {
  const { appId } = Route.useParams();

  return (
    <QueryClientProvider client={qc}>
      <div className="space-y-6">
        <h2 className="text-2xl font-bold text-gray-900">Access Mode</h2>
        <AccessModeToggleCard appId={appId} currentMode="token_required" />
      </div>
    </QueryClientProvider>
  );
}
