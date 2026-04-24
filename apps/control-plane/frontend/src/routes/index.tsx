import { createFileRoute, Link } from '@tanstack/react-router';

export const Route = createFileRoute('/')({
  component: Home,
});

function Home() {
  return (
    <div className="space-y-6">
      <div className="rounded-lg bg-blue-50 p-6">
        <h2 className="text-3xl font-bold text-blue-900">Welcome</h2>
        <p className="mt-2 text-blue-700">
          Submit your research applications to the RAC platform for scanning and
          approval.
        </p>
      </div>

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
    </div>
  );
}
