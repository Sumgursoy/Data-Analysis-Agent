import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Backend'e giden her şeyi proxy'le — böylece frontend'de mutlak URL
    // yok, CORS derdi yok, üretimde tek origin altında çalışır.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
