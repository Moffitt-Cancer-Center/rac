// pattern: Imperative Shell — fetches and manages approval state
/**
 * SubmissionReview: Detail view for a single submission in the approval queue.
 *
 * Shows:
 * - Submission metadata (slug, researcher, PI, repo, dockerfile)
 * - Scan findings (ScanFindingsView) and Defender badge (AC5.4)
 * - Detection nudges (NudgesPanel)
 * - Approve / Reject / Request changes buttons (gated by stage + role)
 * - Notes dialog for each decision
 *
 * Verifies: rac-v1.AC2.2 (UI), rac-v1.AC5.4 (Defender badge)
 */

import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { ScanFindingsView } from '@/features/submissions/scan-findings-view';
import type { ScanResult } from '@/features/submissions/scan-findings-view';
import { NudgesPanel } from '@/features/nudges/nudges-panel';
import {
  postApproval,
  type ApprovalDecision,
  type ApprovalStage,
  type SubmissionSummary,
  type ScanResultSummary,
} from './api';

// ─── Props ─────────────────────────────────────────────────────────────────────

interface SubmissionReviewProps {
  /** Full submission record (pre-loaded by the route). */
  submission: SubmissionSummary;
  /** Scan result for this submission (may be null if scan not yet complete). */
  scanResult: ScanResultSummary | null | undefined;
  /** Which approval stage this viewer can act on. */
  stage: ApprovalStage;
}

// ─── Decision dialog ───────────────────────────────────────────────────────────

interface DecisionDialogProps {
  decision: ApprovalDecision;
  onConfirm: (notes: string) => void;
  onCancel: () => void;
  isSubmitting: boolean;
  errorMessage: string | null;
}

function DecisionDialog({
  decision,
  onConfirm,
  onCancel,
  isSubmitting,
  errorMessage,
}: DecisionDialogProps) {
  const [notes, setNotes] = useState('');

  const label =
    decision === 'approve'
      ? 'Approve'
      : decision === 'reject'
        ? 'Reject'
        : 'Request Changes';

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Confirm ${label}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
    >
      <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md">
        <h2 className="text-lg font-semibold mb-4">Confirm {label}?</h2>

        <label className="block text-sm font-medium text-gray-700 mb-1">
          Optional notes
          <textarea
            aria-label="Optional notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Add context (optional)"
          />
        </label>

        {errorMessage && (
          <div role="alert" className="mt-3 p-3 rounded-md bg-red-50 text-red-800 text-sm">
            {errorMessage}
          </div>
        )}

        <div className="mt-5 flex gap-3 justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="px-4 py-2 text-sm rounded-md border border-gray-300 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(notes)}
            disabled={isSubmitting}
            className="px-4 py-2 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {isSubmitting ? 'Submitting…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

/**
 * SubmissionReview: detail view for a pending submission.
 *
 * Includes the Defender badge (AC5.4): if scanResult.defender_timed_out is true,
 * a visible badge with text "Defender scan pending" and warning styling is rendered.
 */
export function SubmissionReview({
  submission,
  scanResult,
  stage,
}: SubmissionReviewProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const idempotencyKeyRef = useRef<string>('');

  const [activeDecision, setActiveDecision] = useState<ApprovalDecision | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  function openDialog(decision: ApprovalDecision) {
    // Generate a new idempotency key at the user-intent boundary
    idempotencyKeyRef.current = crypto.randomUUID();
    setActiveDecision(decision);
    setErrorMessage(null);
  }

  function closeDialog() {
    setActiveDecision(null);
    setErrorMessage(null);
  }

  async function handleConfirm(notes: string) {
    if (!activeDecision) return;

    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      await postApproval(
        submission.id,
        stage,
        { decision: activeDecision, notes: notes || undefined },
        idempotencyKeyRef.current,
      );

      // Invalidate queue queries and navigate back
      await queryClient.invalidateQueries({ queryKey: ['approval-queue'] });
      closeDialog();
      void navigate({ to: '/approval-queue' });
    } catch (err: unknown) {
      const e = err as { status?: number; message?: string };
      if (e.status === 409) {
        setErrorMessage(
          'This submission is no longer in the expected state. Refresh and try again.',
        );
      } else {
        setErrorMessage(
          e.message ?? 'An unexpected error occurred. Please try again.',
        );
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  // Convert ScanResultSummary to ScanResult for the existing ScanFindingsView
  const scanResultForView: ScanResult | null = scanResult
    ? {
        verdict: scanResult.verdict as ScanResult['verdict'],
        effective_severity: scanResult.effective_severity as ScanResult['effective_severity'],
        findings: (scanResult.findings ?? []) as ScanResult['findings'],
        build_log_uri: scanResult.build_log_uri,
        defender_timed_out: scanResult.defender_timed_out,
      }
    : null;

  return (
    <div className="space-y-6">
      {/* ── Submission metadata ── */}
      <section aria-label="Submission details">
        <h2 className="text-xl font-semibold text-gray-900 mb-3">
          Submission: {submission.slug}
        </h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <dt className="font-medium text-gray-600">Status</dt>
          <dd className="text-gray-900">{submission.status}</dd>
          <dt className="font-medium text-gray-600">Repo</dt>
          <dd className="text-gray-900 truncate">
            <a
              href={submission.github_repo_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline"
            >
              {submission.github_repo_url}
            </a>
          </dd>
          <dt className="font-medium text-gray-600">Git ref</dt>
          <dd className="text-gray-900 font-mono text-xs">{submission.git_ref}</dd>
          <dt className="font-medium text-gray-600">Dockerfile</dt>
          <dd className="text-gray-900 font-mono text-xs">{submission.dockerfile_path}</dd>
          <dt className="font-medium text-gray-600">PI</dt>
          <dd className="text-gray-900 font-mono text-xs">{submission.pi_principal_id}</dd>
          <dt className="font-medium text-gray-600">Department</dt>
          <dd className="text-gray-900">{submission.dept_fallback}</dd>
        </dl>
      </section>

      {/* ── Defender badge (AC5.4) ── */}
      {scanResult?.defender_timed_out && (
        <div
          aria-label="Defender scan pending badge"
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-yellow-100 text-yellow-800 border border-yellow-300 text-sm font-medium"
        >
          <span>⚠</span>
          <span>Defender scan pending</span>
        </div>
      )}

      {/* ── Scan results ── */}
      <section aria-label="Scan results section">
        <h3 className="text-base font-semibold text-gray-800 mb-3">Scan Results</h3>
        <ScanFindingsView scanResult={scanResultForView} />
      </section>

      {/* ── Detection nudges ── */}
      <section aria-label="Detection findings section">
        <NudgesPanel submissionId={submission.id} />
      </section>

      {/* ── Approval actions ── */}
      <section aria-label="Approval actions">
        <h3 className="text-base font-semibold text-gray-800 mb-3">Decision</h3>
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => openDialog('approve')}
            className="px-4 py-2 text-sm rounded-md bg-green-600 text-white hover:bg-green-700"
          >
            Approve
          </button>
          <button
            type="button"
            onClick={() => openDialog('reject')}
            className="px-4 py-2 text-sm rounded-md bg-red-600 text-white hover:bg-red-700"
          >
            Reject
          </button>
          <button
            type="button"
            onClick={() => openDialog('request_changes')}
            className="px-4 py-2 text-sm rounded-md bg-yellow-600 text-white hover:bg-yellow-700"
          >
            Request Changes
          </button>
        </div>
      </section>

      {/* ── Decision dialog ── */}
      {activeDecision && (
        <DecisionDialog
          decision={activeDecision}
          onConfirm={handleConfirm}
          onCancel={closeDialog}
          isSubmitting={isSubmitting}
          errorMessage={errorMessage}
        />
      )}
    </div>
  );
}
