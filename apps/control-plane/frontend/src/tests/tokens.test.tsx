import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MintDialog } from '@/features/tokens/mint-dialog';
import { TokensTable } from '@/features/tokens/tokens-table';
import { OneShotUrlDisplay } from '@/features/tokens/one-shot-url-display';
import '@testing-library/jest-dom';

// ─── Mock MSAL ─────────────────────────────────────────────────────────────────

vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Mock tokens API ───────────────────────────────────────────────────────────
// We mock the API module directly so tests are not coupled to network/MSW.

vi.mock('@/features/tokens/api', () => ({
  mintToken: vi.fn(),
  listTokens: vi.fn(),
  revokeToken: vi.fn(),
}));

// ─── Fixtures ──────────────────────────────────────────────────────────────────

const APP_ID = 'app-uuid-001';

const mintedToken = {
  jwt: 'header.payload.sig',
  jti: 'jti-uuid-001',
  expiresAt: '2026-07-23T00:00:00Z',
  reviewerLabel: 'Journal Reviewer #1',
  visitUrl: 'https://myapp.rac.local/?rac_token=header.payload.sig',
};

const tokenListItem = {
  jti: 'jti-uuid-001',
  reviewerLabel: 'Journal Reviewer #1',
  issuedAt: '2026-04-23T00:00:00Z',
  expiresAt: '2026-07-23T00:00:00Z',
  revokedAt: null,
  scope: 'read',
  issuedByPrincipalId: 'principal-abc',
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// ─── OneShotUrlDisplay tests ──────────────────────────────────────────────────

describe('OneShotUrlDisplay', () => {
  it('shows the visit URL', () => {
    render(<OneShotUrlDisplay visitUrl="https://test.rac.local/?rac_token=abc123" />);
    expect(screen.getByText(/rac_token=abc123/)).toBeInTheDocument();
  });

  it('copies the URL to clipboard when Copy URL is clicked', async () => {
    // userEvent.setup() installs its own clipboard stub on navigator.clipboard.
    // Spy on the stub's writeText to capture the call from the component.
    const user = userEvent.setup();
    const writeTextSpy = vi
      .spyOn(navigator.clipboard, 'writeText')
      .mockResolvedValue(undefined);

    render(<OneShotUrlDisplay visitUrl="https://test.rac.local/?rac_token=abc123" />);

    await user.click(screen.getByRole('button', { name: /copy url/i }));

    expect(writeTextSpy).toHaveBeenCalledWith('https://test.rac.local/?rac_token=abc123');
    writeTextSpy.mockRestore();
  });

  it('clears the URL after 5 minutes (fake timers)', async () => {
    vi.useFakeTimers();
    render(<OneShotUrlDisplay visitUrl="https://test.rac.local/?rac_token=abc123" />);

    expect(screen.getByText(/rac_token=abc123/)).toBeInTheDocument();

    // Advance 5 minutes + a bit; wrap in act so React flushes state updates.
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000 + 100);
    });

    expect(screen.getByText('URL cleared for security')).toBeInTheDocument();

    vi.useRealTimers();
  });
});

// ─── MintDialog tests ─────────────────────────────────────────────────────────

describe('MintDialog', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders the mint form', () => {
    renderWithClient(<MintDialog appId={APP_ID} onClose={() => {}} />);
    expect(screen.getByLabelText(/reviewer label/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/token valid for/i)).toBeInTheDocument();
  });

  it('shows the one-shot URL after successful mint', async () => {
    const { mintToken } = await import('@/features/tokens/api');
    vi.mocked(mintToken).mockResolvedValueOnce(mintedToken);

    const user = userEvent.setup();
    renderWithClient(<MintDialog appId={APP_ID} onClose={() => {}} />);

    await user.type(screen.getByLabelText(/reviewer label/i), 'Journal Reviewer #1');
    await user.click(screen.getByRole('button', { name: /mint token/i }));

    await waitFor(() => {
      expect(screen.getByText(/rac_token=/)).toBeInTheDocument();
    });
  });

  it('shows the copy button after minting', async () => {
    const { mintToken } = await import('@/features/tokens/api');
    vi.mocked(mintToken).mockResolvedValueOnce(mintedToken);

    const user = userEvent.setup();
    renderWithClient(<MintDialog appId={APP_ID} onClose={() => {}} />);

    await user.type(screen.getByLabelText(/reviewer label/i), 'Journal Reviewer #1');
    await user.click(screen.getByRole('button', { name: /mint token/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /copy url/i })).toBeInTheDocument();
    });
  });
});

// ─── TokensTable tests ────────────────────────────────────────────────────────

describe('TokensTable', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders token rows', async () => {
    const { listTokens } = await import('@/features/tokens/api');
    vi.mocked(listTokens).mockResolvedValue({ items: [tokenListItem] });

    renderWithClient(<TokensTable appId={APP_ID} />);
    await waitFor(() => {
      expect(screen.getByText('Journal Reviewer #1')).toBeInTheDocument();
    });
  });

  it('shows Active status for non-revoked token', async () => {
    const { listTokens } = await import('@/features/tokens/api');
    vi.mocked(listTokens).mockResolvedValue({ items: [tokenListItem] });

    renderWithClient(<TokensTable appId={APP_ID} />);
    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument();
    });
  });

  it('calls DELETE when Revoke is clicked', async () => {
    const { listTokens, revokeToken } = await import('@/features/tokens/api');
    vi.mocked(listTokens).mockResolvedValue({ items: [tokenListItem] });
    vi.mocked(revokeToken).mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderWithClient(<TokensTable appId={APP_ID} />);

    await waitFor(() => {
      expect(screen.getByText('Journal Reviewer #1')).toBeInTheDocument();
    });

    const revokeBtn = screen.getByRole('button', { name: /revoke/i });
    await user.click(revokeBtn);

    await waitFor(() => {
      expect(revokeToken).toHaveBeenCalledTimes(1);
    });
  });
});
