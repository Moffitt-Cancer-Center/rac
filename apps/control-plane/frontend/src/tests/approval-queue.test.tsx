import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApprovalQueue } from '@/features/approval-queue';
import { SubmissionReview } from '@/features/approval-queue/submission-review';
import type { SubmissionSummary, ScanResultSummary } from '@/features/approval-queue/api';
import '@testing-library/jest-dom';

// ─── Mock MSAL ─────────────────────────────────────────────────────────────────

vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Mock TanStack Router (Link + navigate) ───────────────────────────────────

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    // Link needs a RouterProvider in real usage; stub it out for unit tests
    Link: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { to?: string; children?: React.ReactNode }) => (
      <a href={props.to ?? '#'}>{children}</a>
    ),
  };
});

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const sub1: SubmissionSummary = {
  id: 'sub-aaa-001',
  slug: 'my-research-app',
  status: 'awaiting_research_review',
  submitter_principal_id: 'user-001',
  github_repo_url: 'https://github.com/org/my-research-app',
  git_ref: 'main',
  dockerfile_path: 'Dockerfile',
  pi_principal_id: 'pi-001',
  dept_fallback: 'Bioinformatics',
  created_at: '2026-04-20T10:00:00Z',
  updated_at: '2026-04-20T10:00:00Z',
};

const sub2: SubmissionSummary = {
  id: 'sub-bbb-002',
  slug: 'ml-pipeline',
  status: 'awaiting_it_review',
  submitter_principal_id: 'user-002',
  github_repo_url: 'https://github.com/org/ml-pipeline',
  git_ref: 'v1.0',
  dockerfile_path: 'docker/Dockerfile',
  pi_principal_id: 'pi-002',
  dept_fallback: 'Data Science',
  created_at: '2026-04-21T10:00:00Z',
  updated_at: '2026-04-21T10:00:00Z',
};

const scanResultNormal: ScanResultSummary = {
  verdict: 'passed',
  effective_severity: 'none',
  findings: [],
  build_log_uri: null,
  defender_timed_out: false,
};

const scanResultDefenderPending: ScanResultSummary = {
  verdict: 'passed',
  effective_severity: 'none',
  findings: [],
  build_log_uri: null,
  defender_timed_out: true,
};

const baseUrl = 'http://localhost:3000/api';

// ─── MSW server ───────────────────────────────────────────────────────────────

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderWithClient(ui: React.ReactElement) {
  const qc = makeQueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('ApprovalQueue — list view', () => {
  it('renders both submissions from both status filters', async () => {
    server.use(
      http.get(`${baseUrl}/submissions`, ({ request }) => {
        const url = new URL(request.url);
        const status = url.searchParams.get('status');
        if (status === 'awaiting_research_review') return HttpResponse.json({ items: [sub1] });
        if (status === 'awaiting_it_review') return HttpResponse.json({ items: [sub2] });
        return HttpResponse.json({ items: [] });
      })
    );

    renderWithClient(
      <ApprovalQueue statusFilters={['awaiting_research_review', 'awaiting_it_review']} />
    );

    await waitFor(() => {
      expect(screen.getByText('my-research-app')).toBeInTheDocument();
      expect(screen.getByText('ml-pipeline')).toBeInTheDocument();
    });
  });

  it('shows empty state when no submissions pending', async () => {
    server.use(
      http.get(`${baseUrl}/submissions`, () => HttpResponse.json({ items: [] }))
    );

    renderWithClient(
      <ApprovalQueue statusFilters={['awaiting_research_review']} />
    );

    await waitFor(() => {
      expect(screen.getByText(/no submissions pending approval/i)).toBeInTheDocument();
    });
  });
});

describe('SubmissionReview — Defender badge (AC5.4)', () => {
  it('renders Defender badge when defender_timed_out is true', () => {
    renderWithClient(
      <SubmissionReview
        submission={sub1}
        scanResult={scanResultDefenderPending}
        stage="research"
      />
    );

    const badge = screen.getByLabelText('Defender scan pending badge');
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toContain('Defender scan pending');
  });

  it('does not render Defender badge when defender_timed_out is false', () => {
    renderWithClient(
      <SubmissionReview
        submission={sub1}
        scanResult={scanResultNormal}
        stage="research"
      />
    );

    expect(screen.queryByLabelText('Defender scan pending badge')).toBeNull();
  });

  it('does not render Defender badge when scanResult is null', () => {
    renderWithClient(
      <SubmissionReview
        submission={sub1}
        scanResult={null}
        stage="research"
      />
    );

    expect(screen.queryByLabelText('Defender scan pending badge')).toBeNull();
  });
});

describe('SubmissionReview — approval actions', () => {
  it('clicking Approve opens dialog with correct label', async () => {
    const user = userEvent.setup();

    renderWithClient(
      <SubmissionReview
        submission={sub1}
        scanResult={scanResultNormal}
        stage="research"
      />
    );

    await user.click(screen.getByRole('button', { name: /^approve$/i }));

    const dialog = screen.getByRole('dialog', { name: /confirm approve/i });
    expect(dialog).toBeInTheDocument();
  });

  it('409 response shows "no longer in expected state" error', async () => {
    server.use(
      http.post(`${baseUrl}/submissions/${sub1.id}/approvals/research`, () =>
        HttpResponse.json({ message: 'Conflict' }, { status: 409 })
      )
    );

    const user = userEvent.setup();

    renderWithClient(
      <SubmissionReview
        submission={sub1}
        scanResult={scanResultNormal}
        stage="research"
      />
    );

    await user.click(screen.getByRole('button', { name: /^approve$/i }));
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert.textContent).toContain('no longer in the expected state');
    });
  });
});
