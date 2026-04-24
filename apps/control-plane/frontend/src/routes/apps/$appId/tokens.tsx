// pattern: Imperative Shell — route component for app token management.
import { useState } from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MintDialog } from '@/features/tokens/mint-dialog';
import { TokensTable } from '@/features/tokens/tokens-table';

export const Route = createFileRoute('/apps/$appId/tokens')({
  component: AppTokensPage,
});

const qc = new QueryClient();

function AppTokensPage() {
  const { appId } = Route.useParams();
  const [showMint, setShowMint] = useState(false);

  return (
    <QueryClientProvider client={qc}>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="text-2xl font-bold text-gray-900">Reviewer Tokens</h2>
          <button
            type="button"
            onClick={() => setShowMint(true)}
            className="bg-blue-600 text-white py-2 px-4 rounded text-sm font-semibold
                       hover:bg-blue-700"
          >
            Mint token
          </button>
        </div>

        {showMint && (
          <div className="bg-white border border-gray-200 rounded-lg p-6">
            <MintDialog appId={appId} onClose={() => setShowMint(false)} />
          </div>
        )}

        <TokensTable appId={appId} />
      </div>
    </QueryClientProvider>
  );
}
