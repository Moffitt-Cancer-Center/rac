# control-plane/frontend — React UI

**Freshness:** 2026-04-24

## Purpose

Single SPA served by the backend's StaticFiles mount at `/`. Handles both researcher workflows (submit, view status, manage tokens, see nudges) and admin workflows (approval queue, ownership transfers, cost dashboard, access log viewer, webhook admin, provisioning retry).

## Stack

- **React 18** + TypeScript, built by Vite.
- **MSAL** (`@azure/msal-browser` + `@azure/msal-react`) for Entra auth; access token attached to all API calls via `lib/api.ts`.
- **TanStack Router** with generated route tree (`routeTree.gen.ts`, auto-generated — don't edit).
- **TanStack Query** for server state; forms via `react-hook-form` + Zod resolvers.
- **Recharts** for the cost dashboard.
- **msw** for test mocking; **vitest** runner; **jsdom** environment.

## Layout

- `routes/` — TanStack Router file-routes. `__root.tsx` is the layout; subfolders mirror URL paths.
- `features/` — One folder per workflow. Contains forms, tables, modals. Keep data-fetching (query hooks) co-located with components.
  - `submissions/` — Submission list + intake form
  - `approval-queue/` — Admin review UI
  - `manifest-form/` — `rac.yaml` form + asset tiles + hash-mismatch card (Phase 8)
  - `nudges/` — Detection finding banners + dismissal UI
  - `tokens/` — Reviewer token mint/list/revoke + access-mode toggle
  - `access-log/` — Filterable paginated log viewer
  - `admin/` — Ownership flags, provisioning retry, webhook admin, cost dashboard
- `lib/api.ts` — Fetch wrapper with MSAL token acquisition and correlation-id surfacing.
- `lib/msal.ts` — MSAL configuration (tenant/client from Vite env vars).

## Build output

`pnpm build` emits to `backend/src/rac_control_plane/static/`. The backend serves `index.html` for all unknown routes (SPA fallback via the `html=True` StaticFiles mount at `/`). Keep this output path in sync — do not redirect the build elsewhere.

## Contracts

- **Auth.** Every API call uses `MsalProvider`'s token silently or falls back to interactive. Never hand-roll an Authorization header.
- **Error surfacing.** The `api.ts` wrapper parses `{code, message, correlation_id}` and throws a typed error; TanStack Query error boundaries render the correlation id for support.
- **Forms.** Form schemas are Zod; on submit they are mapped to the backend manifest via `backend/.../manifest/form_mapper.py` — keep the two in sync when fields change.

## Tests

`pnpm test` runs 84 vitest suites covering feature components with msw-mocked API. There is no backend-integration test here; end-to-end is manual (human test plan).
