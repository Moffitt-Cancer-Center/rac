/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_TENANT_ID: string;
  readonly VITE_FRONTEND_CLIENT_ID: string;
  // One of VITE_API_SCOPE or VITE_API_APP_ID is required at build time.
  // VITE_API_SCOPE: full scope URI, e.g. api://<guid>/submit (overrides VITE_API_APP_ID).
  // VITE_API_APP_ID: API app reg client ID; the scope is built as api://<id>/submit.
  readonly VITE_API_SCOPE?: string;
  readonly VITE_API_APP_ID?: string;
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_INSTITUTION_NAME?: string;
  readonly VITE_BRAND_LOGO_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
