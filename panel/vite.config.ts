import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// El panel se compila a `src/openlegal/static/app/`, que es lo que sirve FastAPI: el
// navegador habla con el mismo origen, así que la sesión (cookie) viaja sin CORS ni tokens.
// En desarrollo, Vite atiende en su puerto y pasa las llamadas a /api al CRM local.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/openlegal/static/app",
    emptyOutDir: true,
    // Sin mapas de fuente en el artefacto que se sirve al navegador de la oficina: el
    // código es el mismo que está en el repositorio, y no se entrega de más.
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8791", changeOrigin: false },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/pruebas/preparacion.ts"],
  },
});
