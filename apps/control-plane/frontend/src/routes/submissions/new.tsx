import { useRef } from 'react';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { NewSubmissionForm } from '@/features/submissions/new-submission-form';
import { createSubmission } from '@/lib/api';
import type { SubmissionCreateRequest } from '@/features/submissions/schemas';

export const Route = createFileRoute('/submissions/new')({
  component: NewSubmissionPage,
});

function NewSubmissionPage() {
  const navigate = useNavigate();
  // Generate one Idempotency-Key per user-intent (per form instance), so
  // double-clicks and network retries hit the same key and the backend
  // collapses them into one submission.
  const idempotencyKeyRef = useRef<string>(crypto.randomUUID());

  const handleSubmit = async (data: SubmissionCreateRequest) => {
    await createSubmission(data, { idempotencyKey: idempotencyKeyRef.current });
    await navigate({ to: '/submissions' });
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
