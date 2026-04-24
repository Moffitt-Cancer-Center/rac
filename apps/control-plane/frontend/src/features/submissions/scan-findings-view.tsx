// pattern: Functional Core — pure render based on props; no side effects.
/**
 * ScanFindingsView: Renders scan result information based on submission verdict.
 *
 * - scan_rejected:    CVE list sorted by severity desc, with KEV badge + EPSS score
 * - pipeline_error:   Build log download link
 * - passed:           Scan passed badge with finding count
 * - partial_passed:   Passed with Defender-timeout warning badge
 * - others:           Nothing
 */

export interface ScanFinding {
  cve_id?: string;
  severity?: string;
  cvss_score?: number | null;
  epss_score?: number | null;
  is_kev?: boolean;
  package_name?: string;
  package_version?: string;
  fixed_version?: string | null;
}

export interface ScanResult {
  verdict: 'passed' | 'rejected' | 'partial_passed' | 'partial_rejected' | 'build_failed';
  effective_severity: 'none' | 'low' | 'medium' | 'high' | 'critical';
  findings: ScanFinding[];
  build_log_uri?: string | null;
  sbom_uri?: string | null;
  grype_report_uri?: string | null;
  defender_report_uri?: string | null;
  image_digest?: string | null;
  defender_timed_out?: boolean;
}

interface ScanFindingsViewProps {
  scanResult: ScanResult | null | undefined;
}

const SEVERITY_ORDER: Record<string, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
  none: 0,
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-700 text-white',
  high: 'bg-red-500 text-white',
  medium: 'bg-orange-400 text-white',
  low: 'bg-yellow-400 text-black',
  none: 'bg-gray-200 text-gray-700',
};

function SeverityBadge({ severity }: { severity: string }) {
  const sev = severity.toLowerCase();
  const cls = SEVERITY_COLORS[sev] ?? 'bg-gray-200 text-gray-700';
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-bold uppercase ${cls}`}
      aria-label={`severity: ${sev}`}
    >
      {sev.toUpperCase()}
    </span>
  );
}

function ScanPassedView({ findings, defenderTimedOut }: { findings: ScanFinding[]; defenderTimedOut?: boolean }) {
  return (
    <div>
      <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-green-100 text-green-800 font-semibold text-sm">
        Scan Passed
        {findings.length > 0 && (
          <span className="text-xs text-green-600">({findings.length} low-severity finding{findings.length !== 1 ? 's' : ''})</span>
        )}
      </span>
      {defenderTimedOut && (
        <span className="ml-3 inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800 border border-yellow-300">
          Defender scan pending — partial result
        </span>
      )}
    </div>
  );
}

function CVEListView({ findings }: { findings: ScanFinding[] }) {
  const sorted = [...findings].sort((a, b) => {
    const ra = SEVERITY_ORDER[a.severity?.toLowerCase() ?? 'none'] ?? 0;
    const rb = SEVERITY_ORDER[b.severity?.toLowerCase() ?? 'none'] ?? 0;
    return rb - ra;
  });

  if (sorted.length === 0) {
    return <p className="text-sm text-gray-600">No findings recorded.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm border border-gray-200 rounded-md">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-3 py-2 text-left font-semibold text-gray-600">CVE</th>
            <th className="px-3 py-2 text-left font-semibold text-gray-600">Severity</th>
            <th className="px-3 py-2 text-left font-semibold text-gray-600">Package</th>
            <th className="px-3 py-2 text-left font-semibold text-gray-600">Version</th>
            <th className="px-3 py-2 text-left font-semibold text-gray-600">Fixed In</th>
            <th className="px-3 py-2 text-left font-semibold text-gray-600">KEV</th>
            <th className="px-3 py-2 text-left font-semibold text-gray-600">EPSS</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {sorted.map((f, i) => (
            <tr key={`${f.cve_id ?? 'finding'}-${i}`} className="hover:bg-gray-50">
              <td className="px-3 py-2 font-mono text-xs">
                {f.cve_id ?? '—'}
              </td>
              <td className="px-3 py-2">
                {f.severity ? <SeverityBadge severity={f.severity} /> : '—'}
              </td>
              <td className="px-3 py-2 font-mono text-xs">{f.package_name ?? '—'}</td>
              <td className="px-3 py-2 font-mono text-xs">{f.package_version ?? '—'}</td>
              <td className="px-3 py-2 font-mono text-xs text-green-700">
                {f.fixed_version ?? '—'}
              </td>
              <td className="px-3 py-2">
                {f.is_kev ? (
                  <span className="inline-block px-1.5 py-0.5 rounded text-xs font-bold bg-red-900 text-white" aria-label="KEV badge">
                    KEV
                  </span>
                ) : '—'}
              </td>
              <td className="px-3 py-2 text-xs">
                {f.epss_score != null ? (
                  <span className={f.epss_score >= 0.95 ? 'text-red-700 font-bold' : 'text-gray-700'}>
                    {(f.epss_score * 100).toFixed(1)}%
                  </span>
                ) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BuildFailedView({ buildLogUri }: { buildLogUri?: string | null }) {
  return (
    <div className="p-4 bg-red-50 border border-red-200 rounded-md">
      <p className="font-semibold text-red-800 mb-2">Pipeline build failed</p>
      {buildLogUri ? (
        <a
          href={buildLogUri}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm text-blue-600 hover:underline"
        >
          Download build log
        </a>
      ) : (
        <p className="text-sm text-gray-600">Build log not available.</p>
      )}
    </div>
  );
}

/**
 * ScanFindingsView: pure component — renders scan result based on verdict.
 */
export function ScanFindingsView({ scanResult }: ScanFindingsViewProps) {
  if (!scanResult) {
    return null;
  }

  const { verdict, findings, build_log_uri, defender_timed_out } = scanResult;

  if (verdict === 'rejected' || verdict === 'partial_rejected') {
    return (
      <section aria-label="Scan findings">
        <h3 className="text-base font-semibold text-red-700 mb-3">
          Scan Rejected — Security Findings
        </h3>
        <CVEListView findings={findings} />
      </section>
    );
  }

  if (verdict === 'build_failed') {
    return (
      <section aria-label="Build failed">
        <BuildFailedView buildLogUri={build_log_uri} />
      </section>
    );
  }

  if (verdict === 'passed' || verdict === 'partial_passed') {
    return (
      <section aria-label="Scan results">
        <ScanPassedView findings={findings} defenderTimedOut={defender_timed_out} />
      </section>
    );
  }

  return null;
}
