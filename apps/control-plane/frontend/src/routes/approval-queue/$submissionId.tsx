// pattern: Imperative Shell — route component for submission detail review
/**
 * /approval-queue/:submissionId route: renders SubmissionReview detail.
 *
 * Fetches submission + scan result; determines the approval stage from status.
 *
 * Verifies: rac-v1.AC2.2 (UI), rac-v1.AC5.4 (Defender badge)
 */

import { createFileRoute, Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { getSubmissionDetail, getScanResult, type ApprovalStage } from '@/features/approval-queue/api';
import { SubmissionReview } from '@/features/approval-queue/submission-review';

export const Route = createFileRoute('/approval-queue/$submissionId')({
  component: ApprovalQueueDetailPage,
});

/** Map a submission status to the approval stage the reviewer should act on. */
function stageFromStatus(status: string): ApprovalStage {
  if (status === 'awaiting_it_review') return 'it';
  return 'research';
}

function ApprovalQueueDetailPage() {
  const { submissionId } = Route.useParams();

  const {
    data: submission,
    isLoading: loadingSubmission,
    error: submissionError,
  } = useQuery({
    queryKey: ['approval-queue-detail', submissionId],
    queryFn: () => getSubmissionDetail(submissionId),
    retry: 1,
  });

  const { data: scanResult } = useQuery({
    queryKey: ['approval-queue-scan', submissionId],
    queryFn: () => getScanResult(submissionId),
    enabled: !!submission,
    retry: 1,
  });

  if (loadingSubmission) {
    return (
      <div className="text-sm text-gray-600" aria-live="polite">
        Loading submission…
      </div>
    );
  }

  if (submissionError || !submission) {
    return (
      <div className="space-y-4">
        <Link to="/approval-queue" className="text-blue-600 hover:underline text-sm">
          ← Back to Queue
        </Link>
        <div role="alert" className="rounded-md bg-red-50 p-4 text-sm text-red-800">
          {submissionError instanceof Error
            ? submissionError.message
            : 'Submission not found'}
        </div>
      </div>
    );
  }

  const stage = stageFromStatus(submission.status);

  return (
    <div className="space-y-6">
      <Link to="/approval-queue" className="text-blue-600 hover:underline text-sm">
        ← Back to Queue
      </Link>
      <SubmissionReview
        submission={submission}
        scanResult={scanResult ?? null}
        stage={stage}
      />
    </div>
  );
}
