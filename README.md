# Lồng Tiếng AI

Web app local để dịch và lồng tiếng Việt cho video tiếng Anh dưới 30 phút.

## Có gì trong MVP

- Upload MP4/MKV/MOV, kiểm tra thời lượng/codec bằng FFprobe.
- Queue xử lý tuần tự, SSE cập nhật tiến trình, cancel/retry.
- Demucs tách thoại khỏi nhạc; ưu tiên CUDA, tự fallback CPU. Tuỳ chọn `htdemucs_ft`.
- STT: faster-whisper chạy local (mặc định, không cần GCS) hoặc Google STT V2 batch.
- Gemini 2.5 Flash dịch theo lô có ngữ cảnh + glossary, khống chế độ dài để khớp giọng.
- Timeline editor, lưu và regenerate riêng từng đoạn; tốc độ/cao độ chỉnh được.
- TTS giọng Việt: VieNeu-TTS chạy local (mặc định, miễn phí, hỗ trợ nhân bản giọng)
  hoặc Vbee (cloud VN, giọng tự nhiên); vòng viết-lại để TTS đọc vừa khung giờ.
- Lồng tiếng 2 giọng (tùy chọn): tự dò nam/nữ theo cao độ rồi gán giọng riêng cho mỗi vai.
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
không dính quota. Đây là engine TTS **mặc định**. Bước dịch vẫn dùng Gemini qua Vertex
AI. Chọn giọng qua `VIDEO_DUB_VIENEU_VOICE` hoặc nhân bản qua `VIDEO_DUB_VIENEU_REF_AUDIO`
(UI chỉ hiển thị giọng đang cấu hình, không đổi trực tiếp).

Muốn đổi engine cho **một job cụ thể** (không đổi cấu hình chung) mà không có nút trên
UI? Xem [AI-CHAT.md](AI-CHAT.md) — yêu cầu trực tiếp trong chat với Claude Code.

## TTS bằng Vbee (cloud VN, giọng tiếng Việt tự nhiên)

Không cần cài thêm gì (dùng `httpx` sẵn có). Lấy **App ID** và **token** trong studio
Vbee rồi đặt trong `.env`:

```
VIDEO_DUB_TTS_ENGINE=vbee
VIDEO_DUB_VBEE_APP_ID=<app-id>
VIDEO_DUB_VBEE_TOKEN=<token>
# voiceCode; xem danh sách bằng GET https://vbee.vn/api/public/v1/voices?languageCode=vi-VN
VIDEO_DUB_VBEE_VOICE=hn_female_ngochuyen_full_48k-fhg
```

Bước tạo giọng gọi Vbee qua HTTP **bất đồng bộ** (gói tài khoản phổ biến không mở chế độ
sync): POST tạo yêu cầu → poll tới `COMPLETED` → tải MP3 về. Bước dịch vẫn dùng Gemini qua
Vertex AI. Khi engine là `vbee`, chọn giọng qua `VIDEO_DUB_VBEE_VOICE`.

## Lồng tiếng 2 giọng (nam/nữ)

Video có cả nam lẫn nữ (phỏng vấn, đối thoại) đọc chung một giọng nghe sai vai. Bật chế độ
2 giọng để tự **dò giới tính người nói theo từng đoạn** (phân tích cao độ F0 trên `vocals.wav`
đã tách — chạy local, không cần model/token mới) rồi **gán giọng nam/nữ tương ứng** khi tạo
TTS. Video một người vẫn ra một giọng như thường.

Bật theo một trong ba cách: công tắc "Lồng tiếng 2 giọng" lúc upload trên web, cờ CLI
`--multi-speaker`, hoặc `PATCH /api/jobs/{id} {"multi_speaker": true}`. Đặt mặc định toàn cục
qua `VIDEO_DUB_MULTI_SPEAKER=true`.

Cấu hình giọng cho mỗi giới tính (giọng **nam** để trống thì kế thừa giọng 1-giọng hiện có,
nên thường chỉ cần khai thêm giọng **nữ**):

```
# Vbee: chỉ cần thêm 1 voiceCode nữ bên cạnh giọng nam đang dùng.
VIDEO_DUB_VBEE_VOICE_FEMALE=hn_female_ngochuyen_full_48k-fhg
# VieNeu: preset hoặc file wav 3-5s để nhân bản, cho từng giới tính.
VIDEO_DUB_VIENEU_REF_AUDIO_MALE=<male.wav>
VIDEO_DUB_VIENEU_REF_AUDIO_FEMALE=<female.wav>
# Ngưỡng Hz phân nam/nữ (mặc định 165; hạ nếu giọng nữ trầm bị nhận nhầm là nam).
VIDEO_DUB_GENDER_F0_THRESHOLD=165
```

Nhãn nam/nữ mỗi đoạn hiện ở màn duyệt; nếu dò sai có thể sửa bản dịch/tạo lại như bình thường.

## Cấu trúc

- `frontend/`: React + Vite, giao diện Guided Flow.
- `backend/app/main.py`: API, SSE, queue và lifecycle.
- `backend/app/pipeline.py`: Demucs, Google Cloud, TTS và FFmpeg.
- `backend/tests/`: kiểm thử API cốt lõi.

## Lưu ý GPU

Máy hiện phát hiện NVIDIA Quadro P1000 4GB. Demucs có thể thiếu VRAM với model
lớn; pipeline sẽ thử CUDA trước rồi tự chạy CPU nếu thất bại.
