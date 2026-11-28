import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" so the built files load from whatever path Streamlit serves the component at; fonts up to
// 120 kB are embedded in the CSS, so the site does not depend on Streamlit serving font files
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true, assetsInlineLimit: 120000 },
});
