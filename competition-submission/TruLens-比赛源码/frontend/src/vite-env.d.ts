/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Overrides the API base URL; defaults to "/api" (proxied to the backend in dev). */
  readonly VITE_API_BASE?: string;
}
