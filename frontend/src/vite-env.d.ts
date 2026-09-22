/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  /** Optional NASA FIRMS MAP_KEY; enables the keyed area API over the open archive. */
  readonly VITE_FIRMS_MAP_KEY?: string
  /** Optional self-hosted proxy returning the FIRMS CSV (wins over the key). */
  readonly VITE_FIRMS_ENDPOINT?: string
  /** Optional Sentinel-5P NO2 WMS GetMap endpoint (Copernicus/GEE). */
  readonly VITE_NO2_WMS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
