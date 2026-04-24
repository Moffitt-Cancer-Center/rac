// pattern: Functional Core — pure card component; delegates API calls via onDecision.
import { useRef, useState } from 'react';
import { DecisionDialog } from './decision-dialog';
import type { FindingWithDecision, Decision } from './types';

// ─── Props ─────────────────────────────────────────────────────────────────────

type NudgeCardProps = {
  finding: FindingWithDecision;
  /**
   * Called when user confirms a decision.
   * Receives decision, optional notes, and the per-intent idempotency key
   * (generated inside this component via useRef).
   */
  onDecision: (decision: Decision, notes: string | undefined, idempotencyKey: string) => Promise<void>;
};

// ─── Helpers ───────────────────────────────────────────────────────────────────

const SEVERITY_BADGE: Record<string, string> = {
  error: 'bg-red-100 text-red-800 border border-red-300',
  warn: 'bg-amber-100 text-amber-800 border border-amber-300',
  info: 'bg-blue-100 text-blue-800 border border-blue-300',
};

function formatLineRanges(ranges: Array<[number, number]>): string {
  return ranges
    .map(([start, end]) => (start === end ? `L${start}` : `L${start}-${end}`))
    .join(', ');
}

function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// ─── Component ─────────────────────────────────────────────────────────────────

/**
 * NudgeCard — renders one detection finding with:
 *   - Severity badge (red/amber/blue)
 *   - Title, optional file path + line ranges
 *   - Detail text (plain text; no markdown parsing)
 *   - If decided: shows decision summary, buttons disabled
 *   - If undecided: shows action bar (Accept / Override / Apply auto-fix / Dismiss)
 *
 * Idempotency key: one UUID is generated per card instance via useRef so that a
 * user's decision attempt reuses the same key until the card re-renders (e.g. after
 * query invalidation creates a fresh card with a new key).
 */
export function NudgeCard({ finding, onDecision }: NudgeCardProps) {
  // One idempotency key per card render; regenerated when the card unmounts+remounts.
  const idempotencyKeyRef = useRef(crypto.randomUUID());

  const [dialogDecision, setDialogDecision] = useState<Decision | null>(null);

  const badgeCls = SEVERITY_BADGE[finding.severity] ?? SEVERITY_BADGE['info'];

  async function handleConfirm(notes: string | undefined) {
    if (!dialogDecision) return;
    // Pass the per-intent key up to the panel/caller for the API call.
    await onDecision(dialogDecision, notes, idempotencyKeyRef.current);
    // Close dialog: parent (NudgesPanel) invalidates query → card unmounts → fresh key.
    setDialogDecision(null);
    // Regenerate key for the (unlikely) same-card retry scenario.
    idempotencyKeyRef.current = crypto.randomUUID();
  }

  return (
    <div className="rounded-md border border-gray-200 bg-white p-4 space-y-3">
      {/* Header row: badge + title */}
      <div className="flex items-start gap-3">
        <span
          className={`inline-block flex-shrink-0 rounded px-2 py-0.5 text-xs font-bold uppercase ${badgeCls}`}
          aria-label={`severity: ${finding.severity}`}
        >
          {finding.severity.toUpperCase()}
        </span>
        <h4 className="text-sm font-semibold text-gray-900 leading-snug">{finding.title}</h4>
      </div>

      {/* File path + line ranges */}
      {(finding.filePath || (finding.lineRanges && finding.lineRanges.length > 0)) && (
        <div className="flex items-center gap-2 text-xs text-gray-600">
          {finding.filePath && (
            <code className="font-mono bg-gray-100 rounded px-1.5 py-0.5">
              {finding.filePath}
            </code>
          )}
          {finding.lineRanges && finding.lineRanges.length > 0 && (
            <span className="font-mono text-gray-500">
              {formatLineRanges(finding.lineRanges)}
            </span>
          )}
        </div>
      )}

      {/* Detail text */}
      <p className="text-sm text-gray-700 whitespace-pre-wrap">{finding.detail}</p>

      {/* Decision state or action bar */}
      {finding.decision ? (
        <div
          className="rounded bg-gray-50 border border-gray-200 px-3 py-2 text-xs text-gray-600"
          aria-label="decision state"
        >
          Decided:{' '}
          <span className="font-semibold">{finding.decision.decision}</span>
          {' by '}
          <span className="font-mono">{finding.decision.decisionActorPrincipalId}</span>
          {' at '}
          <span>{formatTimestamp(finding.decision.createdAt)}</span>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2" aria-label="action bar">
          <button
            type="button"
            onClick={() => setDialogDecision('accept')}
            className="rounded border border-green-300 bg-green-50 px-3 py-1 text-xs font-medium text-green-700 hover:bg-green-100"
          >
            Accept
          </button>
          <button
            type="button"
            onClick={() => setDialogDecision('override')}
            className="rounded border border-yellow-300 bg-yellow-50 px-3 py-1 text-xs font-medium text-yellow-700 hover:bg-yellow-100"
          >
            Override
          </button>
          {finding.autoFix && (
            <button
              type="button"
              onClick={() => setDialogDecision('auto_fix')}
              className="rounded border border-blue-300 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100"
            >
              Apply auto-fix
            </button>
          )}
          <button
            type="button"
            onClick={() => setDialogDecision('dismiss')}
            className="rounded border border-gray-300 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-600 hover:bg-gray-100"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Decision dialog */}
      {dialogDecision && (
        <DecisionDialog
          open={true}
          onClose={() => setDialogDecision(null)}
          decision={dialogDecision}
          onConfirm={handleConfirm}
        />
      )}
    </div>
  );
}
