// pattern: Imperative Shell — route component for app access log viewer.
import { createFileRoute } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AccessLogViewer } from '@/features/access-log/access-log-viewer';

export const Route = createFileRoute('/apps/$appId/access-log')({
  component: AppAccessLogPage,
});

const qc = new QueryClient();

function AppAccessLogPage() {
  const { appId } = Route.useParams();

  return (
    <QueryClientProvider client={qc}>
      <div className="space-y-6">
        <h2 className="text-2xl font-bold text-gray-900">Access Log</h2>
        <AccessLogViewer appId={appId} />
      </div>
    </QueryClientProvider>
  );
}
