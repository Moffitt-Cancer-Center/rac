// pattern: Functional Core — pure render based on props + local state; no external side effects.
/**
 * WebhookSubscriptionsAdmin: Full CRUD UI for outbound webhook subscriptions.
 *
 * - Lists all subscriptions in a table (name, url, enabled, consecutive_failures, last_delivery_at).
 * - "New Subscription" button opens a modal form.
 * - After creation, shows a one-shot secret display (copy + dismiss).
 * - PATCH (enable/disable/reset-failures) via row-level controls.
 * - DELETE with confirmation via row-level button.
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listWebhookSubscriptions,
  createWebhookSubscription,
  updateWebhookSubscription,
  deleteWebhookSubscription,
} from '@/lib/webhook-subscriptions-api';
import type {
  WebhookSubscriptionResponse,
  WebhookSubscriptionCreate,
} from '@/lib/webhook-subscriptions-api';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

// ─── One-shot secret display ─────────────────────────────────────────────────

interface SecretRevealProps {
  name: string;
  secret: string;
  onDismiss: () => void;
}

function SecretRevealPanel({ name, secret, onDismiss }: SecretRevealProps) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(secret);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div
      role="alert"
      aria-label="one-time secret"
      className="rounded-md border border-yellow-300 bg-yellow-50 p-4 space-y-3"
    >
      <p className="font-semibold text-yellow-800">
        Webhook secret for &ldquo;{name}&rdquo; — copy it now, it will not be shown again.
      </p>
      <div className="flex items-center gap-2">
        <code className="flex-1 rounded bg-yellow-100 px-3 py-2 font-mono text-sm break-all">
          {secret}
        </code>
        <button
          type="button"
          onClick={handleCopy}
          className="rounded bg-yellow-600 px-3 py-2 text-sm text-white hover:bg-yellow-700"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="text-sm text-yellow-700 underline hover:no-underline"
      >
        I have saved the secret — dismiss
      </button>
    </div>
  );
}

// ─── Create modal ─────────────────────────────────────────────────────────────

interface CreateModalProps {
  onClose: () => void;
  onCreated: (sub: WebhookSubscriptionResponse, secret: string) => void;
}

const ALL_EVENT_TYPES = [
  'submission.scan_completed',
  'submission.approved',
  'submission.rejected',
  'submission.deployed',
];

function CreateModal({ onClose, onCreated }: CreateModalProps) {
  const [name, setName] = useState('');
  const [callbackUrl, setCallbackUrl] = useState('');
  const [selectedEvents, setSelectedEvents] = useState<string[]>([
    'submission.scan_completed',
  ]);
  const [error, setError] = useState<string | null>(null);

  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (data: WebhookSubscriptionCreate) =>
      createWebhookSubscription(data, { idempotencyKey: crypto.randomUUID() }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['webhook-subscriptions'] });
      onCreated(res, res.secret);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : 'Creation failed');
    },
  });

  function toggleEvent(evt: string) {
    setSelectedEvents((prev) =>
      prev.includes(evt) ? prev.filter((e) => e !== evt) : [...prev, evt]
    );
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    if (!callbackUrl.trim()) {
      setError('Callback URL is required');
      return;
    }
    if (selectedEvents.length === 0) {
      setError('At least one event type is required');
      return;
    }
    setError(null);
    mutation.mutate({ name: name.trim(), callbackUrl: callbackUrl.trim(), eventTypes: selectedEvents });
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Create webhook subscription"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
    >
      <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl space-y-4">
        <h2 className="text-xl font-bold">New Webhook Subscription</h2>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="sub-name" className="block text-sm font-medium text-gray-700">
              Name
            </label>
            <input
              id="sub-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-webhook"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label htmlFor="sub-url" className="block text-sm font-medium text-gray-700">
              Callback URL
            </label>
            <input
              id="sub-url"
              type="url"
              value={callbackUrl}
              onChange={(e) => setCallbackUrl(e.target.value)}
              placeholder="https://example.com/webhook"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
            />
          </div>

          <fieldset>
            <legend className="block text-sm font-medium text-gray-700 mb-2">Event Types</legend>
            <div className="space-y-1">
              {ALL_EVENT_TYPES.map((evt) => (
                <label key={evt} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selectedEvents.includes(evt)}
                    onChange={() => toggleEvent(evt)}
                  />
                  <code>{evt}</code>
                </label>
              ))}
            </div>
          </fieldset>

          {error && (
            <p role="alert" className="text-sm text-red-600">{error}</p>
          )}

          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={mutation.isPending}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {mutation.isPending ? 'Creating…' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Subscription row ─────────────────────────────────────────────────────────

interface SubRowProps {
  sub: WebhookSubscriptionResponse;
  onToggleEnabled: (sub: WebhookSubscriptionResponse) => void;
  onResetFailures: (sub: WebhookSubscriptionResponse) => void;
  onDelete: (sub: WebhookSubscriptionResponse) => void;
}

function SubscriptionRow({ sub, onToggleEnabled, onResetFailures, onDelete }: SubRowProps) {
  return (
    <tr className="hover:bg-gray-50">
      <td className="px-4 py-3 font-medium text-sm">{sub.name}</td>
      <td className="px-4 py-3 font-mono text-xs max-w-xs truncate" title={sub.callbackUrl}>
        {sub.callbackUrl}
      </td>
      <td className="px-4 py-3 text-sm">
        <span
          className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${
            sub.enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'
          }`}
        >
          {sub.enabled ? 'Enabled' : 'Disabled'}
        </span>
      </td>
      <td className="px-4 py-3 text-sm">
        <span
          className={`font-mono ${(sub.consecutiveFailures ?? 0) > 0 ? 'text-red-700 font-bold' : 'text-gray-500'}`}
          aria-label={`failure count: ${sub.consecutiveFailures ?? 0}`}
        >
          {sub.consecutiveFailures ?? 0}
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-gray-600">
        {formatDate(sub.lastDeliveryAt)}
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">
        {sub.eventTypes.join(', ')}
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onToggleEnabled(sub)}
            className="text-xs rounded border border-gray-300 px-2 py-1 hover:bg-gray-100"
            aria-label={sub.enabled ? `disable ${sub.name}` : `enable ${sub.name}`}
          >
            {sub.enabled ? 'Disable' : 'Enable'}
          </button>
          {(sub.consecutiveFailures ?? 0) > 0 && (
            <button
              type="button"
              onClick={() => onResetFailures(sub)}
              className="text-xs rounded border border-yellow-300 px-2 py-1 text-yellow-700 hover:bg-yellow-50"
              aria-label={`reset failures for ${sub.name}`}
            >
              Reset
            </button>
          )}
          <button
            type="button"
            onClick={() => onDelete(sub)}
            className="text-xs rounded border border-red-300 px-2 py-1 text-red-600 hover:bg-red-50"
            aria-label={`delete ${sub.name}`}
          >
            Delete
          </button>
        </div>
      </td>
    </tr>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function WebhookSubscriptionsAdmin() {
  const [showCreate, setShowCreate] = useState(false);
  const [revealSecret, setRevealSecret] = useState<{ name: string; secret: string } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<WebhookSubscriptionResponse | null>(null);

  const queryClient = useQueryClient();

  const { data: subscriptions, isLoading, error } = useQuery({
    queryKey: ['webhook-subscriptions'],
    queryFn: listWebhookSubscriptions,
  });

  const patchMutation = useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Parameters<typeof updateWebhookSubscription>[1];
    }) =>
      updateWebhookSubscription(id, patch, { idempotencyKey: crypto.randomUUID() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhook-subscriptions'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      deleteWebhookSubscription(id, { idempotencyKey: crypto.randomUUID() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhook-subscriptions'] });
      setPendingDelete(null);
    },
  });

  function handleToggleEnabled(sub: WebhookSubscriptionResponse) {
    patchMutation.mutate({ id: sub.id, patch: { enabled: !sub.enabled } });
  }

  function handleResetFailures(sub: WebhookSubscriptionResponse) {
    patchMutation.mutate({ id: sub.id, patch: { resetConsecutiveFailures: true } });
  }

  function handleDeleteConfirm() {
    if (pendingDelete) {
      deleteMutation.mutate(pendingDelete.id);
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Webhook Subscriptions</h2>
        <button
          type="button"
          onClick={() => setShowCreate(true)}
          className="rounded-md bg-blue-600 px-4 py-2 text-white text-sm hover:bg-blue-700"
        >
          New Subscription
        </button>
      </div>

      {/* One-shot secret reveal */}
      {revealSecret && (
        <SecretRevealPanel
          name={revealSecret.name}
          secret={revealSecret.secret}
          onDismiss={() => setRevealSecret(null)}
        />
      )}

      {/* Error state */}
      {error && (
        <div className="rounded-md bg-red-50 p-4">
          <p className="text-sm text-red-800">
            {error instanceof Error ? error.message : 'Failed to load subscriptions'}
          </p>
        </div>
      )}

      {/* Loading state */}
      {isLoading ? (
        <p className="text-gray-600">Loading subscriptions…</p>
      ) : !subscriptions || subscriptions.length === 0 ? (
        <p className="text-gray-600">No webhook subscriptions configured.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-gray-200">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Name</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Callback URL</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Status</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Failures</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Last Delivery</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Events</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {subscriptions.map((sub) => (
                <SubscriptionRow
                  key={sub.id}
                  sub={sub}
                  onToggleEnabled={handleToggleEnabled}
                  onResetFailures={handleResetFailures}
                  onDelete={setPendingDelete}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={(sub, secret) => {
            setShowCreate(false);
            setRevealSecret({ name: sub.name, secret });
          }}
        />
      )}

      {/* Delete confirmation */}
      {pendingDelete && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Confirm delete"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
        >
          <div className="w-full max-w-sm rounded-lg bg-white p-6 shadow-xl space-y-4">
            <h3 className="text-lg font-bold text-red-700">Delete subscription?</h3>
            <p className="text-sm text-gray-700">
              Are you sure you want to delete <strong>{pendingDelete.name}</strong>? This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setPendingDelete(null)}
                className="rounded-md border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteConfirm}
                disabled={deleteMutation.isPending}
                className="rounded-md bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
