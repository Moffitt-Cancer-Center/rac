import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AccessLogViewer } from '@/features/access-log/access-log-viewer';
import '@testing-library/jest-dom';

// ─── Mock MSAL ─────────────────────────────────────────────────────────────────

vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Fixtures ──────────────────────────────────────────────────────────────────

const APP_ID = 'app-uuid-log-001';
const baseUrl = 'http://localhost:3000/api';

function makeRow(overrides: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(),
    created_at: '2026-04-23T10:00:00Z',
    reviewer_token_jti: null,
    reviewer_label: null,
    access_mode: 'token_required',
    method: 'GET',
    path: '/index',
    upstream_status: 200,
    latency_ms: 42,
    source_ip: '10.0.0.1',
    ...overrides,
  };
}

const row1 = makeRow({ id: 'id-001', method: 'GET', path: '/dashboard' });
const row2 = makeRow({ id: 'id-002', method: 'POST', path: '/submit' });
const row3 = makeRow({ id: 'id-003', access_mode: 'public', reviewer_label: 'Dr. Smith' });

// Capture the last request URL for assertion
let lastRequestUrl = '';

// ─── MSW server ────────────────────────────────────────────────────────────────

const server = setupServer(
  http.get(`${baseUrl}/apps/${APP_ID}/access-log`, ({ request }) => {
    lastRequestUrl = request.url;
    const url = new URL(request.url);
    const mode = url.searchParams.get('mode');
    const items = mode === 'public' ? [row3] : [row1, row2, row3];
    return HttpResponse.json({ items, next_cursor: null });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }));
afterEach(() => {
  server.resetHandlers();
  lastRequestUrl = '';
});
afterAll(() => server.close());

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('AccessLogViewer', () => {
  it('renders 3 fixture rows', async () => {
    renderWithClient(<AccessLogViewer appId={APP_ID} />);

    await waitFor(() => {
      expect(screen.getByText('/dashboard')).toBeInTheDocument();
      expect(screen.getByText('/submit')).toBeInTheDocument();
      expect(screen.getByText('/index')).toBeInTheDocument();
    });
  });

  it('shows reviewer_label when present', async () => {
    renderWithClient(<AccessLogViewer appId={APP_ID} />);
    await waitFor(() => {
      expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
    });
  });

  it('refetches with mode filter when dropdown changes', async () => {
    const user = userEvent.setup();
    renderWithClient(<AccessLogViewer appId={APP_ID} />);

    // Wait for initial load
    await waitFor(() => screen.getByText('/dashboard'));

    // Change mode dropdown to "public"
    const modeSelect = screen.getByLabelText(/access mode/i);
    await user.selectOptions(modeSelect, 'public');

    await waitFor(() => {
      expect(lastRequestUrl).toContain('mode=public');
    });
  });

  it('shows Load more button when next_cursor is present', async () => {
    const JTI_CURSOR = crypto.randomUUID();
    server.use(
      http.get(`${baseUrl}/apps/${APP_ID}/access-log`, ({ request }) => {
        const url = new URL(request.url);
        const before = url.searchParams.get('before');
        if (!before) {
          return HttpResponse.json({
            items: [row1, row2, row3],
            next_cursor: JTI_CURSOR,
          });
        }
        return HttpResponse.json({ items: [], next_cursor: null });
      }),
    );

    renderWithClient(<AccessLogViewer appId={APP_ID} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument();
    });
  });

  it('calls fetchNextPage with before cursor when Load more is clicked', async () => {
    const user = userEvent.setup();
    const PAGE2_CURSOR = crypto.randomUUID();
    let page2Called = false;

    server.use(
      http.get(`${baseUrl}/apps/${APP_ID}/access-log`, ({ request }) => {
        const url = new URL(request.url);
        const before = url.searchParams.get('before');
        if (!before) {
          return HttpResponse.json({
            items: [row1, row2, row3],
            next_cursor: PAGE2_CURSOR,
          });
        }
        page2Called = true;
        expect(before).toBe(PAGE2_CURSOR);
        return HttpResponse.json({ items: [], next_cursor: null });
      }),
    );

    renderWithClient(<AccessLogViewer appId={APP_ID} />);

    const loadMoreBtn = await screen.findByRole('button', { name: /load more/i });
    await user.click(loadMoreBtn);

    await waitFor(() => {
      expect(page2Called).toBe(true);
    });
  });
});
