import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // MapLibre starts its worker as a module worker (see components/MapView.tsx).
  worker: { format: 'es' },
})
