import { Outlet, createRootRoute } from '@tanstack/react-router';
import { MsalProvider } from '@azure/msal-react';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import { msalInstance } from '@/lib/msal';

const queryClient = new QueryClient();

function RootLayout() {
  return (
    <MsalProvider instance={msalInstance}>
      <QueryClientProvider client={queryClient}>
        <div className="flex flex-col min-h-screen">
          <header className="bg-blue-600 text-white py-4 px-6">
            <div className="max-w-6xl mx-auto flex items-center justify-between">
              <h1 className="text-2xl font-bold">RAC Control Plane</h1>
              <nav className="flex gap-4">
                <a href="/" className="hover:underline">
                  Home
                </a>
                <a href="/submissions" className="hover:underline">
                  Submissions
                </a>
              </nav>
            </div>
          </header>

          <main className="flex-1 py-8 px-6">
            <div className="max-w-6xl mx-auto">
              <Outlet />
            </div>
          </main>

          <footer className="bg-gray-100 py-4 px-6 border-t">
            <div className="max-w-6xl mx-auto text-center text-sm text-gray-600">
              <p>
                Research Application Commons &bull; Control Plane v1.0.0 &bull; Moffitt
                Cancer Center
              </p>
            </div>
          </footer>
        </div>
      </QueryClientProvider>
    </MsalProvider>
  );
}

export const Route = createRootRoute({
  component: RootLayout,
});
