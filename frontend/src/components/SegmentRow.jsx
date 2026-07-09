import { memo, useEffect, useRef, useState } from "react";
import { ArrowClockwise, Pause, Play, SpinnerGap } from "@phosphor-icons/react";
import { charBudget, clock } from "../format.js";

export const SegmentRow = memo(function SegmentRow({
  segment,
  active,
  playing,
  busy,
  canPlayAudio,
  onSave,
  onRegenerate,
  onSelect,
  onSeek,
  onPlayAudio,
}) {
  const [draft, setDraft] = useState(segment.translated_text);
  const focused = useRef(false);

  // Đồng bộ lại khi backend đổi text (regenerate/review), trừ lúc user đang gõ.
  useEffect(() => {
    if (!focused.current) setDraft(segment.translated_text);
  }, [segment.translated_text]);

  const budget = charBudget(segment);
  const over = draft.length > budget;
  const fitClass = segment.fit_score >= 85 ? "good" : "warn";

  const commit = () => {
    focused.current = false;
    if (draft !== segment.translated_text) onSave(segment, draft);
  };

  return (
    <article className={`segment-row ${active ? "selected" : ""}`}>
      <span className="position">{segment.position}</span>
      <button
        className="time"
        type="button"
        title="Tua video tới câu này"
        onClick={() => onSeek(segment)}
      >
        {clock(segment.start)}
        <br />– {clock(segment.end)}
      </button>
      <p className="source-copy">
        {segment.speaker && (
          <span className={`speaker-badge ${segment.speaker}`}>
            {segment.speaker === "female" ? "Nữ" : "Nam"}
          </span>
        )}
        {segment.source_text}
      </p>
      <div className="translation-box">
        <textarea
          value={draft}
          onFocus={() => {
            focused.current = true;
            onSelect(segment);
          }}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.blur();
            }
          }}
        />
        <small className={over ? "over" : ""}>
          {draft.length}/{budget}
        </small>
      </div>
      <div className={`fit ${fitClass}`}>
        <b>{segment.fit_score}%</b>
        <span className="fit-bar">
          <i style={{ width: `${segment.fit_score}%` }} />
        </span>
        <small>{fitClass === "good" ? "Rất tốt" : "Ổn"}</small>
      </div>
      <div className="row-actions">
        <button
          type="button"
          aria-label="Nghe đoạn"
          title={canPlayAudio ? "Nghe đoạn lồng tiếng" : "Chưa tạo giọng cho câu này"}
          disabled={!canPlayAudio}
          onClick={() => onPlayAudio(segment)}
        >
          {playing ? <Pause weight="fill" /> : <Play weight="fill" />}
        </button>
        <button
          type="button"
          aria-label="Tạo lại giọng"
          title="Tạo lại giọng cho câu này"
          onClick={() => onRegenerate(segment)}
          disabled={busy}
        >
          {busy || segment.status === "processing" ? <SpinnerGap className="spin" /> : <ArrowClockwise />}
        </button>
      </div>
    </article>
  );
});
