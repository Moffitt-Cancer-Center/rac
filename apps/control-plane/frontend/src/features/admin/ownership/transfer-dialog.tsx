// pattern: Functional Core (pure render based on props + controlled callbacks)
/**
 * TransferDialog: form dialog for transferring app ownership to a new PI.
 *
 * Fields: new PI UUID, new dept, justification.
 *
 * Verifies: rac-v1.AC9.3 (UI)
 */

import { useState } from 'react';

// ─── Props ─────────────────────────────────────────────────────────────────────

interface TransferDialogProps {
  appSlug: string;
  onConfirm: (values: {
    newPi: string;
    newDept: string;
    justification: string;
  }) => void;
  onCancel: () => void;
  isSubmitting: boolean;
  errorMessage: string | null;
}

// ─── Component ─────────────────────────────────────────────────────────────────

export function TransferDialog({
  appSlug,
  onConfirm,
  onCancel,
  isSubmitting,
  errorMessage,
}: TransferDialogProps) {
  const [newPi, setNewPi] = useState('');
  const [newDept, setNewDept] = useState('');
  const [justification, setJustification] = useState('');

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onConfirm({ newPi, newDept, justification });
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Transfer ownership of ${appSlug}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
    >
      <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-lg">
        <h2 className="text-lg font-semibold mb-1">Transfer Ownership</h2>
        <p className="text-sm text-gray-600 mb-4">
          App: <span className="font-mono font-medium">{appSlug}</span>
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="new-pi-uuid"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              New PI UUID
            </label>
            <input
              id="new-pi-uuid"
              type="text"
              value={newPi}
              onChange={(e) => setNewPi(e.target.value)}
              required
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label
              htmlFor="new-dept"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              New Department
            </label>
            <input
              id="new-dept"
              type="text"
              value={newDept}
              onChange={(e) => setNewDept(e.target.value)}
              required
              placeholder="e.g. Bioinformatics"
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label
              htmlFor="justification"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Justification
            </label>
            <textarea
              id="justification"
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              required
              rows={3}
              placeholder="Reason for ownership transfer…"
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {errorMessage && (
            <div
              role="alert"
              className="p-3 rounded-md bg-red-50 text-red-800 text-sm"
            >
              {errorMessage}
            </div>
          )}

          <div className="flex gap-3 justify-end pt-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={isSubmitting}
              className="px-4 py-2 text-sm rounded-md border border-gray-300 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-4 py-2 text-sm rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {isSubmitting ? 'Transferring…' : 'Transfer'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
