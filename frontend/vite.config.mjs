import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TOKEN_HEADER = "X-Video-Dub-Token";

let cached = "";

/** Token cục bộ của backend, đọc từ thư mục dữ liệu.
 *
 * Dev server proxy `/api` sang uvicorn và TỰ gắn token, nên trong lúc phát triển token không
 * bao giờ có mặt trong trình duyệt, và frontend gọi cùng origin nên cũng không dính CORS.
 * Đọc lại cho tới khi lấy được: vite hay khởi động trước backend, lúc đó file chưa tồn tại.
 */
function localToken() {
  if (cached) return cached;
  const fromEnv = (process.env.VIDEO_DUB_API_TOKEN || "").trim();
  if (fromEnv) {
    cached = fromEnv;
    return cached;
  }
  const dataDir = process.env.VIDEO_DUB_DATA_DIR || path.resolve(HERE, "..", "data");
  try {
    cached = fs.readFileSync(path.join(dataDir, "api-token"), "utf8").trim();
  } catch {
    cached = ""; // backend chưa chạy lần nào — thử lại ở request sau
  }
  return cached;
}

export default defineConfig({
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
    proxy: {
      "/api": {
        // Đổi được để chạy nhiều instance song song (và cho bản đóng gói sau này).
        target: process.env.VIDEO_DUB_API_TARGET || "http://127.0.0.1:8010",
        changeOrigin: false,
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            const token = localToken();
            if (token) proxyReq.setHeader(TOKEN_HEADER, token);
          });
        },
      },
    },
  },
  plugins: [react()],
});
