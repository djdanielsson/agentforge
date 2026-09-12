/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0a0a0a",
          raised: "#141414",
          border: "#262626",
        },
      },
    },
  },
  plugins: [],
};
