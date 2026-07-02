import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  ArrowClockwise,
  Bell,
  Check,
  DotsThreeVertical,
  FilmSlate,
  Gauge,
  GraphicsCard,
  Info,
  Lightbulb,
  MagnifyingGlass,
  NotePencil,
  Pause,
  Play,
  Plus,
  Question,
  SpeakerHigh,
  SpinnerGap,
  UploadSimple,
  Waveform,
} from "@phosphor-icons/react";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8010/api";

const fallbackJob = {
  id: "demo",
  name: "Productivity Tips.mp4",
  status: "review",
  stage: "translate",
  progress: 52,
  duration: 84,
  width: 1920,
  height: 1080,
  voice: "Aoede",
  style: "Tự nhiên",
  speed: 1.1,
  pitch: 0,
  cost: { stt: 1680, translation: 2100, tts: 8400, total: 12180 },
  segments: [
    ["In this video, I’m going to share 5 simple productivity tips.", "Trong video này, tôi sẽ chia sẻ 5 mẹo tăng năng suất đơn giản.", 92],
    ["These ideas have changed the way I work.", "Những ý tưởng này đã thay đổi cách tôi làm việc.", 85],
    ["They help me get more done every day.", "Chúng giúp tôi hoàn thành nhiều việc hơn mỗi ngày.", 96],
    ["Tip number one is to plan your day the night before.", "Mẹo đầu tiên là lập kế hoạch cho ngày hôm sau từ tối hôm trước.", 90],
    ["A few minutes of planning can save hours of decision-making.", "Chỉ vài phút lên kế hoạch có thể giúp bạn tiết kiệm hàng giờ đắn đo.", 88],
    ["Tip number two is to focus on one task at a time.", "Mẹo thứ hai là tập trung vào một việc tại một thời điểm.", 93],
    ["Multitasking feels productive, but it usually slows you down.", "Đa nhiệm có vẻ hiệu quả, nhưng thường khiến bạn chậm lại.", 79],
  ].map(([source_text, translated_text, fit_score], index) => ({
    id: `fallback-${index + 1}`,
    position: index + 1,
    start: index * 4.8,
    end: (index + 1) * 4.8,
    source_text,
    translated_text,
    fit_score,
    status: "ready",
  })),
};

const voices = [
  { id: "Aoede", label: "Nữ · Aoede", desc: "Ấm áp, truyền cảm" },
  { id: "Kore", label: "Nữ · Kore", desc: "Rõ ràng, trẻ trung" },
  { id: "Charon", label: "Nam · Charon", desc: "Trầm, điềm tĩnh" },
  { id: "Puck", label: "Nam · Puck", desc: "Năng động, tự nhiên" },
];

const steps = [
  ["Tải video", "Hoàn thành"],
  ["Dịch & chỉnh sửa", "Đang thực hiện"],
  ["Tạo giọng", "Chưa bắt đầu"],
  ["Xuất video", "Chưa bắt đầu"],
];

const money = (value = 0) => `${new Intl.NumberFormat("vi-VN").format(value)} đ`;
const clock = (seconds = 0) => {
  const minutes = Math.floor(seconds / 60);
  const remain = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}.${String(Math.round((seconds % 1) * 1000)).padStart(3, "0")}`;
};

async function api(path, options) {
  const response = await fetch(`${API}${path}`, options);
  if (!response.ok) throw new Error((await response.json()).detail || "Có lỗi xảy ra.");
  return response.json();
}

export function App() {
  const [job, setJob] = useState(fallbackJob);
  const [health, setHealth] = useState({ demo_mode: true, gpu: { available: true, name: "NVIDIA GPU" } });
  const [query, setQuery] = useState("");
  const [playing, setPlaying] = useState(false);
  const [activeSegment, setActiveSegment] = useState(null);
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [autoFit, setAutoFit] = useState(true);

  const refresh = async (id = job.id) => {
    try {
      setJob(await api(`/jobs/${id}`));
    } catch {
      // UI vẫn dùng dữ liệu demo nếu backend chưa bật.
    }
  };

  useEffect(() => {
    api("/health").then(setHealth).catch(() => {});
    api("/jobs/demo").then(setJob).catch(() => {});
  }, []);

  useEffect(() => {
    if (!job.id || job.id.startsWith("fallback")) return undefined;
    const source = new EventSource(`${API}/jobs/${job.id}/events`);
    source.onmessage = () => refresh(job.id);
    return () => source.close();
  }, [job.id]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(""), 2600);
    return () => clearTimeout(timer);
  }, [toast]);

  const visibleSegments = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return job.segments || [];
    return (job.segments || []).filter(
      (segment) =>
        segment.source_text.toLowerCase().includes(needle) ||
        segment.translated_text.toLowerCase().includes(needle),
    );
  }, [job.segments, query]);

  const updateTranslation = (id, value) => {
    setJob((current) => ({
      ...current,
      segments: current.segments.map((segment) =>
        segment.id === id ? { ...segment, translated_text: value } : segment,
      ),
    }));
  };

  const saveTranslation = async (segment) => {
    if (segment.id.startsWith("fallback")) return;
    try {
      const updated = await api(`/jobs/${job.id}/segments/${segment.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ translated_text: segment.translated_text }),
      });
      setJob(updated);
      setToast("Đã lưu bản dịch");
    } catch (error) {
      setToast(error.message);
    }
  };

  const regenerate = async (segment) => {
    setBusy(segment.id);
    setJob((current) => ({
      ...current,
      segments: current.segments.map((item) =>
        item.id === segment.id ? { ...item, status: "processing" } : item,
      ),
    }));
    try {
      if (!segment.id.startsWith("fallback")) {
        await api(`/jobs/${job.id}/segments/${segment.id}/regenerate`, { method: "POST" });
        setTimeout(() => refresh(job.id), 1000);
      } else {
        await new Promise((resolve) => setTimeout(resolve, 700));
      }
      setToast("Đã tạo lại đoạn giọng");
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy("");
      setJob((current) => ({
        ...current,
        segments: current.segments.map((item) =>
          item.id === segment.id ? { ...item, status: "ready" } : item,
        ),
      }));
    }
  };

  const updateJobSettings = async (values) => {
    setJob((current) => ({ ...current, ...values }));
    if (job.id === "demo" || job.id.startsWith("fallback")) return;
    try {
      setJob(
        await api(`/jobs/${job.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(values),
        }),
      );
    } catch (error) {
      setToast(error.message);
    }
  };

  const upload = async (file) => {
    if (!file) return;
    setBusy("upload");
    const data = new FormData();
    data.append("file", file);
    data.append("voice", job.voice);
    data.append("style", job.style);
    try {
      const created = await api("/jobs", { method: "POST", body: data });
      setJob(created);
      setUploadOpen(false);
      setToast("Đã tải video, pipeline đang chạy");
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy("");
    }
  };

  const exportVideo = async () => {
    setBusy("export");
    try {
      if (job.id.startsWith("fallback")) {
        await new Promise((resolve) => setTimeout(resolve, 900));
      } else {
        await api(`/jobs/${job.id}/export`, { method: "POST" });
      }
      setToast(health.demo_mode ? "Demo export xong — cấu hình Cloud để render MP4 thật" : "Đang render video…");
    } catch (error) {
      setToast(error.message);
    } finally {
      setBusy("");
    }
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark"><Waveform weight="bold" /></span>
          <span>Lồng Tiếng <b>AI</b></span>
        </div>
        <div className="system-status">
          <button className="header-upload" type="button" onClick={() => setUploadOpen(true)}>
            <UploadSimple weight="bold" /> Upload video
          </button>
          <span className="gpu"><GraphicsCard weight="fill" /> NVIDIA GPU</span>
          <span className="ready-dot" />
          <span>Sẵn sàng</span>
          {health.demo_mode && <span className="demo-pill">Demo mode</span>}
          <button className="icon-button" title="Trợ giúp"><Question /></button>
          <button className="icon-button" title="Thông báo"><Bell /></button>
          <button className="avatar">NV</button>
        </div>
      </header>

      <nav className="stepper" aria-label="Tiến trình dự án">
        {steps.map(([title, subtitle], index) => (
          <div className={`step ${index === 1 ? "active" : ""} ${index === 0 ? "done" : ""}`} key={title}>
            <span className="step-number">{index === 0 ? <Check weight="bold" /> : index + 1}</span>
            <span><b>{title}</b><small>{subtitle}</small></span>
            {index < steps.length - 1 && <span className="step-line" />}
          </div>
        ))}
      </nav>

      <section className="workspace">
        <aside className="preview-column">
          <div className="file-title">
            <div><b>{job.name}</b><button className="edit-name"><NotePencil /></button></div>
            <small><FilmSlate /> MP4 · {job.width || 1920}×{job.height || 1080} · 16:9 · 01:24</small>
          </div>
          <div className="video-frame">
            <img src="/productivity-presenter.png" alt="Người dẫn video productivity" />
          </div>
          <div className="player-controls">
            <button onClick={() => setPlaying(!playing)}>{playing ? <Pause weight="fill" /> : <Play weight="fill" />}</button>
            <span>00:12 / 01:24</span>
            <SpeakerHigh weight="fill" />
            <div className="player-track"><span /></div>
          </div>
          <section className="source-script">
            <div className="section-label"><b>Bản gốc (tiếng Anh)</b><span>CC</span></div>
            <p>{(job.segments || []).slice(0, 3).map((item) => item.source_text).join(" ")}</p>
          </section>
          <section className="tip">
            <b><Lightbulb weight="fill" /> Mẹo</b>
            <p>Nhấn Shift + Enter để xuống dòng. Nhấn Enter để lưu và chuyển sang câu tiếp theo.</p>
          </section>
        </aside>

        <section className="editor-column">
          <div className="editor-heading">
            <div>
              <h1>Chỉnh sửa bản dịch</h1>
              <p>Kiểm tra, chỉnh sửa và tối ưu bản dịch cho khớp thời lượng.</p>
            </div>
            <div className="heading-actions">
              <label className="search"><MagnifyingGlass /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm & thay thế" /></label>
              <button className="icon-button"><DotsThreeVertical /></button>
            </div>
          </div>

          <div className="transcript-table">
            <div className="table-head">
              <span>#</span><span>Thời gian</span><span>Tiếng Anh (gốc)</span><span>Tiếng Việt (dịch)</span><span>Độ khớp thời lượng</span><span>Tác vụ</span>
            </div>
            <div className="table-body">
              {visibleSegments.map((segment) => {
                const fitClass = segment.fit_score >= 85 ? "good" : "warn";
                return (
                  <article className={`segment-row ${activeSegment === segment.id ? "selected" : ""}`} key={segment.id}>
                    <span className="position">{segment.position}</span>
                    <span className="time">{clock(segment.start)}<br />– {clock(segment.end)}</span>
                    <p className="source-copy">{segment.source_text}</p>
                    <div className="translation-box">
                      <textarea
                        value={segment.translated_text}
                        onFocus={() => setActiveSegment(segment.id)}
                        onChange={(event) => updateTranslation(segment.id, event.target.value)}
                        onBlur={() => saveTranslation(segment)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" && !event.shiftKey) {
                            event.preventDefault();
                            event.currentTarget.blur();
                          }
                        }}
                      />
                      <small>{segment.translated_text.length}/{Math.max(60, Math.round((segment.end - segment.start) * 13))}</small>
                    </div>
                    <div className={`fit ${fitClass}`}>
                      <b>{segment.fit_score}%</b>
                      <span className="fit-bar"><i style={{ width: `${segment.fit_score}%` }} /></span>
                      <small>{fitClass === "good" ? "Rất tốt" : "Ổn"}</small>
                    </div>
                    <div className="row-actions">
                      <button title="Nghe đoạn" onClick={() => setActiveSegment(segment.id)}><Play weight="fill" /></button>
                      <button title="Tạo lại" onClick={() => regenerate(segment)} disabled={busy === segment.id}>
                        {busy === segment.id || segment.status === "processing" ? <SpinnerGap className="spin" /> : <ArrowClockwise />}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
          <div className="editor-footer">
            <button className="add-row"><Plus /> Thêm câu mới</button>
            <span>Tổng: {job.segments?.length || 0} câu</span>
            <span>Tổng thời lượng: 01:24.000</span>
          </div>
        </section>

        <aside className="settings-column">
          <h2><Waveform /> Giọng & phong cách</h2>
          <label className="field-label">Giọng nói</label>
          <div className="voice-control">
            <img src="/aoede-avatar.png" alt="" />
            <div>
              <b>{voices.find((voice) => voice.id === job.voice)?.label}</b>
              <small>{voices.find((voice) => voice.id === job.voice)?.desc}</small>
            </div>
            <select aria-label="Giọng nói" value={job.voice} onChange={(event) => updateJobSettings({ voice: event.target.value })}>
              {voices.map((voice) => <option value={voice.id} key={voice.id}>{voice.label} — {voice.desc}</option>)}
            </select>
          </div>
          <button className="preview-voice" onClick={() => setToast("Đang phát giọng mẫu Aoede")}><Play weight="fill" /> Nghe thử giọng</button>

          <h3>Tùy chọn</h3>
          <label className="field-label">Phong cách</label>
          <select value={job.style} onChange={(event) => updateJobSettings({ style: event.target.value })}>
            <option>Tự nhiên</option><option>Truyền cảm</option><option>Tài liệu</option><option>Năng động</option>
          </select>
          <div className="dual-fields">
            <label><span>Tốc độ</span>
              <select value={Number(job.speed ?? 1).toFixed(2)} onChange={(event) => updateJobSettings({ speed: parseFloat(event.target.value) })}>
                <option value="0.90">0.90x</option><option value="1.00">1.00x</option><option value="1.10">1.10x</option>
              </select>
            </label>
            <label><span>Cao độ</span>
              <select value={String(job.pitch ?? 0)} onChange={(event) => updateJobSettings({ pitch: parseFloat(event.target.value) })}>
                <option value="-1">-1</option><option value="0">0</option><option value="1">+1</option>
              </select>
            </label>
          </div>
          <label className="toggle-row">
            <span>Tự động tinh chỉnh thời lượng (TTS) <Info /></span>
            <input type="checkbox" checked={autoFit} onChange={() => setAutoFit(!autoFit)} />
          </label>

          <section className="estimate">
            <h3>Ước tính xử lý</h3>
            <div><span><Gauge /> Thời gian xử lý (dự kiến)</span><b>~ 11 phút</b></div>
            <div><span><GraphicsCard /> GPU</span><b>{health.gpu?.name || "NVIDIA GPU"}</b></div>
            <div><span>Số câu</span><b>{job.segments?.length || 0} câu</b></div>
            <div><span>Tổng thời lượng video</span><b>01:24</b></div>
            <div><span>Ngôn ngữ đích</span><b>Tiếng Việt</b></div>
            <hr />
            <h3 className="cost-title">Ước tính chi phí</h3>
            <div><span>STT (nhận dạng giọng nói)</span><span>{money(job.cost?.stt)}</span></div>
            <div><span>Dịch thuật</span><span>{money(job.cost?.translation)}</span></div>
            <div><span>TTS (tổng hợp giọng nói)</span><span>{money(job.cost?.tts)}</span></div>
            <hr />
            <div className="total"><b>Tổng cộng (ước tính)</b><strong>{money(job.cost?.total)}</strong></div>
          </section>

          <button className="primary-action" onClick={exportVideo} disabled={busy === "export"}>
            {busy === "export" ? <SpinnerGap className="spin" /> : "Tiếp tục: Tạo giọng"} <ArrowRight />
          </button>
          <small className="action-note">Bạn sẽ tạo giọng và xem trước trước khi xuất video.</small>
        </aside>
      </section>

      {toast && <div className="toast">{toast}</div>}

      {uploadOpen && (
        <div className="modal-backdrop" onMouseDown={() => setUploadOpen(false)}>
          <section
            className="upload-modal"
            onMouseDown={(event) => event.stopPropagation()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              upload(event.dataTransfer.files?.[0]);
            }}
          >
            <span className="upload-icon"><UploadSimple /></span>
            <h2>Tải video tiếng Anh</h2>
            <p>MP4, MKV hoặc MOV · tối đa 30 phút<br />Có thể kéo-thả video vào đây</p>
            <label className="primary-action file-picker" htmlFor="video-file-input">
              {busy === "upload" ? <SpinnerGap className="spin" /> : <UploadSimple />} Chọn video
            </label>
            <input
              id="video-file-input"
              className="visually-hidden"
              type="file"
              accept="video/mp4,video/x-matroska,video/quicktime,.mp4,.mkv,.mov"
              disabled={busy === "upload"}
              onChange={(event) => upload(event.target.files?.[0])}
            />
            <button className="text-button" onClick={() => setUploadOpen(false)}>Đóng</button>
          </section>
        </div>
      )}
    </main>
  );
}
