import { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { SegmentRow } from "./SegmentRow.jsx";

export function TranscriptTable({
  segments,
  activeSegmentId,
  playingSegmentId,
  busyId,
  onSave,
  onRegenerate,
  onSelect,
  onSeek,
  onPlayAudio,
}) {
  const parentRef = useRef(null);
  const virtualizer = useVirtualizer({
    count: segments.length,
    getScrollElement: () => parentRef.current,
    // min-height 88px + 1px border-top của .segment-row
    estimateSize: () => 89,
    overscan: 6,
  });

  // Cuộn tới câu đang active (theo playback hoặc chọn); align auto = không nhảy nếu đã thấy.
  const activeIndex = segments.findIndex((segment) => segment.id === activeSegmentId);
  useEffect(() => {
    if (activeIndex >= 0) virtualizer.scrollToIndex(activeIndex, { align: "auto" });
  }, [activeIndex, virtualizer]);

  return (
    <div className="transcript-table">
      <div className="table-head">
        <span>#</span>
        <span>Thời gian</span>
        <span>Tiếng Anh (gốc)</span>
        <span>Tiếng Việt (dịch)</span>
        <span>Độ khớp thời lượng</span>
        <span>Tác vụ</span>
      </div>
      <div className="table-body" ref={parentRef}>
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map((item) => {
            const segment = segments[item.index];
            return (
              <div
                key={segment.id}
                data-index={item.index}
                ref={virtualizer.measureElement}
                className="virtual-row"
                style={{ transform: `translateY(${item.start}px)` }}
              >
                <SegmentRow
                  segment={segment}
                  active={segment.id === activeSegmentId}
                  playing={segment.id === playingSegmentId}
                  busy={segment.id === busyId}
                  canPlayAudio={Boolean(segment.audio_path)}
                  onSave={onSave}
                  onRegenerate={onRegenerate}
                  onSelect={onSelect}
                  onSeek={onSeek}
                  onPlayAudio={onPlayAudio}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
