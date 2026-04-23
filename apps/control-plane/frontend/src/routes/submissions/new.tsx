import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { NewSubmissionForm } from '@/features/submissions/new-submission-form';
import { createSubmission } from '@/lib/api';
import type { SubmissionCreateRequest } from '@/features/submissions/schemas';

export const Route = createFileRoute('/submissions/new' as any)({
  component: NewSubmissionPage,
});

function NewSubmissionPage() {
  const navigate = useNavigate();

  const handleSubmit = async (data: SubmissionCreateRequest) => {
    await createSubmission(data);
    // Navigate to submissions list on success
    await navigate({ to: '/submissions/' as any });
  };

  return (
    <div className="space-y-6">
      <h2 className="text-3xl font-bold">New Application</h2>

      <div className="rounded-lg bg-gray-50 p-6">
        <p className="text-gray-700">
          Fill out the form below to submit a new application for scanning and
          approval.
        </p>
      </div>

      <div className="mx-auto max-w-2xl rounded-lg border border-gray-300 p-6">
        <NewSubmissionForm onSubmit={handleSubmit} />
      </div>
    </div>
  );
}
