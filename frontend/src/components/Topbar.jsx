import { useEffect, useState } from "react";
import { GearSix, GraphicsCard, UploadSimple, Waveform } from "@phosphor-icons/react";
import { api } from "../api.js";

/** Nhãn trạng thái: phân biệt rõ "chưa bật backend", "thiếu cấu hình" và "sẵn sàng" —
 * trước đây thiếu cấu hình cũng hiện "Sẵn sàng" vì chỉ xét health.ok. */
function statusLabel(health) {
  if (!health) return "Đang kết nối…";
  if (health.offline || !health.ok) return "Backend chưa bật";
  if (health.missing?.length) return "Thiếu cấu hình";
  return "Sẵn sàng";
}

export function Topbar({ currentJob, health, onSelectJob, onUpload, onOpenSettings }) {
  const [jobs, setJobs] = useState([]);

  // Nạp lại danh sách khi job hiện tại đổi (vd vừa upload xong).
  useEffect(() => {
    api("/jobs").then(setJobs).catch(() => {});
  }, [currentJob?.id]);

  // currentJob = null khi chưa có dự án nào (thư viện rỗng / chưa cấu hình).
  const known = jobs.some((job) => job.id === currentJob?.id);

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">
          <Waveform weight="bold" />
        </span>
        <span>
          Lồng Tiếng <b>AI</b>
        </span>
      </div>
      <div className="system-status">
        <select
          className="job-picker"
          aria-label="Chọn dự án"
          value={currentJob?.id || ""}
          disabled={!currentJob && jobs.length === 0}
          onChange={(event) => onSelectJob(event.target.value)}
        >
          {!currentJob && <option value="">Chưa có dự án</option>}
          {currentJob && !known && <option value={currentJob.id}>{currentJob.name}</option>}
          {jobs.map((job) => (
            <option key={job.id} value={job.id}>
              {job.name}
            </option>
          ))}
        </select>
        <button className="header-upload" type="button" onClick={onUpload}>
          <UploadSimple weight="bold" /> Upload video
        </button>
        <button className="header-settings" type="button" onClick={onOpenSettings} title="Cài đặt">
          <GearSix weight="bold" />
        </button>
        <span className="gpu" title={health?.gpu?.memory || ""}>
          <GraphicsCard weight="fill" /> {health?.gpu?.name || "Không phát hiện GPU"}
        </span>
        <span className={`ready-dot ${health?.ready ? "" : "off"}`} />
        <span>{statusLabel(health)}</span>
        {health?.demo_mode && <span className="demo-pill">Demo mode</span>}
      </div>
    </header>
  );
}
