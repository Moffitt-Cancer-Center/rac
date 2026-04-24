// pattern: Imperative Shell — table of reviewer tokens for an app.
import { useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listTokens, revokeToken } from './api';
import type { TokenListItem } from './types';

type TokensTableProps = {
  appId: string;
};

function statusBadge(item: TokenListItem) {
  if (item.revokedAt) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-800">
        Revoked
      </span>
    );
  }
  if (new Date(item.expiresAt) < new Date()) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">
        Expired
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">
      Active
    </span>
  );
}

function RevokeButton({ appId, jti }: { appId: string; jti: string }) {
  const queryClient = useQueryClient();
  const idempotencyKey = useRef(crypto.randomUUID());

  const { mutate, isPending } = useMutation({
    mutationFn: () => revokeToken(appId, jti, idempotencyKey.current),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['tokens', appId] });
    },
  });

  return (
    <button
      type="button"
      onClick={() => mutate()}
      disabled={isPending}
      className="text-xs text-red-600 hover:text-red-800 disabled:opacity-50"
    >
      {isPending ? 'Revoking…' : 'Revoke'}
    </button>
  );
}

export function TokensTable({ appId }: TokensTableProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['tokens', appId],
    queryFn: () => listTokens(appId, true),
  });

  if (isLoading) {
    return <p className="text-sm text-gray-500">Loading tokens…</p>;
  }

  if (error) {
    return (
      <p className="text-sm text-red-600" role="alert">
        Failed to load tokens: {(error as Error).message}
      </p>
    );
  }

  const items = data?.items ?? [];

  if (items.length === 0) {
    return <p className="text-sm text-gray-500">No tokens issued yet.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-4 py-2 text-left font-medium text-gray-600">Label</th>
            <th className="px-4 py-2 text-left font-medium text-gray-600">Issued</th>
            <th className="px-4 py-2 text-left font-medium text-gray-600">Expires</th>
            <th className="px-4 py-2 text-left font-medium text-gray-600">Status</th>
            <th className="px-4 py-2 text-left font-medium text-gray-600">Issued by</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {items.map((item) => (
            <tr key={item.jti}>
              <td className="px-4 py-2 font-medium">{item.reviewerLabel ?? '—'}</td>
              <td className="px-4 py-2 text-gray-500">
                {new Date(item.issuedAt).toLocaleDateString()}
              </td>
              <td className="px-4 py-2 text-gray-500">
                {new Date(item.expiresAt).toLocaleDateString()}
              </td>
              <td className="px-4 py-2">{statusBadge(item)}</td>
              <td className="px-4 py-2 text-gray-500 font-mono text-xs">
                {item.issuedByPrincipalId?.slice(0, 8) ?? '—'}
              </td>
              <td className="px-4 py-2 text-right">
                {!item.revokedAt && (
                  <RevokeButton appId={appId} jti={item.jti} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
