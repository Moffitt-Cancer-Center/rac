import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FlagsPanel } from '@/features/admin/ownership/flags-panel';
import type { OwnershipFlag } from '@/features/admin/ownership/api';
import '@testing-library/jest-dom';

// ─── Mock MSAL ─────────────────────────────────────────────────────────────────

vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const flag1: OwnershipFlag = {
  flag_id: 'flag-aaa-001',
  app_id: 'app-aaa-001',
  app_slug: 'cool-app',
  pi_principal_id: 'pi-uuid-001',
  pi_display_name: 'Dr. Smith',
  reason: 'account_disabled',
  flagged_at: '2026-04-20T10:00:00Z',
};

const flag2: OwnershipFlag = {
  flag_id: 'flag-bbb-002',
  app_id: 'app-bbb-002',
  app_slug: 'ml-pipeline',
  pi_principal_id: 'pi-uuid-002',
  pi_display_name: null,
  reason: 'not_found',
  flagged_at: '2026-04-21T11:00:00Z',
};

const baseUrl = 'http://localhost:3000/api';

// ─── MSW server ───────────────────────────────────────────────────────────────

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('FlagsPanel — list view', () => {
  it('renders both flags with reason badges', async () => {
    server.use(
      http.get(`${baseUrl}/admin/ownership/flags`, () =>
        HttpResponse.json([flag1, flag2])
      )
    );

    renderWithClient(<FlagsPanel />);

    await waitFor(() => {
      expect(screen.getByText('cool-app')).toBeInTheDocument();
      expect(screen.getByText('ml-pipeline')).toBeInTheDocument();
    });

    // Reason badges
    expect(screen.getByLabelText('reason: account_disabled')).toBeInTheDocument();
    expect(screen.getByLabelText('reason: not_found')).toBeInTheDocument();

    // PI display name shown when available
    expect(screen.getByText('Dr. Smith')).toBeInTheDocument();

    // Raw principal ID shown when no display name
    expect(screen.getByText('pi-uuid-002')).toBeInTheDocument();
  });

  it('shows empty state when no open flags', async () => {
    server.use(
      http.get(`${baseUrl}/admin/ownership/flags`, () => HttpResponse.json([]))
    );

    renderWithClient(<FlagsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/no open ownership flags/i)).toBeInTheDocument();
    });
  });
});

describe('FlagsPanel — transfer dialog', () => {
  it('clicking Transfer opens dialog; submitting calls transfer API and closes', async () => {
    const transferRequests: { appId: string; body: unknown }[] = [];

    server.use(
      http.get(`${baseUrl}/admin/ownership/flags`, () =>
        HttpResponse.json([flag1])
      ),
      http.post(
        `${baseUrl}/admin/apps/${flag1.app_id}/ownership/transfer`,
        async ({ params, request }) => {
          transferRequests.push({
            appId: params.appId as string ?? flag1.app_id,
            body: await request.json(),
          });
          return HttpResponse.json({
            id: flag1.app_id,
            slug: flag1.app_slug,
            pi_principal_id: 'new-pi-uuid',
            dept_fallback: 'Genomics',
          });
        }
      )
    );

    const user = userEvent.setup();
    renderWithClient(<FlagsPanel />);

    // Wait for flag row to appear
    const transferBtn = await screen.findByRole('button', { name: /transfer ownership/i });
    await user.click(transferBtn);

    // Dialog should open
    const dialog = screen.getByRole('dialog', { name: /transfer ownership of cool-app/i });
    expect(dialog).toBeInTheDocument();

    // Fill in form
    await user.type(screen.getByLabelText(/new pi uuid/i), 'new-pi-uuid');
    await user.type(screen.getByLabelText(/new department/i), 'Genomics');
    await user.type(screen.getByLabelText(/justification/i), 'PI left the institution');

    // Submit
    await user.click(screen.getByRole('button', { name: /^transfer$/i }));

    // Dialog closes after success
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });

    expect(transferRequests).toHaveLength(1);
    const body = transferRequests[0]!.body as Record<string, string>;
    expect(body['new_pi_principal_id']).toBe('new-pi-uuid');
    expect(body['new_dept_fallback']).toBe('Genomics');
    expect(body['justification']).toBe('PI left the institution');
  });
});
