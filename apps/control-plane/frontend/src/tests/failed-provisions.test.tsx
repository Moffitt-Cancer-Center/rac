import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FailedProvisionsList } from '@/features/admin/provisioning/failed-provisions';
import '@testing-library/jest-dom';

// Mock MSAL so no real token acquisition happens
vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const row1 = {
  submission_id: 'sub-aaa-1',
  slug: 'cool-app',
  pi_principal_id: 'pi-uuid-1',
  last_failure_reason: 'aca_transient: 503 Service Unavailable',
  failed_at: '2026-04-23T10:00:00Z',
  retry_count: 2,
};

const row2 = {
  submission_id: 'sub-bbb-2',
  slug: 'other-app',
  pi_principal_id: 'pi-uuid-2',
  last_failure_reason: 'dns_conflict: A record already exists',
  failed_at: '2026-04-23T11:00:00Z',
  retry_count: 1,
};

// The API base is resolved as window.location.origin + /api in jsdom
const baseUrl = 'http://localhost:3000/api';

const handlers = [
  http.get(`${baseUrl}/admin/submissions/failed-provisions`, () =>
    HttpResponse.json([row1, row2])
  ),
  http.post(`${baseUrl}/admin/submissions/:id/provisioning/retry`, ({ params }) => {
    if (params.id === 'sub-aaa-1') {
      return HttpResponse.json({ submission_id: 'sub-aaa-1', success: true, error_code: null, error_detail: null });
    }
    return HttpResponse.json({ submission_id: params.id, success: false, error_code: 'aca_error', error_detail: 'something went wrong' }, { status: 500 });
  }),
];

const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('FailedProvisionsList', () => {
  it('renders both failed rows', async () => {
    renderWithClient(<FailedProvisionsList />);

    await waitFor(() => {
      expect(screen.getByText('cool-app')).toBeInTheDocument();
      expect(screen.getByText('other-app')).toBeInTheDocument();
    });
  });

  it('shows failure reason for each row', async () => {
    renderWithClient(<FailedProvisionsList />);

    await waitFor(() => {
      expect(screen.getByText(/aca_transient/i)).toBeInTheDocument();
      expect(screen.getByText(/dns_conflict/i)).toBeInTheDocument();
    });
  });

  it('click retry makes API call with correct submission ID', async () => {
    const user = userEvent.setup();
    const retryRequests: string[] = [];

    server.use(
      http.post(`${baseUrl}/admin/submissions/:id/provisioning/retry`, ({ params }) => {
        retryRequests.push(params.id as string);
        return HttpResponse.json({
          submission_id: params.id,
          success: true,
          error_code: null,
          error_detail: null,
        });
      })
    );

    renderWithClient(<FailedProvisionsList />);

    // Wait for table to render
    const retryBtn = await screen.findByRole('button', {
      name: /retry provisioning for cool-app/i,
    });

    await user.click(retryBtn);

    await waitFor(() => {
      expect(retryRequests).toContain('sub-aaa-1');
    });
  });

  it('button is disabled while retry is pending', async () => {
    let resolveRetry!: () => void;

    server.use(
      http.post(`${baseUrl}/admin/submissions/:id/provisioning/retry`, async () => {
        await new Promise<void>((resolve) => { resolveRetry = resolve; });
        return HttpResponse.json({
          submission_id: 'sub-aaa-1',
          success: true,
          error_code: null,
          error_detail: null,
        });
      })
    );

    const user = userEvent.setup();
    renderWithClient(<FailedProvisionsList />);

    const retryBtn = await screen.findByRole('button', {
      name: /retry provisioning for cool-app/i,
    });

    void user.click(retryBtn);

    // Button should be disabled during the pending state
    await waitFor(() => {
      expect(retryBtn).toBeDisabled();
    });

    // Resolve the pending request
    resolveRetry();
  });

  it('shows inline error message on 500 response', async () => {
    server.use(
      http.get(`${baseUrl}/admin/submissions/failed-provisions`, () =>
        HttpResponse.json([row1])
      ),
      http.post(`${baseUrl}/admin/submissions/sub-aaa-1/provisioning/retry`, () =>
        HttpResponse.json({ message: 'Internal Server Error' }, { status: 500 })
      )
    );

    const user = userEvent.setup();
    renderWithClient(<FailedProvisionsList />);

    const retryBtn = await screen.findByRole('button', {
      name: /retry provisioning for cool-app/i,
    });
    await user.click(retryBtn);

    await waitFor(() => {
      // The error alert should appear
      const alerts = screen.getAllByRole('alert');
      const errorAlerts = alerts.filter(
        (el) => el.textContent && el.textContent.length > 0
      );
      expect(errorAlerts.length).toBeGreaterThan(0);
    });
  });

  it('shows empty state when no failed provisions', async () => {
    server.use(
      http.get(`${baseUrl}/admin/submissions/failed-provisions`, () =>
        HttpResponse.json([])
      )
    );

    renderWithClient(<FailedProvisionsList />);

    await waitFor(() => {
      expect(screen.getByText(/no failed provisions/i)).toBeInTheDocument();
    });
  });
});
