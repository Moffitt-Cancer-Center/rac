import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@testing-library/jest-dom';

import { AssetTileUpload } from '@/features/manifest-form/asset-tile-upload';
import { AssetTileExternalUrl } from '@/features/manifest-form/asset-tile-external-url';
import { AssetTileSharedReference } from '@/features/manifest-form/asset-tile-shared-reference';
import { ManifestForm } from '@/features/manifest-form';
import { AssetHashMismatchCard } from '@/features/approval-queue/asset-hash-mismatch-card';
import type { FormManifest } from '@/features/manifest-form/types';

// ─── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock('@/lib/msal', () => ({
  acquireApiToken: async () => Promise.resolve('mock-token'),
  msalInstance: { getAllAccounts: () => [] },
}));

// Fixed SHA-256 hex for deterministic tests
const FIXED_SHA256_HEX = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';

// Injected helpers used in upload tile tests
const mockComputeSha256 = vi.fn().mockResolvedValue(FIXED_SHA256_HEX);
const mockUploadToSasUrl = vi.fn().mockResolvedValue(undefined);

// ─── MSW server ────────────────────────────────────────────────────────────────

const apiBase = '/api';
const SUBMISSION_ID = 'sub-test-001';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }));
afterEach(() => {
  server.resetHandlers();
  mockComputeSha256.mockClear();
  mockUploadToSasUrl.mockClear();
});
afterAll(() => server.close());

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderWithClient(ui: React.ReactElement) {
  const qc = makeQueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

/** Create a minimal File object for testing */
function makeFile(name = 'test.fa', content = 'ACGT', type = 'text/plain') {
  return new File([content], name, { type });
}

// ─── Test 1: Upload tile — mint SAS → PUT → finalize → onAssetReady ──────────

describe('test_upload_tile_mint_sas_then_uploads', () => {
  it('calls onAssetReady with correct name + mountPath after successful upload', async () => {
    const user = userEvent.setup();
    const onAssetReady = vi.fn();

    server.use(
      http.post(`${apiBase}/submissions/${SUBMISSION_ID}/assets/uploads/sas`, () =>
        HttpResponse.json({
          upload_url: 'https://blob.storage.test/container/blob?sas=token',
          blob_path: `submissions/${SUBMISSION_ID}/reference-genome`,
          expires_at: new Date(Date.now() + 3600000).toISOString(),
          max_size_bytes: 10 * 1024 * 1024,
        })
      ),
      http.post(`${apiBase}/submissions/${SUBMISSION_ID}/assets/uploads/finalize`, () =>
        HttpResponse.json({
          id: 'asset-001',
          submission_id: SUBMISSION_ID,
          name: 'reference-genome',
          kind: 'upload',
          mount_path: '/mnt/ref',
          sha256: FIXED_SHA256_HEX,
          size_bytes: 4,
          status: 'ready',
          created_at: new Date().toISOString(),
        })
      ),
    );

    renderWithClient(
      <AssetTileUpload
        submissionId={SUBMISSION_ID}
        onAssetReady={onAssetReady}
        _computeSha256={mockComputeSha256}
        _uploadToSasUrl={mockUploadToSasUrl}
      />
    );

    // Fill in name and mount path
    await user.type(screen.getByPlaceholderText(/e\.g\. reference-genome/i), 'reference-genome');
    await user.clear(screen.getByPlaceholderText(/\/mnt\/data\/file\.fa/i));
    await user.type(screen.getByPlaceholderText(/\/mnt\/data\/file\.fa/i), '/mnt/ref');

    // Attach a file
    const fileInput = screen.getByTestId('upload-file-input');
    const file = makeFile('genome.fa', 'ACGT');
    await user.upload(fileInput, file);

    // Click upload
    await user.click(screen.getByRole('button', { name: /^upload$/i }));

    await waitFor(() => {
      expect(onAssetReady).toHaveBeenCalledWith({
        kind: 'upload',
        name: 'reference-genome',
        mountPath: '/mnt/ref',
      });
    });

    // Verify SAS was minted and blob was "uploaded" via injected fn
    expect(mockComputeSha256).toHaveBeenCalledWith(file);
    expect(mockUploadToSasUrl).toHaveBeenCalledWith(
      'https://blob.storage.test/container/blob?sas=token',
      file,
      expect.any(Function),
    );
  });
});

// ─── Test 2: Upload tile — sha256 mismatch shows error ───────────────────────

describe('test_upload_sha256_mismatch_shows_error', () => {
  it('shows sha256_mismatch error message when finalize returns 422', async () => {
    const user = userEvent.setup();
    const onAssetReady = vi.fn();

    server.use(
      http.post(`${apiBase}/submissions/${SUBMISSION_ID}/assets/uploads/sas`, () =>
        HttpResponse.json({
          upload_url: 'https://blob.storage.test/bad?sas=token',
          blob_path: `submissions/${SUBMISSION_ID}/bad-file`,
          expires_at: new Date(Date.now() + 3600000).toISOString(),
          max_size_bytes: 1024,
        })
      ),
      http.post(`${apiBase}/submissions/${SUBMISSION_ID}/assets/uploads/finalize`, () =>
        HttpResponse.json(
          { code: 'sha256_mismatch', message: 'SHA-256 mismatch' },
          { status: 422 }
        )
      ),
    );

    renderWithClient(
      <AssetTileUpload
        submissionId={SUBMISSION_ID}
        onAssetReady={onAssetReady}
        _computeSha256={mockComputeSha256}
        _uploadToSasUrl={mockUploadToSasUrl}
      />
    );

    await user.type(screen.getByPlaceholderText(/e\.g\. reference-genome/i), 'bad-file');
    await user.clear(screen.getByPlaceholderText(/\/mnt\/data\/file\.fa/i));
    await user.type(screen.getByPlaceholderText(/\/mnt\/data\/file\.fa/i), '/mnt/bad');

    const fileInput = screen.getByTestId('upload-file-input');
    await user.upload(fileInput, makeFile('bad.fa', 'XXXX'));

    await user.click(screen.getByRole('button', { name: /^upload$/i }));

    await waitFor(() => {
      const errorEl = screen.getByTestId('upload-error');
      expect(errorEl).toHaveTextContent(/sha-256 mismatch/i);
    });

    expect(onAssetReady).not.toHaveBeenCalled();
  });
});

// ─── Test 3: External URL tile — sha256 length validation ────────────────────

describe('test_external_url_tile_validates_sha256_length', () => {
  it('disables submit for 63-char sha256 with error; enables for 64-char', async () => {
    const user = userEvent.setup();
    const onAssetReady = vi.fn();

    renderWithClient(
      <AssetTileExternalUrl onAssetReady={onAssetReady} />
    );

    const sha256Input = screen.getByLabelText(/declared sha-256/i);
    const addBtn = screen.getByTestId('add-external-url-btn');

    // 63-char sha → button disabled, error shown
    const shortSha = 'a'.repeat(63);
    await user.type(sha256Input, shortSha);
    expect(addBtn).toBeDisabled();
    expect(screen.getByTestId('sha256-length-error')).toHaveTextContent(/63\/64/);

    // Fill all required fields and use valid 64-char sha
    await user.clear(sha256Input);
    const validSha = 'a'.repeat(64);
    await user.type(sha256Input, validSha);

    await user.type(screen.getByLabelText(/asset name/i), 'my-data');
    await user.type(screen.getByLabelText(/url/i), 'https://example.com/file.gz');
    await user.clear(screen.getByLabelText(/mount path/i));
    await user.type(screen.getByLabelText(/mount path/i), '/mnt/data');

    // Button should now be enabled
    await waitFor(() => {
      expect(addBtn).not.toBeDisabled();
    });

    // No sha256 length error for valid 64-char input
    expect(screen.queryByTestId('sha256-length-error')).toBeNull();
  });
});

// ─── Test 4: Shared reference tile — disabled with "Coming in v2" ─────────────

describe('test_shared_reference_disabled', () => {
  it('renders with button disabled and Coming in v2 text', () => {
    render(<AssetTileSharedReference />);

    const btn = screen.getByRole('button', { name: /add shared reference/i });
    expect(btn).toBeDisabled();

    expect(screen.getByText(/Coming in v2/)).toBeInTheDocument();
  });
});

// ─── Test 5: ManifestForm submit collects all sections ───────────────────────

describe('test_manifest_form_submit_collects_all_sections', () => {
  it('calls onSubmit with correct FormManifest after filling resources + env vars', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async (_: FormManifest) => {});

    renderWithClient(
      <ManifestForm
        submissionId={SUBMISSION_ID}
        onSubmit={onSubmit}
      />
    );

    // Change target port
    const portInput = screen.getByLabelText(/target port/i);
    await user.clear(portInput);
    await user.type(portInput, '3000');

    // Select CPU = 1.0
    await user.selectOptions(screen.getByLabelText(/cpu cores/i), '1');

    // Select Memory = 2.0
    await user.selectOptions(screen.getByLabelText(/memory/i), '2');

    // Add an env var
    await user.click(screen.getByRole('button', { name: /\+ add variable/i }));
    const keyInputs = screen.getAllByRole('textbox', { name: /environment variable key/i });
    const valInputs = screen.getAllByRole('textbox', { name: /environment variable value/i });
    await user.type(keyInputs[0], 'PORT');
    await user.type(valInputs[0], '3000');

    // Submit
    await user.click(screen.getByRole('button', { name: /save manifest/i }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledOnce();
    });

    const call = onSubmit.mock.calls[0]![0] as FormManifest;
    expect(call.targetPort).toBe(3000);
    expect(call.cpuCores).toBe(1.0);
    expect(call.memoryGb).toBe(2.0);
    expect(call.envVars).toEqual({ PORT: '3000' });
    expect(call.assets).toEqual([]);
  });
});

// ─── Test 6: AssetHashMismatchCard displays both hashes ──────────────────────

describe('test_hash_mismatch_card_displays_both_hashes', () => {
  it('renders both expected and actual sha256 hashes', () => {
    const expected = 'a'.repeat(64);
    const actual = 'b'.repeat(64);

    render(
      <AssetHashMismatchCard
        assetName="hg38-reference"
        expectedSha256={expected}
        actualSha256={actual}
        sourceUrl="https://example.com/hg38.fa.gz"
      />
    );

    expect(screen.getByTestId('expected-sha256')).toHaveTextContent(expected);
    expect(screen.getByTestId('actual-sha256')).toHaveTextContent(actual);
    // Asset name in heading
    expect(screen.getByRole('region')).toBeInTheDocument();
    expect(screen.getByText(/hg38-reference/)).toBeInTheDocument();
    expect(screen.getByRole('link')).toHaveAttribute(
      'href',
      'https://example.com/hg38.fa.gz',
    );
  });

  it('shows Retry Fetch button only when isAdmin=true', () => {
    const props = {
      assetName: 'test-asset',
      expectedSha256: 'a'.repeat(64),
      actualSha256: 'b'.repeat(64),
      sourceUrl: 'https://example.com/file',
    };

    const { rerender } = render(<AssetHashMismatchCard {...props} isAdmin={false} />);
    expect(screen.queryByRole('button', { name: /retry fetch/i })).toBeNull();

    rerender(<AssetHashMismatchCard {...props} isAdmin={true} onRetry={vi.fn()} />);
    expect(screen.getByRole('button', { name: /retry fetch/i })).toBeInTheDocument();
  });
});

// ─── Test 7: Add asset buttons render correct tiles ──────────────────────────

describe('test_add_asset_buttons', () => {
  it('clicking Add Upload renders AssetTileUpload', async () => {
    const user = userEvent.setup();

    renderWithClient(
      <ManifestForm submissionId={SUBMISSION_ID} onSubmit={vi.fn()} />
    );

    await user.click(screen.getByTestId('add-upload-btn'));

    expect(screen.getByTestId('asset-tile-upload')).toBeInTheDocument();
    // Upload and External buttons should no longer be visible
    expect(screen.queryByTestId('add-upload-btn')).toBeNull();
  });

  it('clicking Add External URL renders AssetTileExternalUrl', async () => {
    const user = userEvent.setup();

    renderWithClient(
      <ManifestForm submissionId={SUBMISSION_ID} onSubmit={vi.fn()} />
    );

    await user.click(screen.getByTestId('add-external-btn'));

    expect(screen.getByTestId('asset-tile-external-url')).toBeInTheDocument();
  });

  it('shared reference tile is always present in the add controls', () => {
    renderWithClient(
      <ManifestForm submissionId={SUBMISSION_ID} onSubmit={vi.fn()} />
    );

    expect(screen.getByTestId('asset-tile-shared-reference')).toBeInTheDocument();
  });
});
