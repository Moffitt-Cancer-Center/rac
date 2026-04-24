import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CostDashboard } from '@/features/admin/cost-dashboard';
import '@testing-library/jest-dom';

// ─── Mock recharts ResponsiveContainer (requires ResizeObserver not in jsdom) ─

vi.mock('recharts', async () => {
  const actual = await vi.importActual('recharts');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 800, height: 260 }}>{children}</div>
    ),
  };
});

// ─── Mock MSAL ─────────────────────────────────────────────────────────────────

vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const costSummary = {
  year_month: '2026-04',
  rows: [
    { app_slug: 'ml-pipeline', total_usd: 150.0 },
    { app_slug: 'cool-app', total_usd: 75.5 },
    { app_slug: 'bio-tools', total_usd: 40.0 },
  ],
  grand_total_usd: 265.5,
  untagged_usd: 0,
};

const idleApps = [
  {
    app_slug: 'old-app',
    last_request_at: '2026-02-01T00:00:00Z',
    days_idle: 81,
    estimated_monthly_savings_usd: 40.0,
  },
];

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

describe('CostDashboard — bar chart', () => {
  it('renders bar chart container when cost data is available', async () => {
    server.use(
      http.get(`${baseUrl}/admin/cost/summary`, () => HttpResponse.json(costSummary)),
      http.get(`${baseUrl}/admin/cost/idle`, () => HttpResponse.json([]))
    );

    renderWithClient(<CostDashboard />);

    await waitFor(() => {
      expect(screen.getByLabelText('cost by app bar chart')).toBeInTheDocument();
    });
  });
});

describe('CostDashboard — idle apps table', () => {
  it('renders idle apps table with app slug and savings', async () => {
    server.use(
      http.get(`${baseUrl}/admin/cost/summary`, () => HttpResponse.json(costSummary)),
      http.get(`${baseUrl}/admin/cost/idle`, () => HttpResponse.json(idleApps))
    );

    renderWithClient(<CostDashboard />);

    await waitFor(() => {
      expect(screen.getByLabelText('idle apps table')).toBeInTheDocument();
      expect(screen.getByText('old-app')).toBeInTheDocument();
      expect(screen.getByText('$40.00')).toBeInTheDocument();
    });
  });
});

describe('CostDashboard — empty idle state', () => {
  it('shows "No idle apps detected." when idle list is empty', async () => {
    server.use(
      http.get(`${baseUrl}/admin/cost/summary`, () => HttpResponse.json(costSummary)),
      http.get(`${baseUrl}/admin/cost/idle`, () => HttpResponse.json([]))
    );

    renderWithClient(<CostDashboard />);

    await waitFor(() => {
      expect(screen.getByText(/no idle apps detected/i)).toBeInTheDocument();
    });
  });
});
