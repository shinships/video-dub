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
import { BlankState } from "./components/BlankState.jsx";
import { SettingsDialog } from "./components/SettingsDialog.jsx";

const LAST_JOB_KEY = "videodub:lastJob";

export function App() {
  // Không có dự án nào cho tới khi backend trả về job thật. Trước đây state khởi tạo bằng
  // một job bịa sẵn nên người dùng không phân biệt được đâu là dữ liệu của mình.
  const [job, setJob] = useState(null);
  const [health, setHealth] = useState(null);
  const [catalog, setCatalog] = useState(null);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [activeSegmentId, setActiveSegmentId] = useState(null);
  const [playingSegmentId, setPlayingSegmentId] = useState(null);
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [multiSpeaker, setMultiSpeaker] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  const seekRef = useRef(null);
  const audioRef = useRef(null);
  const playingRef = useRef(null);
  const jobIdRef = useRef(null);
  jobIdRef.current = job?.id ?? null;

  const showToast = useCallback((message, variant = "success") => {
    setToast({ message, variant });
  }, []);

  const applySavedSettings = useCallback(
    (next) => {
      setHealth((current) => ({ ...(current || {}), missing: next.missing, ready: next.ready }));
      api("/voices").then(setCatalog).catch(() => {});
      showToast("Đã lưu cài đặt.");
    },
    [showToast],
  );

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
      localStorage.setItem(LAST_JOB_KEY, data.id);
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
    api("/health")
      .then(setHealth)
      .catch(() => setHealth({ ok: false, offline: true, missing: [], gpu: {} }));
    api("/voices").then(setCatalog).catch(() => {});
    const last = localStorage.getItem(LAST_JOB_KEY);
    // Mở lại dự án gần nhất; đã bị xoá (hoặc chưa có) thì lùi về dự án mới nhất trong thư
    // viện, hết thì để trống — không dựng dữ liệu giả để lấp chỗ.
    const openLatest = () =>
      api("/jobs")
        .then((list) => (list?.length ? api(`/jobs/${list[0].id}`).then(applyJob) : null))
        .catch(() => {});
    if (last) api(`/jobs/${last}`).then(applyJob).catch(openLatest);
    else openLatest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // SSE: cập nhật tại chỗ từ payload event, chỉ GET lại job ở các mốc lớn.
  useEffect(() => {
    if (!job?.id) return undefined;
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
  }, [job?.id]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), 2600);
    return () => clearTimeout(timer);
  }, [toast]);

  const visibleSegments = useMemo(() => {
    const needle = deferredQuery.trim().toLowerCase();
    if (!needle) return job?.segments || [];
    return (job?.segments || []).filter(
      (segment) =>
        segment.source_text.toLowerCase().includes(needle) ||
        segment.translated_text.toLowerCase().includes(needle),
    );
  }, [job?.segments, deferredQuery]);

  const activeSegment = useMemo(
    () => (job?.segments || []).find((segment) => segment.id === activeSegmentId) || null,
    [job?.segments, activeSegmentId],
  );

  const saveTranslation = useCallback(
    async (segment, text) => {
      setJob((current) => ({
        ...current,
        segments: current.segments.map((item) =>
          item.id === segment.id ? { ...item, translated_text: text } : item,
        ),
      }));
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
        await api(`/jobs/${jobIdRef.current}/segments/${segment.id}/regenerate`, { method: "POST" });
        // SSE event "segment" sẽ cập nhật trạng thái và refresh khi xong.
        showToast("Đang tạo lại đoạn giọng…");
      } catch (error) {
        showToast(error.message, "error");
        // Trả đoạn về "ready" để nút bấm lại được; không có SSE nào tới sửa hộ.
        setJob((current) => ({
          ...current,
          segments: current.segments.map((item) =>
            item.id === segment.id ? { ...item, status: "ready" } : item,
          ),
        }));
      } finally {
        setBusy("");
      }
    },
    [showToast],
  );

  const updateJobSettings = useCallback(
    async (values) => {
      setJob((current) => ({ ...current, ...values }));
      if (!jobIdRef.current) return;
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
      data.append("voice", job?.voice || "");
      data.append("style", job?.style || "");
      data.append("multi_speaker", multiSpeaker ? "true" : "false");
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
    [job?.voice, job?.style, multiSpeaker, applyJob, showToast],
  );

  const exportVideo = useCallback(async () => {
    setBusy("export");
    try {
      const result = await api(`/jobs/${jobIdRef.current}/export`, { method: "POST" });
      // Export nay đi qua hàng đợi chung: bấm lần hai không xếp thêm việc, nên đừng báo
      // như vừa nhận việc mới.
      if (result?.status === "already_queued") {
        showToast("Video này đã nằm trong hàng đợi render.");
      } else {
        showToast(health?.demo_mode ? "Demo export — đây là kết quả giả lập" : "Đang tạo giọng và render video…");
      }
      // Demo-mode export không bắn SSE — refresh trễ để lấy trạng thái completed.
      const jobId = jobIdRef.current;
      setTimeout(() => {
        api(`/jobs/${jobId}`)
          .then((data) => setJob((current) => (current.id === jobId ? data : current)))
          .catch(() => {});
      }, 1500);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusy("");
    }
  }, [health?.demo_mode, showToast]);

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

  // Chưa có dự án (hoặc chưa đủ cấu hình) -> không dựng workspace rỗng, cũng không dựng dữ
  // liệu giả: nói thẳng đang thiếu gì.
  if (!job) {
    return (
      <main className="app-shell">
        <Topbar
          currentJob={job}
          health={health}
          onSelectJob={loadJob}
          onUpload={() => setUploadOpen(true)}
          onOpenSettings={() => setSettingsOpen(true)}
        />
        <BlankState
          health={health}
          onUpload={() => setUploadOpen(true)}
          onOpenSettings={() => setSettingsOpen(true)}
        />
        <Toast toast={toast} />
        {uploadOpen && (
          <UploadModal
            busy={busy === "upload"}
            limitLabel={health?.duration_limit}
            multiSpeaker={multiSpeaker}
            onToggleMultiSpeaker={setMultiSpeaker}
            onUpload={upload}
            onClose={() => setUploadOpen(false)}
          />
        )}
        {settingsOpen && (
          <SettingsDialog onClose={() => setSettingsOpen(false)} onSaved={applySavedSettings} />
        )}
      </main>
    );
  }

  return (
    <main className="app-shell">
      <Topbar
        currentJob={job}
        health={health}
        onSelectJob={loadJob}
        onUpload={() => setUploadOpen(true)}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <Stepper job={job} statusMessage={statusMessage} />

      {(job.status === "failed" || job.status === "cancelled") && (
        <div className="error-banner" role="alert">
          <span>{job.status === "failed" ? `Lỗi: ${job.error || "Pipeline thất bại."}` : "Job đã bị hủy."}</span>
          <button type="button" onClick={retryJob}>
            Thử lại
          </button>
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

      {uploadOpen && (
        <UploadModal
          busy={busy === "upload"}
          limitLabel={health?.duration_limit}
          multiSpeaker={multiSpeaker}
          onToggleMultiSpeaker={setMultiSpeaker}
          onUpload={upload}
          onClose={() => setUploadOpen(false)}
        />
      )}
      {settingsOpen && (
        <SettingsDialog onClose={() => setSettingsOpen(false)} onSaved={applySavedSettings} />
      )}
    </main>
  );
}
