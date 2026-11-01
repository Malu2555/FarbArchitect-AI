/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts}'],
  theme: {
    extend: {
      colors: {
        fab: {
          bg: '#04070f',
          panel: '#0b1626',
          border: '#1e293b',
          accent: '#14b8a6',
          danger: '#ef4444',
          warn: '#f97316',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
};
