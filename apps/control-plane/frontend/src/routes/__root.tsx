import { Outlet, Link, createRootRoute } from '@tanstack/react-router';
import {
  MsalProvider,
  useMsal,
  useIsAuthenticated,
  AuthenticatedTemplate,
  UnauthenticatedTemplate,
} from '@azure/msal-react';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import { msalInstance, apiScope } from '@/lib/msal';

const queryClient = new QueryClient();

function AuthWidget() {
  const { instance } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const account = instance.getActiveAccount() ?? instance.getAllAccounts()[0];

  if (!isAuthenticated) {
    return (
      <button
        type="button"
        onClick={() =>
          instance.loginRedirect({
            scopes: [apiScope],
            redirectStartPage: window.location.href,
          })
        }
        className="rounded-md bg-white/10 px-3 py-1 text-sm font-medium hover:bg-white/20"
      >
        Sign in
      </button>
    );
  }

  return (
    <div className="flex items-center gap-3 text-sm">
      <span title={account?.username ?? ''}>
        {account?.name ?? account?.username ?? 'Signed in'}
      </span>
      <button
        type="button"
        onClick={() => instance.logoutRedirect()}
        className="rounded-md bg-white/10 px-3 py-1 font-medium hover:bg-white/20"
      >
        Sign out
      </button>
    </div>
  );
}

function RootLayout() {
  return (
    <MsalProvider instance={msalInstance}>
      <QueryClientProvider client={queryClient}>
        <div className="flex flex-col min-h-screen">
          <header className="bg-blue-600 text-white py-4 px-6">
            <div className="max-w-6xl mx-auto flex items-center justify-between">
              <h1 className="text-2xl font-bold">RAC Control Plane</h1>
              <div className="flex items-center gap-6">
                <nav className="flex gap-4">
                  <Link to="/" className="hover:underline">
                    Home
                  </Link>
                  <AuthenticatedTemplate>
                    <Link to="/submissions" className="hover:underline">
                      Submissions
                    </Link>
                    <Link
                      to="/admin/webhook-subscriptions"
                      className="hover:underline"
                    >
                      Webhooks
                    </Link>
                  </AuthenticatedTemplate>
                </nav>
                <AuthWidget />
              </div>
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
