/** Design tokens live in src/input.css as CSS variables (light + dark); Tailwind only references them. */
const token = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

module.exports = {
  darkMode: 'class',
  content: ['../web/index.html', '../web/js/**/*.js'],
  // Component classes are assembled in JS ('btn-' + variant), so Tailwind cannot see them: keep them explicitly.
  safelist: [{ pattern: /^(btn|badge|alert)-(primary|secondary|ghost|danger|danger-soft|sm|icon|neutral|brand|ok|warn|bad|info)$/ }],
  theme: {
    extend: {
      colors: {
        bg: token('bg'),           // app background
        surface: token('surface'), // cards, inputs, dialogs
        sunken: token('sunken'),   // hover / subtle fills
        line: token('line'),       // borders and dividers
        ink: token('ink'),         // primary text
        muted: token('muted'),     // secondary text
        subtle: token('subtle'),   // placeholders, disabled
        brand: { DEFAULT: token('brand'), hover: token('brand-hover'), soft: token('brand-soft'), ink: token('brand-ink') },
        ok: { DEFAULT: token('ok'), soft: token('ok-soft') },
        warn: { DEFAULT: token('warn'), soft: token('warn-soft') },
        bad: { DEFAULT: token('bad'), soft: token('bad-soft') },
        info: { DEFAULT: token('info'), soft: token('info-soft') },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Cascadia Code', 'Consolas', 'Menlo', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgb(0 0 0 / 0.04), 0 1px 1px rgb(0 0 0 / 0.03)',
        pop: '0 20px 40px -12px rgb(0 0 0 / 0.35)',
      },
      keyframes: {
        bar: { from: { transform: 'translateX(-100%)' }, to: { transform: 'translateX(300%)' } },
        'fade-up': { from: { opacity: 0, transform: 'translateY(6px)' }, to: { opacity: 1, transform: 'none' } },
        'slide-in': { from: { opacity: 0, transform: 'translateX(16px)' }, to: { opacity: 1, transform: 'none' } },
      },
      animation: { bar: 'bar 1s ease-in-out infinite', 'fade-up': 'fade-up .18s ease-out', 'slide-in': 'slide-in .2s ease-out' },
    },
  },
  plugins: [],
};
