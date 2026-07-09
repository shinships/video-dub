import { useEffect, useState } from "react";
import { GraphicsCard, UploadSimple, Waveform } from "@phosphor-icons/react";
import { api } from "../api.js";

export function Topbar({ currentJob, health, onSelectJob, onUpload }) {
  const [jobs, setJobs] = useState([]);

  // Nạp lại danh sách khi job hiện tại đổi (vd vừa upload xong).
  useEffect(() => {
    api("/jobs").then(setJobs).catch(() => {});
  }, [currentJob.id]);

  const known = jobs.some((job) => job.id === currentJob.id);

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
          value={currentJob.id}
          onChange={(event) => onSelectJob(event.target.value)}
        >
          {!known && <option value={currentJob.id}>{currentJob.name}</option>}
          {jobs.map((job) => (
            <option key={job.id} value={job.id}>
              {job.name}
            </option>
          ))}
        </select>
        <button className="header-upload" type="button" onClick={onUpload}>
          <UploadSimple weight="bold" /> Upload video
        </button>
        <span className="gpu" title={health.gpu?.memory || ""}>
          <GraphicsCard weight="fill" /> {health.gpu?.name || "Không phát hiện GPU"}
        </span>
        <span className={`ready-dot ${health.ok ? "" : "off"}`} />
        <span>{health.ok ? "Sẵn sàng" : "Backend chưa bật"}</span>
        {health.demo_mode && <span className="demo-pill">Demo mode</span>}
      </div>
    </header>
  );
}
