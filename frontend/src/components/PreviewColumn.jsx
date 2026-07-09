import { useEffect, useRef, useState } from "react";
import { FilmSlate, Lightbulb, Pause, Play, SpeakerHigh } from "@phosphor-icons/react";
import { API } from "../api.js";
import { clockShort } from "../format.js";

export function PreviewColumn({ job, activeSegment, seekRef, onTimeSegment }) {
  const videoRef = useRef(null);
  const trackRef = useRef(null);
  const lastSegmentRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(job.duration || 0);

  // Job demo/fallback không có file nguồn — giữ ảnh minh hoạ.
  const hasVideo = Boolean(job.source_path);
  const src = hasVideo ? `${API}/jobs/${job.id}/source` : null;

  // Cho phép bảng transcript tua video qua ref chung.
  useEffect(() => {
    seekRef.current = (seconds) => {
      const video = videoRef.current;
      if (video) video.currentTime = seconds;
      setTime(seconds);
    };
    return () => {
      seekRef.current = null;
    };
  }, [seekRef]);

  useEffect(() => {
    setTime(0);
    setPlaying(false);
    setDuration(job.duration || 0);
    lastSegmentRef.current = null;
  }, [job.id, job.duration]);

  const handleTime = (event) => {
    const t = event.currentTarget.currentTime;
    setTime(t);
    const segment = (job.segments || []).find((item) => t >= item.start && t < item.end);
    const id = segment?.id || null;
    if (id !== lastSegmentRef.current) {
      lastSegmentRef.current = id;
      if (id) onTimeSegment(id);
    }
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) video.play();
    else video.pause();
  };

  const seekFromTrack = (event) => {
    if (!duration) return;
    const rect = trackRef.current.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const video = videoRef.current;
    if (video) video.currentTime = ratio * duration;
    setTime(ratio * duration);
  };

  return (
    <aside className="preview-column">
      <div className="file-title">
        <div>
          <b>{job.name}</b>
        </div>
        <small>
          <FilmSlate /> MP4 · {job.width || "?"}×{job.height || "?"} · {clockShort(duration)}
        </small>
      </div>
      <div className="video-frame">
        {hasVideo ? (
          <video
            ref={videoRef}
            src={src}
            preload="metadata"
            onTimeUpdate={handleTime}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || job.duration || 0)}
          />
        ) : (
          <img src="/productivity-presenter.png" alt="Video demo chưa có file nguồn" />
        )}
      </div>
      <div className="player-controls">
        <button
          type="button"
          onClick={togglePlay}
          disabled={!hasVideo}
          aria-label={playing ? "Tạm dừng" : "Phát"}
          title={hasVideo ? "" : "Job demo không có file video"}
        >
          {playing ? <Pause weight="fill" /> : <Play weight="fill" />}
        </button>
        <span>
          {clockShort(time)} / {clockShort(duration)}
        </span>
        <SpeakerHigh weight="fill" />
        <div className="player-track" ref={trackRef} onClick={seekFromTrack}>
          <span style={{ width: duration ? `${(time / duration) * 100}%` : "0%" }} />
        </div>
      </div>
      <section className="source-script">
        <div className="section-label">
          <b>Bản gốc (tiếng Anh)</b>
          <span>CC</span>
        </div>
        <p>
          {activeSegment
            ? activeSegment.source_text
            : (job.segments || [])
                .slice(0, 3)
                .map((item) => item.source_text)
                .join(" ")}
        </p>
      </section>
      <section className="tip">
        <b>
          <Lightbulb weight="fill" /> Mẹo
        </b>
        <p>Nhấn Shift + Enter để xuống dòng. Nhấn Enter để lưu. Bấm vào cột thời gian để tua video tới câu đó.</p>
      </section>
    </aside>
  );
}
