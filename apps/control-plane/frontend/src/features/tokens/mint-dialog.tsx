// pattern: Imperative Shell — form for minting reviewer tokens.
import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { mintToken } from './api';
import { OneShotUrlDisplay } from './one-shot-url-display';
import type { TokenCreateResponse } from './types';

type MintDialogProps = {
  appId: string;
  onClose: () => void;
};

const TTL_OPTIONS = [7, 30, 90, 180] as const;

export function MintDialog({ appId, onClose }: MintDialogProps) {
  const [reviewerLabel, setReviewerLabel] = useState('');
  const [ttlDays, setTtlDays] = useState<number>(30);
  const [issued, setIssued] = useState<TokenCreateResponse | null>(null);
  const idempotencyKey = useRef(crypto.randomUUID());
  const queryClient = useQueryClient();

  const { mutate, isPending, error } = useMutation({
    mutationFn: () =>
      mintToken(appId, { reviewerLabel, ttlDays }, idempotencyKey.current),
    onSuccess: (data) => {
      setIssued(data);
      void queryClient.invalidateQueries({ queryKey: ['tokens', appId] });
    },
    onError: (err) => {
      // Surface errors for debugging (remove in production if noisy)
      console.error('mintToken error:', err);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    mutate();
  }

  if (issued) {
    return (
      <div className="space-y-4">
        <h3 className="text-lg font-semibold text-gray-900">Token Minted</h3>
        <p className="text-sm text-gray-600">
          Reviewer: <span className="font-medium">{issued.reviewerLabel}</span>
          {' | '}
          Expires: {new Date(issued.expiresAt).toLocaleDateString()}
        </p>
        <OneShotUrlDisplay visitUrl={issued.visitUrl} />
        <button
          type="button"
          onClick={onClose}
          className="mt-4 w-full border border-gray-300 rounded py-2 px-4 text-sm hover:bg-gray-50"
        >
          Close
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h3 className="text-lg font-semibold text-gray-900">Mint Reviewer Token</h3>

      <div>
        <label htmlFor="reviewer-label" className="block text-sm font-medium text-gray-700">
          Reviewer label
        </label>
        <input
          id="reviewer-label"
          type="text"
          value={reviewerLabel}
          onChange={(e) => setReviewerLabel(e.target.value)}
          placeholder="e.g. Journal Reviewer #1"
          required
          minLength={1}
          maxLength={100}
          className="mt-1 block w-full border border-gray-300 rounded px-3 py-2 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      <div>
        <label htmlFor="ttl-days" className="block text-sm font-medium text-gray-700">
          Token valid for
        </label>
        <select
          id="ttl-days"
          value={ttlDays}
          onChange={(e) => setTtlDays(Number(e.target.value))}
          className="mt-1 block w-full border border-gray-300 rounded px-3 py-2 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {TTL_OPTIONS.map((d) => (
            <option key={d} value={d}>
              {d} days
            </option>
          ))}
        </select>
      </div>

      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error.message}
        </p>
      )}

      <div className="flex gap-3 pt-2">
        <button
          type="submit"
          disabled={isPending || reviewerLabel.trim().length === 0}
          className="flex-1 bg-blue-600 text-white py-2 px-4 rounded text-sm font-semibold
                     hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isPending ? 'Minting…' : 'Mint Token'}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="flex-1 border border-gray-300 rounded py-2 px-4 text-sm hover:bg-gray-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
