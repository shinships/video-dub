import { GearSix, UploadSimple, WarningCircle } from "@phosphor-icons/react";

/** Màn hình thay cho workspace khi chưa có gì để chỉnh sửa.
 *
 * Bản cũ luôn dựng sẵn một dự án bịa ("Productivity Tips.mp4") nên người dùng mới không phân
 * biệt được dữ liệu mẫu với dữ liệu của mình, và cấu hình thiếu thì âm thầm ra kết quả giả.
 */
export function BlankState({ health, onUpload, onOpenSettings }) {
  if (health === null) {
    return (
      <section className="blank-state">
        <p className="blank-hint">Đang kết nối backend…</p>
      </section>
    );
  }

  if (health.offline) {
    return (
      <section className="blank-state">
        <WarningCircle size={40} weight="duotone" />
        <h2>Chưa kết nối được backend</h2>
        <p>Hãy khởi động máy chủ rồi tải lại trang.</p>
        <code className="blank-code">uvicorn app.main:app --port 8010</code>
      </section>
    );
  }

  const missing = health.missing || [];
  if (missing.length > 0) {
    return (
      <section className="blank-state">
        <WarningCircle size={40} weight="duotone" />
        <h2>Cần thiết lập trước khi lồng tiếng</h2>
        <p>Thiếu những thứ sau nên chưa thể xử lý video thật:</p>
        <ul className="blank-missing">
          {missing.map((item) => (
            <li key={item.key}>{item.message}</li>
          ))}
        </ul>
        <button type="button" className="primary-action" onClick={onOpenSettings}>
          <GearSix /> Mở Cài đặt
        </button>
        <p className="blank-hint">
          Khai báo API key ngay trong Cài đặt — key được lưu vào keychain của hệ điều hành và
          có hiệu lực ngay. FFmpeg thì phải cài ngoài rồi mở lại app.
        </p>
      </section>
    );
  }

  return (
    <section className="blank-state">
      <UploadSimple size={40} weight="duotone" />
      <h2>Chưa có dự án nào</h2>
      <p>Tải lên một video tiếng Anh để bắt đầu lồng tiếng Việt.</p>
      <button type="button" className="primary-action" onClick={onUpload}>
        <UploadSimple /> Tải video lên
      </button>
    </section>
  );
}
