import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Relative base so the build works on GitHub Pages project sites
  // (mdshezkhn.github.io/lesson-planner/) and any other host without changes.
  base: './',
})
