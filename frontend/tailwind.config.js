/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      animation: {
        'pulse-border': 'pulse-border 1.5s ease-in-out infinite',
      },
      keyframes: {
        'pulse-border': {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(59, 130, 246, 0.5)' },
          '50%': { boxShadow: '0 0 0 6px rgba(59, 130, 246, 0)' },
        },
      },
    },
  },
  plugins: [],
}
