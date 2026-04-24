// pattern: Imperative Shell — access mode toggle card for admins.
import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { setAccessMode } from './api';

type ToggleCardProps = {
  appId: string;
  currentMode: 'token_required' | 'public';
};

const MIN_NOTES_LEN = 10;

export function AccessModeToggleCard({ appId, currentMode }: ToggleCardProps) {
  const [mode, setMode] = useState<'token_required' | 'public'>(currentMode);
  const [notes, setNotes] = useState('');
  const [showConfirm, setShowConfirm] = useState(false);
  const idempotencyKey = useRef(crypto.randomUUID());
  const queryClient = useQueryClient();

  const { mutate, isPending, error, isSuccess } = useMutation({
    mutationFn: () =>
      setAccessMode(appId, { mode, notes }, idempotencyKey.current),
    onSuccess: () => {
      // Refresh app data
      void queryClient.invalidateQueries({ queryKey: ['app', appId] });
      setShowConfirm(false);
      // Reset idempotency key for next submission
      idempotencyKey.current = crypto.randomUUID();
    },
  });

  const notesValid = notes.trim().length >= MIN_NOTES_LEN;
  const canSubmit = notesValid && !isPending;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    if (mode === 'public') {
      setShowConfirm(true);
    } else {
      mutate();
    }
  }

  function handleConfirm() {
    mutate();
  }

  function handleCancelConfirm() {
    setShowConfirm(false);
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 space-y-4">
      <h3 className="text-lg font-semibold text-gray-900">Access Mode</h3>

      {isSuccess && (
        <p className="text-sm text-green-600" role="status">
          Access mode updated successfully.
        </p>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <fieldset>
          <legend className="text-sm font-medium text-gray-700 mb-2">Mode</legend>
          <div className="space-y-2">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="access-mode"
                value="token_required"
                checked={mode === 'token_required'}
                onChange={() => setMode('token_required')}
              />
              <span className="text-sm">
                <span className="font-medium">Token required</span>
                {' '}— reviewers need a valid token URL
              </span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="access-mode"
                value="public"
                checked={mode === 'public'}
                onChange={() => setMode('public')}
              />
              <span className="text-sm">
                <span className="font-medium">Public</span>
                {' '}— anyone can access without a token
              </span>
            </label>
          </div>
        </fieldset>

        <div>
          <label htmlFor="access-mode-notes" className="block text-sm font-medium text-gray-700">
            Reason / notes{' '}
            <span className="text-gray-400">
              ({notes.trim().length}/{MIN_NOTES_LEN} min)
            </span>
          </label>
          <textarea
            id="access-mode-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            placeholder="Describe why you are changing the access mode (min 10 characters)"
            className="mt-1 block w-full border border-gray-300 rounded px-3 py-2 text-sm
                       focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        {error && (
          <p className="text-sm text-red-600" role="alert">
            {(error as Error).message}
          </p>
        )}

        <button
          type="submit"
          disabled={!canSubmit}
          className="bg-blue-600 text-white py-2 px-4 rounded text-sm font-semibold
                     hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isPending ? 'Saving…' : 'Update access mode'}
        </button>
      </form>

      {/* Confirmation dialog for switching to public */}
      {showConfirm && (
        <div
          className="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center z-50"
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-dialog-title"
        >
          <div className="bg-white rounded-lg shadow-xl p-6 max-w-md w-full mx-4 space-y-4">
            <h4 id="confirm-dialog-title" className="text-lg font-semibold text-gray-900">
              Make app publicly accessible?
            </h4>
            <p className="text-sm text-gray-600">
              Are you sure? This makes the app publicly accessible without any
              reviewer token. Anyone with the app URL can view it.
            </p>
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={handleConfirm}
                disabled={isPending}
                className="flex-1 bg-red-600 text-white py-2 px-4 rounded text-sm font-semibold
                           hover:bg-red-700 disabled:opacity-50"
              >
                {isPending ? 'Saving…' : 'Confirm — make public'}
              </button>
              <button
                type="button"
                onClick={handleCancelConfirm}
                className="flex-1 border border-gray-300 rounded py-2 px-4 text-sm hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
