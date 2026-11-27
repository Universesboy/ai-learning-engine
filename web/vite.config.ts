import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Built files go to lengine/static/dist and are committed, so the app runs with Python alone.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../lengine/static/dist', emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
