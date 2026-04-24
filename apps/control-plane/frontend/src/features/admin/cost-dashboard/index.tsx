// pattern: Imperative Shell — fetches cost data, renders dashboard
/**
 * CostDashboard: Admin view showing per-app costs and idle app recommendations.
 *
 * - Month picker (defaults to current month)
 * - Bar chart of top 5 apps by cost (recharts BarChart)
 * - Grand total + untagged cost display
 * - Idle apps table with estimated monthly savings
 *
 * Verifies: rac-v1.AC11.2 (UI), rac-v1.AC11.3 (UI)
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { getCostSummary, getIdleApps } from './api';

// ─── Helpers ───────────────────────────────────────────────────────────────────

function currentYearMonth(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  return `${y}-${m}`;
}

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

// ─── Month Picker ──────────────────────────────────────────────────────────────

interface MonthPickerProps {
  value: string;
  onChange: (month: string) => void;
}

function MonthPicker({ value, onChange }: MonthPickerProps) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="month-picker" className="text-sm font-medium text-gray-700">
        Month
      </label>
      <input
        id="month-picker"
        type="month"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
    </div>
  );
}

// ─── Cost Chart ────────────────────────────────────────────────────────────────

interface CostChartProps {
  rows: { app_slug: string; total_usd: number }[];
}

function CostChart({ rows }: CostChartProps) {
  const top5 = [...rows]
    .sort((a, b) => b.total_usd - a.total_usd)
    .slice(0, 5);

  if (top5.length === 0) {
    return (
      <div className="text-sm text-gray-500 py-4 text-center">
        No cost data available for this month.
      </div>
    );
  }

  return (
    <div aria-label="cost by app bar chart" style={{ width: '100%', height: 260 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={top5} margin={{ top: 8, right: 16, bottom: 8, left: 48 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="app_slug"
            tick={{ fontSize: 11 }}
            interval={0}
          />
          <YAxis
            tickFormatter={(v: number) => `$${v}`}
            tick={{ fontSize: 11 }}
          />
          <Tooltip formatter={(value: number) => formatUsd(value)} />
          <Bar dataKey="total_usd" name="Cost (USD)" fill="#2563eb" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Idle Apps Table ───────────────────────────────────────────────────────────

interface IdleAppsTableProps {
  apps: {
    app_slug: string;
    last_request_at: string | null;
    days_idle: number;
    estimated_monthly_savings_usd: number;
  }[];
}

function IdleAppsTable({ apps }: IdleAppsTableProps) {
  if (apps.length === 0) {
    return (
      <div className="text-sm text-gray-500 py-2">
        No idle apps detected.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-gray-200">
      <table className="min-w-full text-sm" aria-label="idle apps table">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-4 py-3 text-left font-semibold text-gray-600">App slug</th>
            <th className="px-4 py-3 text-left font-semibold text-gray-600">Last request</th>
            <th className="px-4 py-3 text-left font-semibold text-gray-600">Days idle</th>
            <th className="px-4 py-3 text-left font-semibold text-gray-600">Est. monthly savings</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {apps.map((app) => (
            <tr key={app.app_slug} className="hover:bg-gray-50">
              <td className="px-4 py-3 font-mono text-xs">{app.app_slug}</td>
              <td className="px-4 py-3 text-gray-500 text-xs">
                {app.last_request_at
                  ? new Date(app.last_request_at).toLocaleDateString()
                  : 'Never'}
              </td>
              <td className="px-4 py-3">{app.days_idle}</td>
              <td className="px-4 py-3 font-medium text-green-700">
                {formatUsd(app.estimated_monthly_savings_usd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Main Component ────────────────────────────────────────────────────────────

export function CostDashboard() {
  const [selectedMonth, setSelectedMonth] = useState<string>(currentYearMonth);

  const {
    data: summary,
    isLoading: loadingSummary,
    error: summaryError,
  } = useQuery({
    queryKey: ['cost-summary', selectedMonth],
    queryFn: () => getCostSummary(selectedMonth),
    retry: 1,
  });

  const {
    data: idleApps,
    isLoading: loadingIdle,
    error: idleError,
  } = useQuery({
    queryKey: ['cost-idle'],
    queryFn: getIdleApps,
    retry: 1,
  });

  return (
    <div className="space-y-8">
      {/* ── Month picker ── */}
      <div className="flex items-center justify-between">
        <MonthPicker value={selectedMonth} onChange={setSelectedMonth} />
        {summary && (
          <div className="text-sm text-gray-600">
            Grand total:{' '}
            <span className="font-semibold text-gray-900">
              {formatUsd(summary.grand_total_usd)}
            </span>
            {summary.untagged_usd > 0 && (
              <span className="ml-2 text-xs text-orange-600">
                (includes {formatUsd(summary.untagged_usd)} untagged)
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── Cost chart ── */}
      <section aria-label="cost by app">
        <h3 className="text-base font-semibold text-gray-800 mb-3">
          Top 5 Apps by Cost
        </h3>
        {loadingSummary && (
          <div className="text-sm text-gray-600" aria-live="polite">
            Loading cost data…
          </div>
        )}
        {summaryError && (
          <div role="alert" className="rounded-md bg-red-50 p-4 text-sm text-red-800">
            {summaryError instanceof Error
              ? summaryError.message
              : 'Failed to load cost summary'}
          </div>
        )}
        {summary && <CostChart rows={summary.rows} />}
      </section>

      {/* ── Idle apps ── */}
      <section aria-label="idle apps">
        <h3 className="text-base font-semibold text-gray-800 mb-3">
          Idle Apps (30+ days)
        </h3>
        {loadingIdle && (
          <div className="text-sm text-gray-600" aria-live="polite">
            Loading idle apps…
          </div>
        )}
        {idleError && (
          <div role="alert" className="rounded-md bg-red-50 p-4 text-sm text-red-800">
            {idleError instanceof Error
              ? idleError.message
              : 'Failed to load idle apps'}
          </div>
        )}
        {idleApps && <IdleAppsTable apps={idleApps} />}
      </section>
    </div>
  );
}
