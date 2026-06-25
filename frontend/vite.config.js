import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  // tailwindcss() scans the source for class names and generates the CSS on the
  // fly during dev/build. In Tailwind v4 this plugin replaces the old
  // postcss.config.js + tailwind.config.js setup.
  plugins: [react(), tailwindcss()],
})
