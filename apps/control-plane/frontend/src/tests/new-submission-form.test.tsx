import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer as mswSetupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NewSubmissionForm } from '@/features/submissions/new-submission-form';
import '@testing-library/jest-dom';

const apiBaseUrl = '/api';

// Mock MSAL
vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: {
    getAllAccounts: () => [],
  },
}));

const handlers = [
  http.post(`${apiBaseUrl}/submissions`, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;

    // Validate request matches backend schema expectations (snake_case)
    if (!body.github_repo_url) {
      return HttpResponse.json(
        {
          code: 'validation_error',
          message: 'Validation error',
          details: [
            {
              field: 'github_repo_url',
              message: 'Field required',
            },
          ],
        },
        { status: 422 }
      );
    }

    // Simulate GitHub validation error
    if (String(body.github_repo_url).includes('nonexistent')) {
      return HttpResponse.json(
        {
          code: 'github_not_found',
          message: 'Repository not found',
          details: [
            {
              field: 'github_repo_url',
              message: 'Repository or ref not found',
            },
          ],
        },
        { status: 422 }
      );
    }

    // Success case
    return HttpResponse.json(
      {
        id: '550e8400-e29b-41d4-a716-446655440000',
        slug: 'test-submission',
        status: 'awaiting_scan',
        submitter_principal_id: '550e8400-e29b-41d4-a716-446655440001',
        agent_id: null,
        github_repo_url: body.github_repo_url,
        git_ref: body.git_ref || 'main',
        dockerfile_path: body.dockerfile_path || 'Dockerfile',
        pi_principal_id: body.pi_principal_id,
        dept_fallback: body.dept_fallback,
        manifest: body.manifest || null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      { status: 201 }
    );
  }),
];

const server = mswSetupServer(...handlers);

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
});

afterEach(() => {
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false },
    mutations: { retry: false },
  },
});

describe('NewSubmissionForm', () => {
  it('renders all form fields', () => {
    const mockSubmit = vi.fn();

    render(
      <QueryClientProvider client={queryClient}>
        <NewSubmissionForm onSubmit={mockSubmit} />
      </QueryClientProvider>
    );

    expect(screen.getByLabelText(/github repository url/i)).toBeDefined();
    expect(screen.getByLabelText(/git ref/i)).toBeDefined();
    expect(screen.getByLabelText(/dockerfile path/i)).toBeDefined();
    expect(screen.getByLabelText(/paper title/i)).toBeDefined();
    expect(screen.getByLabelText(/pi principal id/i)).toBeDefined();
    expect(screen.getByLabelText(/department/i)).toBeDefined();
    expect(screen.getByText(/submit application/i)).toBeDefined();
  });

  it('validates required fields', async () => {
    const mockSubmit = vi.fn();
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <NewSubmissionForm onSubmit={mockSubmit} />
      </QueryClientProvider>
    );

    const submitButton = screen.getByText(/submit application/i);
    await user.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText(/must be a valid github url/i)).toBeDefined();
    });
  });

  it('submits form with valid data', async () => {
    const mockSubmit = vi.fn(async () => {
      // Success
    });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <NewSubmissionForm onSubmit={mockSubmit} />
      </QueryClientProvider>
    );

    await user.type(
      screen.getByPlaceholderText(/github\.com\/owner\/repo/i),
      'https://github.com/test/repo'
    );
    await user.type(
      screen.getByPlaceholderText(/xxxxxxxx-xxxx-xxxx-xxxx/i),
      '550e8400-e29b-41d4-a716-446655440001'
    );
    await user.type(
      screen.getByPlaceholderText(/Medical Oncology/i),
      'Medical Oncology'
    );

    const submitButton = screen.getByText(/submit application/i);
    await user.click(submitButton);

    await waitFor(() => {
      expect(mockSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          githubRepoUrl: 'https://github.com/test/repo',
          piPrincipalId: '550e8400-e29b-41d4-a716-446655440001',
          deptFallback: 'Medical Oncology',
          gitRef: 'main',
          dockerfilePath: 'Dockerfile',
        })
      );
    });
  });

  it('displays backend validation errors', async () => {
    const mockSubmit = vi.fn(async () => {
      // Simulate API call that returns validation error
      const error = new Error('Validation error') as any;
      error.apiError = {
        code: 'github_not_found',
        message: 'Repository not found',
        details: [
          {
            field: 'githubRepoUrl',
            message: 'Repository or ref not found',
          },
        ],
      };
      throw error;
    });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <NewSubmissionForm onSubmit={mockSubmit} />
      </QueryClientProvider>
    );

    await user.type(
      screen.getByPlaceholderText(/github\.com\/owner\/repo/i),
      'https://github.com/nonexistent/repo'
    );
    await user.type(
      screen.getByPlaceholderText(/xxxxxxxx-xxxx-xxxx-xxxx/i),
      '550e8400-e29b-41d4-a716-446655440001'
    );
    await user.type(
      screen.getByPlaceholderText(/Medical Oncology/i),
      'Medical Oncology'
    );

    const submitButton = screen.getByText(/submit application/i);
    await user.click(submitButton);

    await waitFor(() => {
      expect(
        screen.getByText(/repository or ref not found/i)
      ).toBeDefined();
    });
  });

  it('disables form while submitting', async () => {
    let resolveSubmit: (() => void) | null = null;
    const submitPromise = new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    });

    const mockSubmit = vi.fn(async () => {
      await submitPromise;
    });
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <NewSubmissionForm onSubmit={mockSubmit} />
      </QueryClientProvider>
    );

    await user.type(
      screen.getByPlaceholderText(/github\.com\/owner\/repo/i),
      'https://github.com/test/repo'
    );
    await user.type(
      screen.getByPlaceholderText(/xxxxxxxx-xxxx-xxxx-xxxx/i),
      '550e8400-e29b-41d4-a716-446655440001'
    );
    await user.type(
      screen.getByPlaceholderText(/Medical Oncology/i),
      'Medical Oncology'
    );

    const submitButton = screen.getByRole('button', {name: /submit application/i});
    await user.click(submitButton);

    await waitFor(() => {
      expect((submitButton as HTMLButtonElement).disabled).toBe(true);
    });

    if (resolveSubmit) {
      resolveSubmit();
    }
  });
});
