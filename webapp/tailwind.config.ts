import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        orthographe: '#ef4444',
        grammaire: '#f97316',
        typographie: '#3b82f6',
        style: '#22c55e',
      },
    },
  },
  plugins: [],
}

export default config
