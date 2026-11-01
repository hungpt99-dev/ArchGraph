/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#080b12", 900: "#0b0f18", 850: "#0f141f", 800: "#131a27", 700: "#1c2536" },
        accent: { DEFAULT: "#6366f1", soft: "#818cf8", cyan: "#22d3ee" },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: { glow: "0 0 0 1px rgba(99,102,241,.25), 0 8px 40px -12px rgba(99,102,241,.45)" },
    },
  },
  plugins: [],
};
