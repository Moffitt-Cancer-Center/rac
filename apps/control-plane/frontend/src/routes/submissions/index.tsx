import { createFileRoute, Link } from '@tanstack/react-router';
import { SubmissionsList } from '@/features/submissions/submissions-list';

export const Route = createFileRoute('/submissions/')({
  component: SubmissionsPage,
});

function SubmissionsPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-3xl font-bold">Your Submissions</h2>
        <Link
          to={'/submissions/new'}
          className="rounded-md bg-green-600 px-4 py-2 text-white hover:bg-green-700"
        >
          New Submission
        </Link>
      </div>

      <SubmissionsList pageSize={10} />
    </div>
  );
}
