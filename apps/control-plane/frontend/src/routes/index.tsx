import { createFileRoute, Link } from '@tanstack/react-router';
import {
  AuthenticatedTemplate,
  UnauthenticatedTemplate,
  useMsal,
} from '@azure/msal-react';
import { apiScope } from '@/lib/msal';

export const Route = createFileRoute('/')({
  component: Home,
});

function Home() {
  const { instance } = useMsal();

  return (
    <div className="space-y-6">
      <div className="rounded-lg bg-blue-50 p-6">
        <h2 className="text-3xl font-bold text-blue-900">Welcome</h2>
        <p className="mt-2 text-blue-700">
          Submit your research applications to the RAC platform for scanning and
          approval.
        </p>
      </div>

      <UnauthenticatedTemplate>
        <div className="rounded-lg border-2 border-blue-300 bg-white p-6 text-center">
          <h3 className="text-xl font-bold text-blue-900">
            Sign in to get started
          </h3>
          <p className="mt-2 text-gray-600">
            You need to sign in with your institutional Microsoft account to view or
            create submissions.
          </p>
          <button
            type="button"
            onClick={() =>
              instance.loginRedirect({
                scopes: [apiScope],
                redirectStartPage: window.location.origin + '/submissions',
              })
            }
            className="mt-4 rounded-md bg-blue-600 px-6 py-2 font-medium text-white hover:bg-blue-700"
          >
            Sign in with Microsoft
          </button>
        </div>
      </UnauthenticatedTemplate>

      <AuthenticatedTemplate>
        <div className="grid grid-cols-2 gap-4">
          <Link
            to={'/submissions'}
            className="block rounded-lg border-2 border-blue-300 p-6 hover:bg-blue-50"
          >
            <h3 className="text-xl font-bold text-blue-900">View Submissions</h3>
            <p className="mt-2 text-gray-600">
              Browse your previous submissions and their status
            </p>
          </Link>

          <Link
            to={'/submissions/new'}
            className="block rounded-lg border-2 border-green-300 p-6 hover:bg-green-50"
          >
            <h3 className="text-xl font-bold text-green-900">New Submission</h3>
            <p className="mt-2 text-gray-600">Create a new application submission</p>
          </Link>
        </div>
      </AuthenticatedTemplate>
    </div>
  );
}
