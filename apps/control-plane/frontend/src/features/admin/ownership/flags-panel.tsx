// pattern: Imperative Shell — fetches flags, renders table with transfer action
/**
 * FlagsPanel: Admin table of open ownership flags.
 *
 * Shows PI name (from Graph), app slug, flag reason, and a "Transfer ownership"
 * button that opens the TransferDialog form.
 *
 * Verifies: rac-v1.AC9.2 (UI), rac-v1.AC9.3 (UI)
 */

import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { listFlags, transferOwnership, type OwnershipFlag } from './api';
import { TransferDialog } from './transfer-dialog';

// ─── Reason badge ──────────────────────────────────────────────────────────────

function ReasonBadge({ reason }: { reason: OwnershipFlag['reason'] }) {
  const config = {
    account_disabled: {
      label: 'Account Disabled',
      cls: 'bg-orange-100 text-orange-800',
    },
    not_found: {
      label: 'PI Not Found',
      cls: 'bg-red-100 text-red-800',
    },
  };

  const { label, cls } = config[reason] ?? {
    label: reason,
    cls: 'bg-gray-100 text-gray-800',
  };

  return (
    <span
      aria-label={`reason: ${reason}`}
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`}
    >
      {label}
    </span>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

export function FlagsPanel() {
  const queryClient = useQueryClient();
  const idempotencyKeyRef = useRef<string>('');

  const [dialogApp, setDialogApp] = useState<OwnershipFlag | null>(null);
  const [isTransferring, setIsTransferring] = useState(false);
  const [transferError, setTransferError] = useState<string | null>(null);

  const { data: flags, isLoading, error } = useQuery({
    queryKey: ['ownership-flags'],
    queryFn: listFlags,
    retry: 1,
  });

  function openTransferDialog(flag: OwnershipFlag) {
    idempotencyKeyRef.current = crypto.randomUUID();
    setDialogApp(flag);
    setTransferError(null);
  }

  function closeTransferDialog() {
    setDialogApp(null);
    setTransferError(null);
  }

  async function handleTransfer(values: {
    newPi: string;
    newDept: string;
    justification: string;
  }) {
    if (!dialogApp) return;

    setIsTransferring(true);
    setTransferError(null);

    try {
      await transferOwnership(
        dialogApp.app_id,
        {
          new_pi_principal_id: values.newPi,
          new_dept_fallback: values.newDept,
          justification: values.justification,
        },
        idempotencyKeyRef.current,
      );

      // Refresh flags list — the transferred flag will have a review row now
      await queryClient.invalidateQueries({ queryKey: ['ownership-flags'] });
      closeTransferDialog();
    } catch (err: unknown) {
      const e = err as { message?: string };
      setTransferError(e.message ?? 'Transfer failed. Please try again.');
    } finally {
      setIsTransferring(false);
    }
  }

  // ─── Render states ─────────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="text-sm text-gray-600" aria-live="polite">
        Loading ownership flags…
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" className="rounded-md bg-red-50 p-4 text-sm text-red-800">
        {error instanceof Error ? error.message : 'Failed to load ownership flags'}
      </div>
    );
  }

  if (!flags || flags.length === 0) {
    return (
      <div className="text-sm text-gray-600">
        No open ownership flags.
      </div>
    );
  }

  return (
    <>
      <div className="overflow-x-auto rounded-md border border-gray-200">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left font-semibold text-gray-600">App slug</th>
              <th className="px-4 py-3 text-left font-semibold text-gray-600">PI</th>
              <th className="px-4 py-3 text-left font-semibold text-gray-600">Reason</th>
              <th className="px-4 py-3 text-left font-semibold text-gray-600">Flagged at</th>
              <th className="px-4 py-3 text-left font-semibold text-gray-600">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {flags.map((flag) => (
              <tr key={flag.flag_id} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-mono text-xs">{flag.app_slug}</td>
                <td className="px-4 py-3 text-xs">
                  {flag.pi_display_name ?? (
                    <span className="font-mono text-gray-500">{flag.pi_principal_id}</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <ReasonBadge reason={flag.reason} />
                </td>
                <td className="px-4 py-3 text-gray-500">
                  {new Date(flag.flagged_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => openTransferDialog(flag)}
                    className="px-3 py-1 text-xs rounded-md bg-blue-600 text-white hover:bg-blue-700"
                  >
                    Transfer ownership
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {dialogApp && (
        <TransferDialog
          appSlug={dialogApp.app_slug}
          onConfirm={handleTransfer}
          onCancel={closeTransferDialog}
          isSubmitting={isTransferring}
          errorMessage={transferError}
        />
      )}
    </>
  );
}
