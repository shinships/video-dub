import { ArrowRight, DownloadSimple, Gauge, GraphicsCard, SpinnerGap, Waveform } from "@phosphor-icons/react";
import { API } from "../api.js";
import { clockShort, money, tokens } from "../format.js";

const STYLES = ["Tự nhiên", "Truyền cảm", "Tài liệu", "Năng động"];

export function SettingsColumn({ job, health, catalog, busy, onUpdateSettings, onExport, onCancel }) {
  // Chỉ còn một engine TTS (VieNeu local) nên không hiện dropdown chọn engine nữa.
  const voiceList = catalog?.engines?.[0]?.voices || [];
  // Đọc được preset VieNeu -> cho chọn; chưa cài vieneu thì backend chỉ trả một giọng từ
  // env -> hiển thị read-only như cũ.
  const selectableVoices = voiceList.length > 1;
  const defaultVoiceId =
    catalog?.engines?.[0]?.default_voice || catalog?.default_voice || "Minh Quân";
  const currentVoice =
    (job.voice && job.voice !== "Aoede" && voiceList.find((item) => item.id === job.voice)) ||
    voiceList.find((item) => item.id === defaultVoiceId) ||
    voiceList[0];

  const processing = job.status === "processing";
  const completed = job.status === "completed" && job.artifacts?.video;
  // Ước lượng thô: STT + dịch + TTS + render xấp xỉ 8 lần thời lượng video.
  const estimatedMinutes = Math.max(2, Math.round(((job.duration || 0) / 60) * 8));
  // Chi phí ĐO THẬT từ token nhà cung cấp trả về (jobs.cost), không phải số ước lượng cứng.
  // `vnd` null nghĩa là model không có trong bảng giá -> thà không hiện còn hơn hiện số bịa.
  const total = job.cost?.total || { input_tokens: 0, output_tokens: 0, calls: 0 };
  const measured = typeof job.cost?.vnd === "number" && total.calls > 0;
  const costModel = job.cost?.model && job.cost.model !== "(demo)" ? job.cost.model : "";

  return (
    <aside className="settings-column">
      <h2>
        <Waveform /> Giọng & phong cách
      </h2>

      <label className="field-label" htmlFor="voice-select">Giọng nói</label>
      {selectableVoices ? (
        <>
          <select
            id="voice-select"
            value={currentVoice?.id || ""}
            onChange={(event) => onUpdateSettings({ voice: event.target.value })}
          >
            {voiceList.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
          <small className="voice-desc">{currentVoice?.desc}</small>
        </>
      ) : (
        <div className="voice-control">
          <div>
            <b>{currentVoice?.label || "Chưa cấu hình"}</b>
            <small>{currentVoice?.desc || "Đổi giọng qua biến môi trường"}</small>
          </div>
        </div>
      )}

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
          <b>{health?.gpu?.name || "Không phát hiện"}</b>
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
        <h3 className="cost-title">Chi phí API</h3>
        <div>
          <span>Nhận dạng giọng nói (Whisper)</span>
          <span className="free">Miễn phí · local</span>
        </div>
        <div>
          <span>Tách nhạc nền (Demucs)</span>
          <span className="free">Miễn phí · local</span>
        </div>
        <div>
          <span>Tạo giọng (VieNeu)</span>
          <span className="free">Miễn phí · local</span>
        </div>
        <div>
          <span>Dịch{costModel ? ` (${costModel})` : ""}</span>
          <span>{measured ? money(job.cost.vnd) : "—"}</span>
        </div>
        {measured && (
          <div className="cost-tokens">
            <span>Token đã dùng</span>
            <span>
              {tokens(total.input_tokens)} vào · {tokens(total.output_tokens)} ra · {total.calls} lời gọi
            </span>
          </div>
        )}
        <hr />
        <div className="total">
          <b>Tổng cộng{measured ? " (đo thật)" : ""}</b>
          <strong>{measured ? money(job.cost.vnd) : "chưa có"}</strong>
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
