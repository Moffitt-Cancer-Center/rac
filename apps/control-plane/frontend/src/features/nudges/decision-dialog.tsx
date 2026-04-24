// pattern: Functional Core — pure dialog component; no direct API calls.
import { useState } from 'react';
import type { Decision } from './types';

// ─── Props ─────────────────────────────────────────────────────────────────────

type DecisionDialogProps = {
  open: boolean;
  onClose: () => void;
  decision: Decision;
  /** Called on confirm. Receives the notes string (undefined if blank). */
  onConfirm: (notes: string | undefined) => Promise<void>;
};

// ─── Helpers ───────────────────────────────────────────────────────────────────

const DECISION_LABEL: Record<Decision, string> = {
  accept: 'Accept',
  override: 'Override',
  auto_fix: 'Apply auto-fix',
  dismiss: 'Dismiss',
};

// ─── Component ─────────────────────────────────────────────────────────────────

/**
 * DecisionDialog — modal dialog that collects optional notes before confirming
 * a finding decision. The dialog stays open on API error and shows the message.
 *
 * Idempotency: the caller (NudgeCard) generates a per-intent UUID and hands it
 * into onConfirm via the API layer. This component is unaware of that detail.
 */
export function DecisionDialog({ open, onClose, decision, onConfirm }: DecisionDialogProps) {
  const [notes, setNotes] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function handleConfirm() {
    setPending(true);
    setError(null);
    try {
      await onConfirm(notes.trim() || undefined);
      // parent closes the dialog on success by toggling `open`
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setPending(false);
    }
  }

  function handleCancel() {
    if (!pending) {
      setNotes('');
      setError(null);
      onClose();
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Confirm ${DECISION_LABEL[decision]}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
    >
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl space-y-4">
        <h2 className="text-xl font-bold">
          Confirm {DECISION_LABEL[decision]}?
        </h2>

        <div>
          <label
            htmlFor="decision-notes"
            className="block text-sm font-medium text-gray-700 mb-1"
          >
            Optional notes
          </label>
          <textarea
            id="decision-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            disabled={pending}
            placeholder="Add context or rationale (optional)"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm disabled:opacity-50 resize-none"
          />
        </div>

        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={handleCancel}
            disabled={pending}
            className="rounded-md border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={pending}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {pending ? 'Confirming…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  );
}
