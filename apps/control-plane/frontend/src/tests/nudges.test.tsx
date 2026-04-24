import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NudgesPanel } from '@/features/nudges/nudges-panel';
import '@testing-library/jest-dom';

// ─── Mock MSAL ─────────────────────────────────────────────────────────────────

vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Fixtures ──────────────────────────────────────────────────────────────────

const SUBMISSION_ID = 'sub-aaaa-0001';

/** Build a snake_case finding fixture (as the API returns). */
function makeFinding(overrides: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(),
    submission_id: SUBMISSION_ID,
    rule_id: 'dockerfile/inline_downloads',
    rule_version: 1,
    severity: 'warn',
    title: 'Inline download in Dockerfile',
    detail: 'Found wget usage at RUN step.',
    line_ranges: [[4, 4]],
    file_path: 'Dockerfile',
    suggested_action: 'override',
    auto_fix: null,
    created_at: '2026-04-23T10:00:00Z',
    decision: null,
    ...overrides,
  };
}

const findingError = makeFinding({
  id: 'f-error-001',
  severity: 'error',
  title: 'Root user in Dockerfile',
  detail: 'The last USER instruction sets root.',
  file_path: 'Dockerfile',
  line_ranges: [[10, 10]],
});

const findingWarn1 = makeFinding({
  id: 'f-warn-001',
  severity: 'warn',
  title: 'Inline download in Dockerfile',
  detail: 'wget found.',
  file_path: 'Dockerfile',
  line_ranges: [[4, 4]],
});

const findingWarn2 = makeFinding({
  id: 'f-warn-002',
  severity: 'warn',
  title: 'Large file committed',
  detail: 'data.bin is 55 MB.',
  file_path: 'data/data.bin',
  line_ranges: null,
});

const findingDecided = makeFinding({
  id: 'f-decided-001',
  severity: 'warn',
  title: 'Override example',
  detail: 'This finding has already been decided.',
  decision: {
    id: 'd-001',
    detection_finding_id: 'f-decided-001',
    decision: 'override',
    decision_actor_principal_id: 'user-principal-abc',
    decision_notes: null,
    created_at: '2026-04-23T11:00:00Z',
  },
});

const findingWithAutoFix = makeFinding({
  id: 'f-autofix-001',
  severity: 'warn',
  title: 'Has auto-fix',
  detail: 'Can be auto-fixed.',
  auto_fix: {
    kind: 'replace_line',
    file_path: 'Dockerfile',
    payload: 'RUN apt-get install -y curl',
  },
});

const findingNoAutoFix = makeFinding({
  id: 'f-no-autofix-001',
  severity: 'info',
  title: 'No auto-fix',
  detail: 'Cannot be auto-fixed.',
  auto_fix: null,
});

// ─── URL helpers ───────────────────────────────────────────────────────────────

// In vitest jsdom, window.location.origin = http://localhost:3000
// VITE_API_BASE_URL is undefined so resolveUrl builds:
// http://localhost:3000/api/submissions/<id>/findings
function findingsUrl(submissionId: string) {
  return `http://localhost:3000/api/submissions/${submissionId}/findings`;
}

function decisionsUrl(submissionId: string, findingId: string) {
  return `http://localhost:3000/api/submissions/${submissionId}/findings/${findingId}/decisions`;
}

// ─── MSW server ───────────────────────────────────────────────────────────────

const server = setupServer();

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

function renderPanel(submissionId: string = SUBMISSION_ID) {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <NudgesPanel submissionId={submissionId} />
    </QueryClientProvider>
  );
}

// ─── Test 1: Renders finding list ─────────────────────────────────────────────

describe('NudgesPanel — renders finding list', () => {
  it('shows all finding titles with correct severity badges, error ordered first', async () => {
    server.use(
      http.get(findingsUrl(SUBMISSION_ID), () =>
        // Return warn1, error, warn2 in non-sorted order → panel must sort error first
        HttpResponse.json([findingWarn1, findingError, findingWarn2])
      )
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText('Root user in Dockerfile')).toBeDefined();
      expect(screen.getByText('Inline download in Dockerfile')).toBeDefined();
      expect(screen.getByText('Large file committed')).toBeDefined();
    });

    // Check severity badges
    const errorBadge = screen.getByLabelText('severity: error');
    expect(errorBadge).toBeDefined();
    expect(errorBadge.textContent?.trim()).toBe('ERROR');

    const warnBadges = screen.getAllByLabelText('severity: warn');
    expect(warnBadges).toHaveLength(2);

    // Verify error appears before warn by checking DOM order of titles
    const allCards = screen.getAllByRole('heading', { level: 4 });
    expect(allCards[0]?.textContent).toBe('Root user in Dockerfile');
    expect(allCards[1]?.textContent).toBe('Inline download in Dockerfile');
    expect(allCards[2]?.textContent).toBe('Large file committed');
  });
});

// ─── Test 2: Empty state ───────────────────────────────────────────────────────

describe('NudgesPanel — empty state', () => {
  it('shows "No issues detected." when findings list is empty', async () => {
    server.use(
      http.get(findingsUrl(SUBMISSION_ID), () => HttpResponse.json([]))
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText('No issues detected.')).toBeDefined();
    });
  });
});

// ─── Test 3: Click Accept → dialog opens ──────────────────────────────────────

describe('NudgesPanel — click Accept opens dialog', () => {
  it('clicking Accept button opens DecisionDialog with accept preselected', async () => {
    server.use(
      http.get(findingsUrl(SUBMISSION_ID), () => HttpResponse.json([findingWarn1]))
    );

    const user = userEvent.setup();
    renderPanel();

    await waitFor(() => {
      expect(screen.getByText('Inline download in Dockerfile')).toBeDefined();
    });

    const acceptBtn = screen.getByRole('button', { name: /^accept$/i });
    await user.click(acceptBtn);

    // Dialog should now be open
    const dialog = screen.getByRole('dialog', { name: /confirm accept/i });
    expect(dialog).toBeDefined();

    // Heading confirms decision label
    expect(screen.getByText('Confirm Accept?')).toBeDefined();
  });
});

// ─── Test 4: Submit dialog with notes ─────────────────────────────────────────

describe('NudgesPanel — submit dialog with notes calls API', () => {
  it('types notes, clicks Confirm, asserts correct API payload, dialog closes', async () => {
    const postedBodies: unknown[] = [];
    let postedFindingId: string | undefined;

    // Use a counter so the first GET returns the undecided finding and subsequent
    // GETs (after query invalidation) return the decided version.
    let getCallCount = 0;

    server.use(
      http.get(findingsUrl(SUBMISSION_ID), () => {
        getCallCount++;
        if (getCallCount === 1) {
          return HttpResponse.json([findingWarn1]);
        }
        // Re-fetch after decision — return decided version
        return HttpResponse.json([
          {
            ...findingWarn1,
            decision: {
              id: 'd-new-001',
              detection_finding_id: findingWarn1.id,
              decision: 'accept',
              decision_actor_principal_id: 'user-principal-xyz',
              decision_notes: 'intentional',
              created_at: '2026-04-23T12:00:00Z',
            },
          },
        ]);
      }),
      http.post(decisionsUrl(SUBMISSION_ID, findingWarn1.id), async ({ request }) => {
        // Extract the finding ID from the literal URL (no route params — using exact URL)
        const url = new URL(request.url);
        const parts = url.pathname.split('/');
        // pathname: /api/submissions/<subId>/findings/<findingId>/decisions
        const findingsIdx = parts.indexOf('findings');
        postedFindingId = findingsIdx >= 0 ? parts[findingsIdx + 1] : undefined;
        postedBodies.push(await request.json());
        return HttpResponse.json(
          {
            id: 'd-new-001',
            detection_finding_id: findingWarn1.id,
            decision: 'accept',
            decision_actor_principal_id: 'user-principal-xyz',
            decision_notes: 'intentional',
            created_at: '2026-04-23T12:00:00Z',
          },
          { status: 201 }
        );
      })
    );

    const user = userEvent.setup();
    renderPanel();

    await waitFor(() => {
      expect(screen.getByText('Inline download in Dockerfile')).toBeDefined();
    });

    // Open dialog
    await user.click(screen.getByRole('button', { name: /^accept$/i }));
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeDefined();
    });

    // Type notes
    const notesField = screen.getByLabelText(/optional notes/i);
    await user.type(notesField, 'intentional');

    // Click Confirm
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    // Assert API was called with correct params
    await waitFor(() => {
      expect(postedBodies).toHaveLength(1);
    });

    const body = postedBodies[0] as Record<string, string>;
    expect(body['decision']).toBe('accept');
    expect(body['notes']).toBe('intentional');
    expect(postedFindingId).toBe(findingWarn1.id);

    // Dialog should close
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
  });
});

// ─── Test 5: Decided finding shows decision state ─────────────────────────────

describe('NudgesPanel — decided finding shows decision state', () => {
  it('finding with existing decision shows "Decided: override" text, action buttons absent', async () => {
    server.use(
      http.get(findingsUrl(SUBMISSION_ID), () => HttpResponse.json([findingDecided]))
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText('Override example')).toBeDefined();
    });

    // Decision state text
    const decisionState = screen.getByLabelText('decision state');
    expect(decisionState.textContent).toContain('Decided:');
    expect(decisionState.textContent).toContain('override');
    expect(decisionState.textContent).toContain('user-principal-abc');

    // No action buttons
    expect(screen.queryByRole('button', { name: /^accept$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^override$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^dismiss$/i })).toBeNull();
  });
});

// ─── Test 6: Apply auto-fix visibility ────────────────────────────────────────

describe('NudgesPanel — Apply auto-fix button visibility', () => {
  it('shows Apply auto-fix only for findings with auto_fix; absent otherwise', async () => {
    server.use(
      http.get(findingsUrl(SUBMISSION_ID), () =>
        HttpResponse.json([findingWithAutoFix, findingNoAutoFix])
      )
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText('Has auto-fix')).toBeDefined();
      expect(screen.getByText('No auto-fix')).toBeDefined();
    });

    // Apply auto-fix button present for findingWithAutoFix
    const autoFixBtns = screen.getAllByRole('button', { name: /apply auto-fix/i });
    expect(autoFixBtns).toHaveLength(1);

    // The "No auto-fix" card should only show Accept, Override, Dismiss
    const acceptBtns = screen.getAllByRole('button', { name: /^accept$/i });
    expect(acceptBtns).toHaveLength(2); // one per undecided finding
  });
});

// ─── Test 7: API error on decision ────────────────────────────────────────────

describe('NudgesPanel — API error keeps dialog open with error message', () => {
  it('500 from decisions endpoint shows error message inline, dialog stays open', async () => {
    server.use(
      http.get(findingsUrl(SUBMISSION_ID), () => HttpResponse.json([findingWarn1])),
      http.post(decisionsUrl(SUBMISSION_ID, findingWarn1.id), () =>
        HttpResponse.json({ message: 'Internal server error' }, { status: 500 })
      )
    );

    const user = userEvent.setup();
    renderPanel();

    await waitFor(() => {
      expect(screen.getByText('Inline download in Dockerfile')).toBeDefined();
    });

    // Open dialog
    await user.click(screen.getByRole('button', { name: /^accept$/i }));
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeDefined();
    });

    // Click Confirm without notes
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    // Error message appears inline
    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert.textContent).toContain('Internal server error');
    });

    // Dialog remains open
    expect(screen.getByRole('dialog')).toBeDefined();
  });
});
