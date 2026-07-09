export function Toast({ toast }) {
  // Vùng aria-live luôn hiện diện để screen reader đọc được toast mới.
  return (
    <div className="toast-region" aria-live="polite">
      {toast && <div className={`toast ${toast.variant === "error" ? "toast--error" : ""}`}>{toast.message}</div>}
    </div>
  );
}
