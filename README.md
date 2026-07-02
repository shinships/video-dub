# Lồng Tiếng AI

Web app local để dịch và lồng tiếng Việt cho video tiếng Anh dưới 30 phút.

## Có gì trong MVP

- Upload MP4/MKV/MOV, kiểm tra thời lượng/codec bằng FFprobe.
- Queue xử lý tuần tự, SSE cập nhật tiến trình, cancel/retry.
- Demucs tách thoại khỏi nhạc; ưu tiên CUDA, tự fallback CPU. Tuỳ chọn `htdemucs_ft`.
- STT: faster-whisper chạy local (mặc định, không cần GCS) hoặc Google STT V2 batch.
- Gemini 2.5 Flash dịch theo lô có ngữ cảnh + glossary, khống chế độ dài để khớp giọng.
- Timeline editor, lưu và regenerate riêng từng đoạn; tốc độ/cao độ chỉnh được.
- TTS giọng Việt: Gemini-TTS (cloud, mặc định Aoede) hoặc VieNeu-TTS chạy local
  (miễn phí, hỗ trợ nhân bản giọng); vòng viết-lại để TTS đọc vừa khung giờ.
- FFmpeg: ducking động giữ nguyên nhạc nền gốc, chuẩn loudness bus thoại −16 LUFS,
  TTS tạo song song, xuất MP4 + SRT.
- SQLite lưu job/segment. Có demo mode để chạy ngay khi chưa cấu hình Cloud.

## Chạy nhanh trên Windows

Yêu cầu: Python 3.11+, pnpm/Node.js.

```powershell
.\setup.ps1
.\start.ps1
```

Mở [http://127.0.0.1:5173](http://127.0.0.1:5173). API docs ở
[http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs).

## Bật pipeline Google Cloud thật

1. Cài [FFmpeg](https://ffmpeg.org/download.html) và xác nhận `ffmpeg`,
   `ffprobe` chạy được trong PowerShell.
2. Cài dependency:

```powershell
.\.venv\Scripts\python -m pip install -r backend\requirements-cloud.txt
.\.venv\Scripts\python -m pip install -r backend\requirements-audio.txt
```

3. Bật API: Vertex AI, Text-to-Speech (và Speech-to-Text + Cloud Storage nếu dùng
   `VIDEO_DUB_STT_ENGINE=google`).
4. Đăng nhập Application Default Credentials:

```powershell
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

5. Copy `.env.example` thành `.env`, điền project (và bucket nếu dùng Google STT).
   Nạp biến môi trường trước khi chạy hoặc dùng công cụ quản lý `.env` của bạn.

Mặc định STT chạy local bằng faster-whisper nên **không cần Cloud Storage**; chỉ cần
Vertex AI (dịch) + Text-to-Speech. Nếu đặt `VIDEO_DUB_STT_ENGINE=google` thì STT V2
batch mới cần GCS — hãy tạo Budget + alert trong Billing trước khi chạy video dài.

## TTS local bằng VieNeu (không cần Vertex AI cho bước tạo giọng)

```powershell
.\.venv\Scripts\python -m pip install -r backend\requirements-tts-local.txt
```

Rồi đặt trong `.env`:

```
VIDEO_DUB_TTS_ENGINE=vieneu
# Tuỳ chọn: giọng preset (vd Ngọc Lan, Xuân Vĩnh) hoặc wav 3-5s để nhân bản giọng.
VIDEO_DUB_VIENEU_VOICE=
VIDEO_DUB_VIENEU_REF_AUDIO=
```

[VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS) chạy hoàn toàn local (CPU dùng
ONNX Runtime, không cần torch; có GPU thì `pip install "vieneu[gpu]"`), miễn phí và
không dính quota. Bước dịch vẫn dùng Gemini qua Vertex AI như cũ. Khi engine là
`vieneu`, lựa chọn giọng Aoede/Kore… trên UI không có tác dụng — chọn giọng qua
`VIDEO_DUB_VIENEU_VOICE` hoặc nhân bản qua `VIDEO_DUB_VIENEU_REF_AUDIO`.

Muốn đổi engine cho **một job cụ thể** (không đổi cấu hình chung) mà không có nút trên
UI? Xem [AI-CHAT.md](AI-CHAT.md) — yêu cầu trực tiếp trong chat với Claude Code.

## Cấu trúc

- `frontend/`: React + Vite, giao diện Guided Flow.
- `backend/app/main.py`: API, SSE, queue và lifecycle.
- `backend/app/pipeline.py`: Demucs, Google Cloud, TTS và FFmpeg.
- `backend/tests/`: kiểm thử API cốt lõi.

## Lưu ý GPU

Máy hiện phát hiện NVIDIA Quadro P1000 4GB. Demucs có thể thiếu VRAM với model
lớn; pipeline sẽ thử CUDA trước rồi tự chạy CPU nếu thất bại.
