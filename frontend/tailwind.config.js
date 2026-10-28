// Шаблонлар ва Python (класс номлари views/templatetags'да ҳам ёзилади) кўриб чиқилади.
// Шаблонда йиғиладиган ранглар (bg-{{ st.1 }}-50 каби) — safelist'да.
const DYNAMIC_COLORS = "slate|sky|emerald|rose|amber|violet|indigo|orange|fuchsia|cyan|stone";

module.exports = {
  content: [
    "../jobs/templates/**/*.{html,txt}",
    "../panel/templates/**/*.html",
    "../accounts/templates/**/*.html",
    "../jobs/**/*.py",
    "../panel/**/*.py",
    "../accounts/**/*.py",
  ],
  safelist: [
    { pattern: new RegExp(`^(bg|text|ring|border)-(${DYNAMIC_COLORS})-(50|100|200|500|600|700)$`) },
  ],
  theme: { extend: {} },
  plugins: [],
};
