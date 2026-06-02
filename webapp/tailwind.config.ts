import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        // ── Palette Vert Sauge — couleur primaire ───────────────────────
        sage: {
          50:  '#f3f6f0',
          100: '#e4ece0',
          200: '#c9d9c1',
          300: '#a5bf9c',
          400: '#7ea075',
          500: '#5f8356',
          600: '#4b6944',
          700: '#3c5437',
          800: '#30432c',
          900: '#263724',
          950: '#141e13',
        },
        // ── Catégories corrections (inchangées) ────────────────────────
        orthographe: '#ef4444',
        grammaire:   '#f97316',
        typographie: '#8b5cf6',
        style:       '#3b82f6',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        'warm-sm': '0 1px 3px rgba(60,44,28,0.08), 0 1px 2px rgba(60,44,28,0.04)',
        'warm-md': '0 4px 12px rgba(60,44,28,0.10), 0 2px 4px rgba(60,44,28,0.06)',
        'warm-lg': '0 10px 32px rgba(60,44,28,0.12), 0 4px 8px rgba(60,44,28,0.06)',
      },
      borderRadius: {
        '3xl': '1.5rem',
        '4xl': '2rem',
      },
    },
  },
  plugins: [],
}

export default config
