import { Check } from "@phosphor-icons/react";

const STEPS = ["Tải video", "Dịch & chỉnh sửa", "Tạo giọng", "Xuất video"];

// Thứ tự stage của pipeline để suy ra bước hiện tại.
const STAGE_RANK = {
  probe: 1,
  separate: 1,
  transcribe: 1,
  translate: 1,
  voice: 2,
  export: 3,
};

// Trả về index bước đang active; các bước trước đó coi là done.
function activeStep(job) {
  if (job.status === "completed") return STEPS.length; // tất cả done
  if (job.status === "queued") return 0;
  if (job.status === "review") return 1;
  if (job.status === "processing") return STAGE_RANK[job.stage] ?? 1;
  return 1; // failed/cancelled: dừng ở bước đang dịch
}

export function Stepper({ job, statusMessage }) {
  const current = activeStep(job);
  const processing = job.status === "processing";
  return (
    <>
      <nav className="stepper" aria-label="Tiến trình dự án">
        {STEPS.map((title, index) => {
          const done = index < current;
          const active = index === current;
          const subtitle = done ? "Hoàn thành" : active ? "Đang thực hiện" : "Chưa bắt đầu";
          return (
            <div className={`step ${active ? "active" : ""} ${done ? "done" : ""}`} key={title}>
              <span className="step-number">{done ? <Check weight="bold" /> : index + 1}</span>
              <span>
                <b>{title}</b>
                <small>{subtitle}</small>
              </span>
              {index < STEPS.length - 1 && <span className="step-line" />}
            </div>
          );
        })}
      </nav>
      {processing && (
        <div className="pipeline-progress" role="status">
          <div className="pipeline-progress-bar">
            <i style={{ width: `${job.progress || 0}%` }} />
          </div>
          <span>
            {statusMessage || "Đang xử lý…"} ({job.progress || 0}%)
          </span>
        </div>
      )}
    </>
  );
}
