import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { WebhookSubscriptionsAdmin } from '@/features/admin/webhook-subscriptions';
import '@testing-library/jest-dom';

// Mock MSAL
vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const sub1 = {
  id: 'sub-1',
  name: 'my-webhook',
  callback_url: 'https://example.com/hook',
  event_types: ['submission.scan_completed'],
  enabled: true,
  consecutive_failures: 0,
  last_delivery_at: '2026-04-23T10:00:00Z',
  secret_rotated_at: null,
  updated_at: '2026-04-23T10:00:00Z',
};

const sub2 = {
  id: 'sub-2',
  name: 'failing-webhook',
  callback_url: 'https://other.com/hook',
  event_types: ['submission.approved'],
  enabled: false,
  consecutive_failures: 7,
  last_delivery_at: null,
  secret_rotated_at: null,
  updated_at: '2026-04-23T09:00:00Z',
};

// In vitest jsdom, window.location.origin is http://localhost:3000
// and VITE_API_BASE_URL is undefined, so adminUrl builds:
// http://localhost:3000/api/admin/webhook-subscriptions
const adminBase = 'http://localhost:3000/api/admin/webhook-subscriptions';

const handlers = [
  http.get(adminBase, () =>
    HttpResponse.json([sub1, sub2])
  ),

  http.post(adminBase, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const created = {
      id: 'sub-new',
      name: body['name'],
      callback_url: body['callback_url'],
      event_types: body['event_types'],
      enabled: true,
      consecutive_failures: 0,
      last_delivery_at: null,
      secret_rotated_at: null,
      updated_at: new Date().toISOString(),
      secret: 'deadbeef1234567890abcdef',
    };
    return HttpResponse.json(created, { status: 201 });
  }),

  http.patch(`${adminBase}/:id`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const updated = {
      ...sub1,
      ...body,
    };
    return HttpResponse.json(updated);
  }),

  http.delete(`${adminBase}/:id`, () =>
    new HttpResponse(null, { status: 204 })
  ),
];

const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderAdmin() {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <WebhookSubscriptionsAdmin />
    </QueryClientProvider>
  );
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('WebhookSubscriptionsAdmin', () => {
  it('renders subscription table with names and status', async () => {
    renderAdmin();

    await waitFor(() => {
      expect(screen.getByText('my-webhook')).toBeDefined();
      expect(screen.getByText('failing-webhook')).toBeDefined();
    });

    // Status badges
    expect(screen.getByText('Enabled')).toBeDefined();
    expect(screen.getByText('Disabled')).toBeDefined();
  });

  it('renders failure counter for subscriptions with failures', async () => {
    renderAdmin();

    await waitFor(() => {
      expect(screen.getByText('failing-webhook')).toBeDefined();
    });

    // Failure count badge for sub2 (7 failures)
    const failureBadge = screen.getByLabelText('failure count: 7');
    expect(failureBadge).toBeDefined();
    expect(failureBadge.textContent).toBe('7');
  });

  it('renders zero failures for healthy subscription', async () => {
    renderAdmin();

    await waitFor(() => {
      expect(screen.getByText('my-webhook')).toBeDefined();
    });

    const zeroBadge = screen.getByLabelText('failure count: 0');
    expect(zeroBadge.textContent).toBe('0');
  });

  it('opens create modal and shows one-shot secret after creation', async () => {
    const user = userEvent.setup();
    renderAdmin();

    await waitFor(() => {
      expect(screen.getByText('my-webhook')).toBeDefined();
    });

    // Open modal
    const newBtn = screen.getByRole('button', { name: /new subscription/i });
    await user.click(newBtn);

    expect(screen.getByRole('dialog', { name: /create webhook subscription/i })).toBeDefined();

    // Fill out form
    await user.type(screen.getByLabelText(/name/i), 'test-hook');
    await user.type(screen.getByLabelText(/callback url/i), 'https://test.com/hook');

    // Submit
    const createBtn = screen.getByRole('button', { name: /^create$/i });
    await user.click(createBtn);

    // Secret reveal panel should appear
    await waitFor(() => {
      expect(screen.getByRole('alert', { name: /one-time secret/i })).toBeDefined();
      expect(screen.getByText('deadbeef1234567890abcdef')).toBeDefined();
    });
  });

  it('renders event types in the table', async () => {
    renderAdmin();

    await waitFor(() => {
      expect(screen.getByText('submission.scan_completed')).toBeDefined();
      expect(screen.getByText('submission.approved')).toBeDefined();
    });
  });

  it('shows reset button only for subscriptions with failures', async () => {
    renderAdmin();

    await waitFor(() => {
      expect(screen.getByText('failing-webhook')).toBeDefined();
    });

    // Reset button should exist for failing-webhook (sub2 with 7 failures)
    const resetBtn = screen.getByRole('button', { name: /reset failures for failing-webhook/i });
    expect(resetBtn).toBeDefined();

    // No reset button for my-webhook (0 failures)
    const allResets = screen.queryAllByRole('button', { name: /reset failures for my-webhook/i });
    expect(allResets).toHaveLength(0);
  });

  it('shows delete confirmation dialog', async () => {
    const user = userEvent.setup();
    renderAdmin();

    await waitFor(() => {
      expect(screen.getByText('my-webhook')).toBeDefined();
    });

    const deleteBtn = screen.getByRole('button', { name: /delete my-webhook/i });
    await user.click(deleteBtn);

    expect(screen.getByRole('dialog', { name: /confirm delete/i })).toBeDefined();
    expect(screen.getByText(/are you sure/i)).toBeDefined();
  });

  it('shows "No webhook subscriptions configured" when list is empty', async () => {
    server.use(
      http.get(adminBase, () => HttpResponse.json([]))
    );

    renderAdmin();

    await waitFor(() => {
      expect(screen.getByText(/no webhook subscriptions configured/i)).toBeDefined();
    });
  });
});
