import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { useEffect } from 'react';
import { apiScope } from '@/lib/msal';

type SignInSearch = { redirect?: string };

export const Route = createFileRoute('/signin')({
  validateSearch: (search: Record<string, unknown>): SignInSearch => ({
    redirect: typeof search.redirect === 'string' ? search.redirect : undefined,
  }),
  component: SignInPage,
});

function SignInPage() {
  const { instance } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const navigate = useNavigate();
  const search = Route.useSearch();

  useEffect(() => {
    if (isAuthenticated) {
      navigate({ to: search.redirect ?? '/', replace: true });
    }
  }, [isAuthenticated, navigate, search.redirect]);

  const handleSignIn = () => {
    instance.loginRedirect({
      scopes: [apiScope],
      redirectStartPage: search.redirect,
    });
  };

  return (
    <div className="mx-auto max-w-md rounded-lg border bg-white p-8 shadow-sm">
      <h2 className="text-2xl font-bold text-blue-900">Sign in</h2>
      <p className="mt-2 text-gray-600">
        You need to sign in with your institutional Microsoft account to continue.
      </p>
      <button
        type="button"
        onClick={handleSignIn}
        className="mt-6 w-full rounded-md bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700"
      >
        Sign in with Microsoft
      </button>
    </div>
  );
}
