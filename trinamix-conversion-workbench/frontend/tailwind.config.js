/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      colors: {
        // Trinamix design tokens — used purposefully throughout the app
        sidebar: {
          DEFAULT: "#0F172A", // deep slate, dark navigation panel
          hover: "#1E293B",
          active: "#312E81", // indigo-950 mix used as active item bg
        },
        brand: {
          DEFAULT: "#6366F1", // indigo-500 — primary accent
          dark: "#4F46E5",
          light: "#818CF8",
          subtle: "#EEF2FF",
        },
        canvas: "#F8FAFC", // page background
        surface: "#FFFFFF", // card surface
        ink: {
          DEFAULT: "#0F172A",
          muted: "#475569",
          subtle: "#94A3B8",
        },
        line: "#E2E8F0", // borders
        // Status palette — only used for status pills, KPI deltas, severities
        success: { DEFAULT: "#10B981", subtle: "#ECFDF5" },
        warning: { DEFAULT: "#F59E0B", subtle: "#FFFBEB" },
        danger: { DEFAULT: "#EF4444", subtle: "#FEF2F2" },
        info: { DEFAULT: "#3B82F6", subtle: "#EFF6FF" },
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 1px 0 rgb(0 0 0 / 0.02)",
        soft: "0 4px 16px -4px rgb(15 23 42 / 0.08)",
      },
      borderRadius: { md: "8px", lg: "10px", xl: "14px" },
    },
  },
  plugins: [],
};
