import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AccessModeToggleCard } from '@/features/admin/access-mode/toggle-card';
import '@testing-library/jest-dom';

// ─── Mock MSAL ─────────────────────────────────────────────────────────────────

vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// ─── Fixtures ──────────────────────────────────────────────────────────────────

const APP_ID = 'app-uuid-am-001';
const baseUrl = 'http://localhost:3000/api';

let lastRequestBody: Record<string, unknown> = {};

const server = setupServer(
  http.post(`${baseUrl}/apps/${APP_ID}/access-mode`, async ({ request }) => {
    lastRequestBody = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      app_id: APP_ID,
      access_mode: lastRequestBody['mode'] as string,
      slug: 'test-app',
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }));
afterEach(() => {
  server.resetHandlers();
  lastRequestBody = {};
});
afterAll(() => server.close());

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('AccessModeToggleCard', () => {
  it('renders with token_required initially selected', () => {
    renderWithClient(
      <AccessModeToggleCard appId={APP_ID} currentMode="token_required" />,
    );

    const tokenRequiredRadio = screen.getByRole('radio', { name: /token required/i });
    expect(tokenRequiredRadio).toBeChecked();
  });

  it('Submit is disabled when notes is below minimum length', () => {
    renderWithClient(
      <AccessModeToggleCard appId={APP_ID} currentMode="token_required" />,
    );

    const submitBtn = screen.getByRole('button', { name: /update access mode/i });
    expect(submitBtn).toBeDisabled();
  });

  it('Submit is enabled after typing valid notes for token_required', async () => {
    const user = userEvent.setup();
    renderWithClient(
      <AccessModeToggleCard appId={APP_ID} currentMode="token_required" />,
    );

    await user.type(screen.getByRole('textbox'), 'Switching back to token required mode.');
    const submitBtn = screen.getByRole('button', { name: /update access mode/i });
    expect(submitBtn).not.toBeDisabled();
  });

  it('selecting public and submitting shows confirmation dialog', async () => {
    const user = userEvent.setup();
    renderWithClient(
      <AccessModeToggleCard appId={APP_ID} currentMode="token_required" />,
    );

    // Select public
    await user.click(screen.getByRole('radio', { name: /public/i }));

    // Fill in notes
    await user.type(screen.getByRole('textbox'), 'Making app public for open access.');

    // Submit
    await user.click(screen.getByRole('button', { name: /update access mode/i }));

    // Confirmation dialog should appear
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText(/make app publicly accessible/i)).toBeInTheDocument();
    });
  });

  it('confirmation dialog Confirm calls API with mode=public', async () => {
    const user = userEvent.setup();
    renderWithClient(
      <AccessModeToggleCard appId={APP_ID} currentMode="token_required" />,
    );

    await user.click(screen.getByRole('radio', { name: /public/i }));
    await user.type(screen.getByRole('textbox'), 'Making app public for open access.');
    await user.click(screen.getByRole('button', { name: /update access mode/i }));

    // Click the confirm button in the dialog
    const confirmBtn = await screen.findByRole('button', { name: /confirm — make public/i });
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(lastRequestBody['mode']).toBe('public');
    });
  });

  it('selecting token_required and submitting does NOT show confirmation', async () => {
    const user = userEvent.setup();
    renderWithClient(
      <AccessModeToggleCard appId={APP_ID} currentMode="public" />,
    );

    // Already on public, switch back to token_required
    await user.click(screen.getByRole('radio', { name: /token required/i }));
    await user.type(screen.getByRole('textbox'), 'Reverting to token required mode.');
    await user.click(screen.getByRole('button', { name: /update access mode/i }));

    // No confirmation dialog
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // API called directly
    await waitFor(() => {
      expect(lastRequestBody['mode']).toBe('token_required');
    });
  });

  it('cancel in confirmation dialog closes it without API call', async () => {
    const user = userEvent.setup();
    renderWithClient(
      <AccessModeToggleCard appId={APP_ID} currentMode="token_required" />,
    );

    await user.click(screen.getByRole('radio', { name: /public/i }));
    await user.type(screen.getByRole('textbox'), 'Test notes for cancellation flow.');
    await user.click(screen.getByRole('button', { name: /update access mode/i }));

    // Dialog shown
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // Click cancel
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));

    // Dialog gone
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // No API call was made (lastRequestBody still empty)
    expect(lastRequestBody).toEqual({});
  });
});
