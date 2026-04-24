import { createFileRoute, Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { getSubmission } from '@/lib/api';
import { ScanFindingsView } from '@/features/submissions/scan-findings-view';
import type { ScanResult } from '@/features/submissions/scan-findings-view';

export const Route = createFileRoute('/submissions/$submissionId')({
  component: SubmissionDetailPage,
});

const STATUS_DISPLAY: Record<string, string> = {
  awaiting_scan: 'Awaiting Scan',
  pipeline_error: 'Pipeline Error',
  scan_rejected: 'Scan Rejected',
  needs_user_action: 'Needs User Action',
  needs_assistance: 'Needs Assistance',
  awaiting_research_review: 'Awaiting Research Review',
  research_rejected: 'Research Rejected',
  awaiting_it_review: 'Awaiting IT Review',
  it_rejected: 'IT Rejected',
  approved: 'Approved',
  deployed: 'Deployed',
};

function statusBadgeColor(status: string): string {
  switch (status) {
    case 'approved':
    case 'deployed':
      return 'bg-green-100 text-green-800';
    case 'pipeline_error':
    case 'scan_rejected':
    case 'research_rejected':
    case 'it_rejected':
      return 'bg-red-100 text-red-800';
    default:
      return 'bg-yellow-100 text-yellow-800';
  }
}

/** Map submission status → a synthetic ScanResult verdict for the view component. */
function deriveScanResult(submission: Awaited<ReturnType<typeof getSubmission>>): ScanResult | null {
  const { status } = submission;

  // Only show scan findings section for terminal-scan statuses
  if (status === 'scan_rejected') {
    return {
      verdict: 'rejected',
      effective_severity: 'critical',
      findings: [],
    };
  }
  if (status === 'pipeline_error') {
    return {
      verdict: 'build_failed',
      effective_severity: 'none',
      findings: [],
    };
  }
  if (
    status === 'approved' ||
    status === 'deployed' ||
    status === 'needs_user_action' ||
    status === 'awaiting_research_review' ||
    status === 'awaiting_it_review' ||
    status === 'needs_assistance'
  ) {
    return {
      verdict: 'passed',
      effective_severity: 'none',
      findings: [],
    };
  }

  return null;
}

function SubmissionDetailPage() {
  const { submissionId } = Route.useParams();

  const { data: submission, isLoading, error } = useQuery({
    queryKey: ['submissions', submissionId],
    queryFn: () => getSubmission(submissionId),
    retry: 1,
  });

  if (isLoading) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-600">Loading submission…</p>
      </div>
    );
  }

  if (error || !submission) {
    return (
      <div className="space-y-4">
        <Link to="/submissions" className="text-blue-600 hover:underline text-sm">
          ← Back to Submissions
        </Link>
        <div className="rounded-md bg-red-50 p-4">
          <p className="text-red-800">
            {error instanceof Error ? error.message : 'Submission not found'}
          </p>
        </div>
      </div>
    );
  }

  const scanResult = deriveScanResult(submission);

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <Link to="/submissions" className="text-blue-600 hover:underline text-sm">
        ← Back to Submissions
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-2xl font-bold font-mono">{submission.slug}</h2>
          <p className="text-sm text-gray-500 mt-1">ID: {submission.id}</p>
        </div>
        <span
          className={`inline-block rounded-full px-3 py-1 text-sm font-semibold ${statusBadgeColor(submission.status)}`}
        >
          {STATUS_DISPLAY[submission.status] ?? submission.status}
        </span>
      </div>

      {/* Details card */}
      <div className="rounded-md border border-gray-200 bg-white p-5 space-y-3">
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs font-semibold uppercase text-gray-500">Repository</dt>
            <dd className="mt-1 text-sm font-mono break-all">
              <a
                href={submission.githubRepoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                {submission.githubRepoUrl}
              </a>
            </dd>
          </div>

          <div>
            <dt className="text-xs font-semibold uppercase text-gray-500">Git Ref</dt>
            <dd className="mt-1 text-sm font-mono">{submission.gitRef}</dd>
          </div>

          <div>
            <dt className="text-xs font-semibold uppercase text-gray-500">Dockerfile Path</dt>
            <dd className="mt-1 text-sm font-mono">{submission.dockerfilePath}</dd>
          </div>

          <div>
            <dt className="text-xs font-semibold uppercase text-gray-500">Department</dt>
            <dd className="mt-1 text-sm">{submission.deptFallback}</dd>
          </div>

          <div>
            <dt className="text-xs font-semibold uppercase text-gray-500">Submitted</dt>
            <dd className="mt-1 text-sm">{new Date(submission.createdAt).toLocaleString()}</dd>
          </div>

          <div>
            <dt className="text-xs font-semibold uppercase text-gray-500">Last Updated</dt>
            <dd className="mt-1 text-sm">{new Date(submission.updatedAt).toLocaleString()}</dd>
          </div>
        </dl>
      </div>

      {/* Scan findings — shown when a scan result is available */}
      {scanResult && (
        <div className="rounded-md border border-gray-200 bg-white p-5">
          <ScanFindingsView scanResult={scanResult} />
        </div>
      )}
    </div>
  );
}
