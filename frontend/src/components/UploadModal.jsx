import { SpinnerGap, UploadSimple } from "@phosphor-icons/react";

export function UploadModal({ busy, onUpload, onClose }) {
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section
        className="upload-modal"
        onMouseDown={(event) => event.stopPropagation()}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          onUpload(event.dataTransfer.files?.[0]);
        }}
      >
        <span className="upload-icon">
          <UploadSimple />
        </span>
        <h2>Tải video tiếng Anh</h2>
        <p>
          MP4, MKV hoặc MOV · tối đa 30 phút
          <br />
          Có thể kéo-thả video vào đây
        </p>
        <label className="primary-action file-picker" htmlFor="video-file-input">
          {busy ? <SpinnerGap className="spin" /> : <UploadSimple />} Chọn video
        </label>
        <input
          id="video-file-input"
          className="visually-hidden"
          type="file"
          accept="video/mp4,video/x-matroska,video/quicktime,.mp4,.mkv,.mov"
          disabled={busy}
          onChange={(event) => onUpload(event.target.files?.[0])}
        />
        <button className="text-button" type="button" onClick={onClose}>
          Đóng
        </button>
      </section>
    </div>
  );
}
