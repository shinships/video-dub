// Mặc định gọi CÙNG ORIGIN: dev server proxy /api sang uvicorn và tự gắn token cục bộ, nên
// token không bao giờ nằm trong JS của trình duyệt, và cũng không còn request cross-origin nào.
export const API = import.meta.env.VITE_API_URL || "/api";

export async function api(path, options) {
  // Cookie token do backend phát: cần cho <video src>, link tải và EventSource — những chỗ
  // không đặt được header.
  const response = await fetch(`${API}${path}`, { credentials: "include", ...options });
  if (!response.ok) {
    let detail = "Có lỗi xảy ra.";
    try {
      detail = (await response.json()).detail || detail;
    } catch {
      // Lỗi không phải JSON (backend tắt/proxy) — giữ thông báo chung.
    }
    throw new Error(detail);
  }
  return response.json();
}

export const patchJson = (path, body) =>
  api(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const putJson = (path, body) =>
  api(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
