export const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8010/api";

export async function api(path, options) {
  const response = await fetch(`${API}${path}`, options);
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
