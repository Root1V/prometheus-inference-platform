import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // Served under /admin by the gateway (see gateway/src/prometheus_gateway/main.py),
  // not at the domain root.
  base: '/admin/',
  plugins: [react(), tailwindcss()],
  build: {
    // Build output lands where the Python gateway's StaticFiles mount expects it.
    outDir: '../src/prometheus_gateway/admin/static',
    emptyOutDir: true,
  },
})
