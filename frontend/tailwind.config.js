// Шаблонлар ва Python (класс номлари views/templatetags'да ҳам ёзилади) кўриб чиқилади.
// Шаблонда йиғиладиган ранглар (bg-{{ st.1 }}-50 каби) — safelist'да.
const DYNAMIC_COLORS = "slate|sky|emerald|rose|amber|violet|indigo|orange|fuchsia|cyan|stone|ink|brand|ok|warn|bad";

// 3-босқич: 5 та маъноли ранг оиласи (frontend/tokens.py, tokens.css — CSS ўзгарувчилари).
// Эски номлар (sky, violet, green…) ҳам шу оилаларга боғланган — шаблонлардаги минглаб класс ўзгармайди,
// лекин сайтда энди фақат 5 та ранг: ink (кулранг), brand (кўк), ok (яшил), warn (сариқ), bad (қизил).
const SHADES = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"];
const family = (name) => Object.fromEntries(SHADES.map((s) => [s, `rgb(var(--${name}-${s}) / <alpha-value>)`]));
const ink = family("ink"), brand = family("brand"), ok = family("ok"), warn = family("warn"), bad = family("bad");

module.exports = {
  content: [
    "../jobs/templates/**/*.{html,txt}",
    "../panel/templates/**/*.html",
    "../accounts/templates/**/*.html",
    "../texnazor/templates/**/*.html",
    "../support/templates/**/*.html",
    "../narxbaza/templates/**/*.html",
    "../jobs/**/*.py",
    "../panel/**/*.py",
    "../accounts/**/*.py",
    "../resbase/**/*.py",
    "../texnazor/**/*.py",
    "../narxbaza/**/*.py",
  ],
  safelist: [
    { pattern: new RegExp(`^(bg|text|ring|border)-(${DYNAMIC_COLORS})-(50|100|200|500|600|700)$`) },
  ],
  theme: {
    colors: {
      transparent: "transparent", current: "currentColor", inherit: "inherit",
      white: "#fff", black: "#000",
      ink, brand, ok, warn, bad,
      // эски номлар → 5 та оила
      slate: ink, gray: ink, zinc: ink, stone: ink, neutral: ink,
      blue: brand, sky: brand, indigo: brand, violet: brand, purple: brand, fuchsia: brand,
      emerald: ok, green: ok, teal: ok, lime: ok, cyan: ok,
      amber: warn, yellow: warn, orange: warn,
      rose: bad, red: bad, pink: bad,
    },
    extend: {},
  },
  plugins: [],
};
