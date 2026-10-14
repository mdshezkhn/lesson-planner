import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server is LAN-exposed (host: true) so a phone on the same Wi-Fi can open
// the app directly. /analyze, /generate and /health are proxied to the FastAPI
// backend on :8000, so the phone only ever needs ONE URL — no CORS, and the
// frontend's same-origin API default just works.
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/analyze': 'http://localhost:8000',
      '/generate': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
