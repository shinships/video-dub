import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";
import { API, api, patchJson } from "./api.js";
import { clockShort } from "./format.js";
import { Topbar } from "./components/Topbar.jsx";
import { Stepper } from "./components/Stepper.jsx";
import { PreviewColumn } from "./components/PreviewColumn.jsx";
import { TranscriptTable } from "./components/TranscriptTable.jsx";
import { SettingsColumn } from "./components/SettingsColumn.jsx";
import { UploadModal } from "./components/UploadModal.jsx";
import { Toast } from "./components/Toast.jsx";

// Dữ liệu demo để UI chạy được khi backend chưa bật (id "fallback-*" = không gọi API).
const fallbackJob = {
  id: "fallback-demo",
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

const LAST_JOB_KEY = "videodub:lastJob";

export function App() {
  const [job, setJob] = useState(fallbackJob);
  const [health, setHealth] = useState({ ok: false, demo_mode: true, gpu: { available: false, name: "…" } });
  const [catalog, setCatalog] = useState(null);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [activeSegmentId, setActiveSegmentId] = useState(null);
  const [playingSegmentId, setPlayingSegmentId] = useState(null);
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  const seekRef = useRef(null);
  const audioRef = useRef(null);
  const playingRef = useRef(null);
  const jobIdRef = useRef(job.id);
  jobIdRef.current = job.id;

  const showToast = useCallback((message, variant = "success") => {
    setToast({ message, variant });
  }, []);

  const stopSegmentAudio = useCallback(() => {
    audioRef.current?.pause();
    playingRef.current = null;
    setPlayingSegmentId(null);
  }, []);

  const applyJob = useCallback(
    (data) => {
      setJob(data);
      setActiveSegmentId(null);
      stopSegmentAudio();
      if (!data.id.startsWith("fallback")) localStorage.setItem(LAST_JOB_KEY, data.id);
    },
    [stopSegmentAudio],
  );

  const loadJob = useCallback(
    (id) => {
      api(`/jobs/${id}`)
        .then(applyJob)
        .catch((error) => showToast(error.message, "error"));
    },
    [applyJob, showToast],
  );

  useEffect(() => {
    api("/health").then(setHealth).catch(() => {});
    api("/voices").then(setCatalog).catch(() => {});
    const last = localStorage.getItem(LAST_JOB_KEY) || "demo";
    api(`/jobs/${last}`)
      .then(applyJob)
      .catch(() => {
        // Job cũ có thể đã bị xoá — quay về job demo; backend tắt thì giữ fallback.
        if (last !== "demo") api("/jobs/demo").then(applyJob).catch(() => {});
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // SSE: cập nhật tại chỗ từ payload event, chỉ GET lại job ở các mốc lớn.
  useEffect(() => {
    if (!job.id || job.id.startsWith("fallback")) return undefined;
    const jobId = job.id;
    let source;
    let timer;
    let attempts = 0;
    let closed = false;

    const refresh = () =>
      api(`/jobs/${jobId}`)
        .then((data) => setJob((current) => (current.id === jobId ? data : current)))
        .catch(() => {});

    const handle = (event) => {
      if (event.type === "progress") {
        setStatusMessage(event.message || "");
        setJob((current) =>
          current.id === jobId
            ? { ...current, stage: event.stage, progress: event.progress, status: "processing", error: null }
            : current,
        );
      } else if (event.type === "segment") {
        setJob((current) =>
          current.id === jobId
            ? {
                ...current,
                segments: (current.segments || []).map((segment) =>
                  segment.id === event.segment_id ? { ...segment, status: event.status } : segment,
                ),
              }
            : current,
        );
        if (event.status === "ready") refresh();
      } else if (["ready", "completed", "error", "cancelled"].includes(event.type)) {
        refresh();
      }
    };

    const connect = () => {
      if (closed) return;
      source = new EventSource(`${API}/jobs/${jobId}/events`);
      source.onopen = () => {
        attempts = 0;
      };
      source.onmessage = (message) => {
        try {
          handle(JSON.parse(message.data));
        } catch {
          // Bỏ qua event không phải JSON (ping).
        }
      };
      source.onerror = () => {
        source.close();
        timer = setTimeout(connect, Math.min(15000, 1000 * 2 ** attempts++));
      };
    };
    connect();
    return () => {
      closed = true;
      source?.close();
      clearTimeout(timer);
    };
  }, [job.id]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 2600);
    return () => clearTimeout(timer);
  }, [toast]);

  const visibleSegments = useMemo(() => {
    const needle = deferredQuery.trim().toLowerCase();
    if (!needle) return job.segments || [];
    return (job.segments || []).filter(
      (segment) =>
        segment.source_text.toLowerCase().includes(needle) ||
        segment.translated_text.toLowerCase().includes(needle),
    );
  }, [job.segments, deferredQuery]);

  const activeSegment = useMemo(
    () => (job.segments || []).find((segment) => segment.id === activeSegmentId) || null,
    [job.segments, activeSegmentId],
  );

  const saveTranslation = useCallback(
    async (segment, text) => {
      setJob((current) => ({
        ...current,
        segments: current.segments.map((item) =>
          item.id === segment.id ? { ...item, translated_text: text } : item,
        ),
      }));
      if (segment.id.startsWith("fallback")) return;
      try {
        const updated = await patchJson(`/jobs/${jobIdRef.current}/segments/${segment.id}`, {
          translated_text: text,
        });
        setJob(updated);
        showToast("Đã lưu bản dịch");
      } catch (error) {
        showToast(error.message, "error");
      }
    },
    [showToast],
  );

  const regenerate = useCallback(
    async (segment) => {
      setBusy(segment.id);
      setJob((current) => ({
        ...current,
        segments: current.segments.map((item) =>
          item.id === segment.id ? { ...item, status: "processing" } : item,
        ),
      }));
      try {
        if (!segment.id.startsWith("fallback")) {
          await api(`/jobs/${jobIdRef.current}/segments/${segment.id}/regenerate`, { method: "POST" });
          // SSE event "segment" sẽ cập nhật trạng thái và refresh khi xong.
          showToast("Đang tạo lại đoạn giọng…");
        } else {
          await new Promise((resolve) => setTimeout(resolve, 700));
          showToast("Đã tạo lại đoạn giọng");
        }
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        setBusy("");
        if (segment.id.startsWith("fallback")) {
          setJob((current) => ({
            ...current,
            segments: current.segments.map((item) =>
              item.id === segment.id ? { ...item, status: "ready" } : item,
            ),
          }));
        }
      }
    },
    [showToast],
  );

  const updateJobSettings = useCallback(
    async (values) => {
      setJob((current) => ({ ...current, ...values }));
      if (jobIdRef.current.startsWith("fallback")) return;
      try {
        setJob(await patchJson(`/jobs/${jobIdRef.current}`, values));
      } catch (error) {
        showToast(error.message, "error");
      }
    },
    [showToast],
  );

  const upload = useCallback(
    async (file) => {
      if (!file) return;
      setBusy("upload");
      const data = new FormData();
      data.append("file", file);
      data.append("voice", job.voice);
      data.append("style", job.style);
      try {
        const created = await api("/jobs", { method: "POST", body: data });
        applyJob(created);
        setUploadOpen(false);
        showToast("Đã tải video, pipeline đang chạy");
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        setBusy("");
      }
    },
    [job.voice, job.style, applyJob, showToast],
  );

  const exportVideo = useCallback(async () => {
    setBusy("export");
    try {
      if (jobIdRef.current.startsWith("fallback")) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        showToast("Demo export xong — cấu hình Cloud để render MP4 thật");
      } else {
        await api(`/jobs/${jobIdRef.current}/export`, { method: "POST" });
        showToast(health.demo_mode ? "Demo export — cấu hình Cloud để render MP4 thật" : "Đang tạo giọng và render video…");
        // Demo-mode export không bắn SSE — refresh trễ để lấy trạng thái completed.
        const jobId = jobIdRef.current;
        setTimeout(() => {
          api(`/jobs/${jobId}`)
            .then((data) => setJob((current) => (current.id === jobId ? data : current)))
            .catch(() => {});
        }, 1500);
      }
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusy("");
    }
  }, [health.demo_mode, showToast]);

  const cancelJob = useCallback(async () => {
    try {
      await api(`/jobs/${jobIdRef.current}/cancel`, { method: "POST" });
      showToast("Đã gửi yêu cầu hủy");
    } catch (error) {
      showToast(error.message, "error");
    }
  }, [showToast]);

  const retryJob = useCallback(async () => {
    try {
      await api(`/jobs/${jobIdRef.current}/retry`, { method: "POST" });
      setJob((current) => ({ ...current, status: "queued", error: null }));
      showToast("Đã đưa job vào hàng đợi lại");
    } catch (error) {
      showToast(error.message, "error");
    }
  }, [showToast]);

  const selectSegment = useCallback((segment) => setActiveSegmentId(segment.id), []);

  const seekToSegment = useCallback((segment) => {
    setActiveSegmentId(segment.id);
    seekRef.current?.(segment.start);
  }, []);

  const followSegment = useCallback((segmentId) => setActiveSegmentId(segmentId), []);

  const playSegmentAudio = useCallback(
    (segment) => {
      if (!audioRef.current) audioRef.current = new Audio();
      const audio = audioRef.current;
      if (playingRef.current === segment.id) {
        stopSegmentAudio();
        return;
      }
      audio.src = `${API}/jobs/${jobIdRef.current}/segments/${segment.id}/audio`;
      audio.onended = () => {
        playingRef.current = null;
        setPlayingSegmentId(null);
      };
      audio.onerror = () => {
        playingRef.current = null;
        setPlayingSegmentId(null);
        showToast("Không phát được audio của câu này.", "error");
      };
      audio.play().catch(() => {});
      playingRef.current = segment.id;
      setPlayingSegmentId(segment.id);
      setActiveSegmentId(segment.id);
    },
    [showToast, stopSegmentAudio],
  );

  return (
    <main className="app-shell">
      <Topbar currentJob={job} health={health} onSelectJob={loadJob} onUpload={() => setUploadOpen(true)} />
      <Stepper job={job} statusMessage={statusMessage} />

      {(job.status === "failed" || job.status === "cancelled") && (
        <div className="error-banner" role="alert">
          <span>{job.status === "failed" ? `Lỗi: ${job.error || "Pipeline thất bại."}` : "Job đã bị hủy."}</span>
          {!job.id.startsWith("fallback") && (
            <button type="button" onClick={retryJob}>
              Thử lại
            </button>
          )}
        </div>
      )}

      <section className="workspace">
        <PreviewColumn job={job} activeSegment={activeSegment} seekRef={seekRef} onTimeSegment={followSegment} />

        <section className="editor-column">
          <div className="editor-heading">
            <div>
              <h1>Chỉnh sửa bản dịch</h1>
              <p>Kiểm tra, chỉnh sửa và tối ưu bản dịch cho khớp thời lượng.</p>
            </div>
            <div className="heading-actions">
              <label className="search">
                <MagnifyingGlass />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Tìm trong phụ đề"
                />
              </label>
            </div>
          </div>

          <TranscriptTable
            segments={visibleSegments}
            activeSegmentId={activeSegmentId}
            playingSegmentId={playingSegmentId}
            busyId={busy}
            onSave={saveTranslation}
            onRegenerate={regenerate}
            onSelect={selectSegment}
            onSeek={seekToSegment}
            onPlayAudio={playSegmentAudio}
          />
          <div className="editor-footer">
            <span>Tổng: {job.segments?.length || 0} câu</span>
            <span>Tổng thời lượng: {clockShort(job.duration || 0)}</span>
          </div>
        </section>

        <SettingsColumn
          job={job}
          health={health}
          catalog={catalog}
          busy={busy}
          onUpdateSettings={updateJobSettings}
          onExport={exportVideo}
          onCancel={cancelJob}
        />
      </section>

      <Toast toast={toast} />

      {uploadOpen && <UploadModal busy={busy === "upload"} onUpload={upload} onClose={() => setUploadOpen(false)} />}
    </main>
  );
}
