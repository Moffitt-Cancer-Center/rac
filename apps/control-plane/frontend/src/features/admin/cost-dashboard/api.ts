// pattern: Imperative Shell
/**
 * API client for admin cost endpoints.
 *
 * - GET /api/admin/cost/summary?year_month=YYYY-MM
 * - GET /api/admin/cost/idle
 */

import { acquireApiToken } from '@/lib/msal';

const apiBase = import.meta.env.VITE_API_BASE_URL || window.location.origin + '/api';

// ─── Types ─────────────────────────────────────────────────────────────────────

export interface CostSummaryRow {
  app_slug: string;
  total_usd: number;
}

export interface CostSummary {
  year_month: string;
  rows: CostSummaryRow[];
  grand_total_usd: number;
  untagged_usd: number;
}

export interface IdleApp {
  app_slug: string;
  last_request_at: string | null;
  days_idle: number;
  estimated_monthly_savings_usd: number;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

async function authHeaders(): Promise<Record<string, string>> {
  const token = await acquireApiToken();
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
}

// ─── API functions ─────────────────────────────────────────────────────────────

/**
 * Get cost summary for a given month.
 * @param yearMonth YYYY-MM format, defaults to current month
 */
export async function getCostSummary(yearMonth?: string): Promise<CostSummary> {
  const headers = await authHeaders();
  const params = yearMonth ? `?year_month=${encodeURIComponent(yearMonth)}` : '';
  const resp = await fetch(`${apiBase}/admin/cost/summary${params}`, { headers });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message ?? `HTTP ${resp.status}`);
  }

  return resp.json() as Promise<CostSummary>;
}

/**
 * List idle apps with cost savings estimates.
 */
export async function getIdleApps(): Promise<IdleApp[]> {
  const headers = await authHeaders();
  const resp = await fetch(`${apiBase}/admin/cost/idle`, { headers });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message ?? `HTTP ${resp.status}`);
  }

  return resp.json() as Promise<IdleApp[]>;
}
