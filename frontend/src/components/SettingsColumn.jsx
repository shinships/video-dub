import { ArrowRight, DownloadSimple, Gauge, GraphicsCard, SpinnerGap, Waveform } from "@phosphor-icons/react";
import { API } from "../api.js";
import { clockShort, money } from "../format.js";

const STYLES = ["Tự nhiên", "Truyền cảm", "Tài liệu", "Năng động"];

export function SettingsColumn({ job, health, catalog, busy, onUpdateSettings, onExport, onCancel }) {
  const engineId = job.tts_engine || catalog?.default_engine || "vieneu";
  const engines = catalog?.engines || [];
  const engine = engines.find((item) => item.id === engineId);
  // VieNeu/Vbee mỗi engine chỉ có một giọng cấu hình sẵn (qua env) — hiển thị read-only.
  const currentVoice = engine?.voices?.[0];

  const processing = job.status === "processing";
  const completed = job.status === "completed" && job.artifacts?.video;
  // Ước lượng thô: STT + dịch + TTS + render xấp xỉ 8 lần thời lượng video.
  const estimatedMinutes = Math.max(2, Math.round(((job.duration || 0) / 60) * 8));

  return (
    <aside className="settings-column">
      <h2>
        <Waveform /> Giọng & phong cách
      </h2>

      {engines.length > 0 && (
        <>
          <label className="field-label" htmlFor="engine-select">Engine TTS</label>
          <select
            id="engine-select"
            value={engineId}
            onChange={(event) => onUpdateSettings({ tts_engine: event.target.value })}
          >
            {engines.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </>
      )}

      <label className="field-label">Giọng nói</label>
      <div className="voice-control">
        <div>
          <b>{currentVoice?.label || "Chưa cấu hình"}</b>
          <small>{currentVoice?.desc || "Đổi giọng qua biến môi trường"}</small>
        </div>
      </div>

      <h3>Tùy chọn</h3>
      <label className="field-label" htmlFor="style-select">Phong cách</label>
      <select
        id="style-select"
        value={job.style}
        onChange={(event) => onUpdateSettings({ style: event.target.value })}
      >
        {STYLES.map((style) => (
          <option key={style}>{style}</option>
        ))}
      </select>
      <div className="dual-fields">
        <label>
          <span>Tốc độ</span>
          <select
            value={Number(job.speed ?? 1).toFixed(2)}
            onChange={(event) => onUpdateSettings({ speed: parseFloat(event.target.value) })}
          >
            <option value="0.90">0.90x</option>
            <option value="1.00">1.00x</option>
            <option value="1.10">1.10x</option>
          </select>
        </label>
        <label>
          <span>Cao độ</span>
          <select
            value={String(job.pitch ?? 0)}
            onChange={(event) => onUpdateSettings({ pitch: parseFloat(event.target.value) })}
          >
            <option value="-1">-1</option>
            <option value="0">0</option>
            <option value="1">+1</option>
          </select>
        </label>
      </div>

      <section className="estimate">
        <h3>Ước tính xử lý</h3>
        <div>
          <span>
            <Gauge /> Thời gian xử lý (ước lượng thô)
          </span>
          <b>~ {estimatedMinutes} phút</b>
        </div>
        <div>
          <span>
            <GraphicsCard /> GPU
          </span>
          <b>{health.gpu?.name || "Không phát hiện"}</b>
        </div>
        <div>
          <span>Số câu</span>
          <b>{job.segments?.length || 0} câu</b>
        </div>
        <div>
          <span>Tổng thời lượng video</span>
          <b>{clockShort(job.duration || 0)}</b>
        </div>
        <div>
          <span>Ngôn ngữ đích</span>
          <b>Tiếng Việt</b>
        </div>
        <hr />
        <h3 className="cost-title">Ước tính chi phí</h3>
        <div>
          <span>STT (nhận dạng giọng nói)</span>
          <span>{money(job.cost?.stt)}</span>
        </div>
        <div>
          <span>Dịch thuật</span>
          <span>{money(job.cost?.translation)}</span>
        </div>
        <div>
          <span>TTS (tổng hợp giọng nói)</span>
          <span>{money(job.cost?.tts)}</span>
        </div>
        <hr />
        <div className="total">
          <b>Tổng cộng (ước tính)</b>
          <strong>{money(job.cost?.total)}</strong>
        </div>
      </section>

      {completed ? (
        <>
          <a className="primary-action" href={`${API}/jobs/${job.id}/download`}>
            <DownloadSimple weight="bold" /> Tải MP4
          </a>
          <a className="text-button srt-link" href={`${API}/jobs/${job.id}/download?kind=srt`}>
            Tải phụ đề SRT
          </a>
        </>
      ) : processing ? (
        <>
          <button className="primary-action" disabled>
            <SpinnerGap className="spin" /> Đang xử lý… {job.progress || 0}%
          </button>
          <button className="text-button danger" type="button" onClick={onCancel}>
            Hủy xử lý
          </button>
        </>
      ) : (
        <button className="primary-action" onClick={onExport} disabled={busy === "export"}>
          {busy === "export" ? <SpinnerGap className="spin" /> : <>Tạo giọng & xuất video <ArrowRight /></>}
        </button>
      )}
      {!completed && !processing && (
        <small className="action-note">Bạn sẽ tạo giọng và xem trước trước khi xuất video.</small>
      )}
    </aside>
  );
}
