/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  /** Optional Sentinel-5P NO2 WMS GetMap endpoint (Copernicus/GEE). */
  readonly VITE_NO2_WMS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
