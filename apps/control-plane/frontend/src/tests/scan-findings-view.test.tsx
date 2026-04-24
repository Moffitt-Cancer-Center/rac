import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ScanFindingsView } from '@/features/submissions/scan-findings-view';
import type { ScanResult, ScanFinding } from '@/features/submissions/scan-findings-view';
import '@testing-library/jest-dom';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderView(scanResult: ScanResult | null | undefined) {
  return render(<ScanFindingsView scanResult={scanResult} />);
}

const sampleFinding: ScanFinding = {
  cve_id: 'CVE-2024-1234',
  severity: 'critical',
  cvss_score: 9.8,
  epss_score: 0.97,
  is_kev: true,
  package_name: 'openssl',
  package_version: '3.0.1',
  fixed_version: '3.0.7',
};

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('ScanFindingsView', () => {
  it('renders nothing when scanResult is null', () => {
    const { container } = renderView(null);
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when scanResult is undefined', () => {
    const { container } = renderView(undefined);
    expect(container.firstChild).toBeNull();
  });

  it('renders CVE table for rejected verdict', () => {
    renderView({
      verdict: 'rejected',
      effective_severity: 'critical',
      findings: [sampleFinding],
    });

    expect(screen.getByText(/scan rejected/i)).toBeDefined();
    expect(screen.getByText('CVE-2024-1234')).toBeDefined();
    // KEV badge
    expect(screen.getByLabelText('KEV badge')).toBeDefined();
    // EPSS score — 97.0%
    expect(screen.getByText('97.0%')).toBeDefined();
  });

  it('renders CVE table for partial_rejected verdict', () => {
    renderView({
      verdict: 'partial_rejected',
      effective_severity: 'high',
      findings: [sampleFinding],
    });

    expect(screen.getByText(/scan rejected/i)).toBeDefined();
    expect(screen.getByText('CVE-2024-1234')).toBeDefined();
  });

  it('renders severity badges sorted by severity descending', () => {
    const findings: ScanFinding[] = [
      { cve_id: 'CVE-LOW', severity: 'low' },
      { cve_id: 'CVE-CRITICAL', severity: 'critical' },
      { cve_id: 'CVE-MEDIUM', severity: 'medium' },
    ];
    renderView({ verdict: 'rejected', effective_severity: 'critical', findings });

    const rows = screen.getAllByRole('row');
    // rows[0] is <thead>, rows[1..] are data rows
    // Critical should appear first
    expect(rows[1]?.textContent).toContain('CVE-CRITICAL');
    expect(rows[2]?.textContent).toContain('CVE-MEDIUM');
    expect(rows[3]?.textContent).toContain('CVE-LOW');
  });

  it('renders build failed view with download link for build_failed verdict', () => {
    renderView({
      verdict: 'build_failed',
      effective_severity: 'none',
      findings: [],
      build_log_uri: 'https://example.com/build.log',
    });

    expect(screen.getByText(/pipeline build failed/i)).toBeDefined();
    const link = screen.getByRole('link', { name: /download build log/i });
    expect(link).toBeDefined();
    expect(link.getAttribute('href')).toBe('https://example.com/build.log');
  });

  it('renders build failed without link when build_log_uri is absent', () => {
    renderView({
      verdict: 'build_failed',
      effective_severity: 'none',
      findings: [],
    });

    expect(screen.getByText(/pipeline build failed/i)).toBeDefined();
    expect(screen.getByText(/build log not available/i)).toBeDefined();
  });

  it('renders scan passed badge for passed verdict', () => {
    renderView({
      verdict: 'passed',
      effective_severity: 'none',
      findings: [],
    });

    expect(screen.getByText(/scan passed/i)).toBeDefined();
  });

  it('renders scan passed with finding count when findings exist', () => {
    renderView({
      verdict: 'passed',
      effective_severity: 'low',
      findings: [{ cve_id: 'CVE-LOW', severity: 'low' }],
    });

    expect(screen.getByText(/1 low-severity finding/i)).toBeDefined();
  });

  it('renders defender timeout badge for partial_passed verdict', () => {
    renderView({
      verdict: 'partial_passed',
      effective_severity: 'none',
      findings: [],
      defender_timed_out: true,
    });

    expect(screen.getByText(/scan passed/i)).toBeDefined();
    expect(screen.getByText(/defender scan pending/i)).toBeDefined();
  });

  it('renders nothing for an unrecognised verdict', () => {
    // Cast to bypass the TypeScript union to test runtime guard
    const { container } = renderView({
      verdict: 'pending' as ScanResult['verdict'],
      effective_severity: 'none',
      findings: [],
    });
    // Should not crash and render nothing meaningful
    expect(container.querySelector('section')).toBeNull();
  });

  it('shows dashes for findings with missing optional fields', () => {
    renderView({
      verdict: 'rejected',
      effective_severity: 'high',
      findings: [{ cve_id: 'CVE-EMPTY', severity: 'high' }],
    });

    // Package, version, fixed_version should show em dashes
    const cells = screen.getAllByText('—');
    expect(cells.length).toBeGreaterThan(0);
  });

  it('applies red bold class to EPSS scores >= 95%', () => {
    renderView({
      verdict: 'rejected',
      effective_severity: 'critical',
      findings: [{ cve_id: 'CVE-HIGH-EPSS', severity: 'critical', epss_score: 0.96 }],
    });

    const epssSpan = screen.getByText('96.0%');
    expect(epssSpan.className).toContain('text-red-700');
  });

  it('applies normal class to EPSS scores < 95%', () => {
    renderView({
      verdict: 'rejected',
      effective_severity: 'high',
      findings: [{ cve_id: 'CVE-LOW-EPSS', severity: 'high', epss_score: 0.5 }],
    });

    const epssSpan = screen.getByText('50.0%');
    expect(epssSpan.className).toContain('text-gray-700');
  });
});
