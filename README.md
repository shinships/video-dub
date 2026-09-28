# Lồng Tiếng AI

Web app local để dịch và lồng tiếng Việt cho video tiếng Anh (mặc định tối đa 4 giờ,
đổi được trong Cài đặt).

## Có gì trong MVP

- Upload MP4/MKV/MOV, kiểm tra thời lượng/codec bằng FFprobe.
- Queue xử lý tuần tự, SSE cập nhật tiến trình, cancel/retry.
- Demucs tách thoại khỏi nhạc; ưu tiên CUDA, tự fallback CPU. Tuỳ chọn `htdemucs_ft`.
- STT: faster-whisper chạy local (mặc định, không cần GCS) hoặc Google STT V2 batch.
- Gemini/DeepSeek dịch theo lô có ngữ cảnh + glossary, khống chế độ dài để khớp giọng.
- Timeline editor, lưu và regenerate riêng từng đoạn; tốc độ/cao độ chỉnh được.
- TTS giọng Việt: VieNeu-TTS chạy **100% local** (miễn phí, hỗ trợ nhân bản giọng);
  vòng viết-lại để TTS đọc vừa khung giờ.
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

## Cài đặt trong app (không cần sửa .env)

Bấm biểu tượng bánh răng trên thanh trên cùng để mở **Cài đặt**: API key dịch, engine dịch,
giọng VieNeu (kể cả file wav 3-5 giây để nhân bản giọng), engine STT, model Whisper/Demucs,
thiết bị chạy TTS.

- Thay đổi **có hiệu lực ngay**, không phải khởi động lại backend.
- **API key được lưu vào keychain của hệ điều hành** (Keychain trên macOS, Credential Manager
  trên Windows), không ghi ra `.env`, và không bao giờ được API trả ngược ra ngoài.
- Thứ tự ưu tiên: keychain → `settings.json` trong thư mục dữ liệu → `.env` → mặc định. Giá
  trị bấm lưu trong app thắng `.env`; ô nào đang lấy từ `.env` thì Cài đặt có ghi chú ngay cạnh.
- Máy không có keychain thì việc lưu key báo lỗi rõ ràng chứ **không** âm thầm ghi ra file thường.

## Khoá API cục bộ

Backend chỉ bind `127.0.0.1` và **mọi route đều cần token**, vì app giữ API key và credit của
bạn — để cổng 8010 mở cho mọi tiến trình trên máy là đủ để một script bất kỳ đọc hoặc sửa cấu
hình. (CORS không chặn được `curl`; nó chỉ ràng buộc trình duyệt.)

- Token tự sinh lần chạy đầu, lưu ở `data/api-token` với quyền `0600`, giữ nguyên qua các lần
  khởi động. Đặt `VIDEO_DUB_API_TOKEN` để tự chỉ định.
- `pnpm dev` **không cần cấu hình gì**: dev server proxy `/api` sang uvicorn và tự gắn token,
  nên token không bao giờ có mặt trong JS của trình duyệt.
- Gọi bằng tay: `curl -H "X-Video-Dub-Token: $(cat data/api-token)" http://127.0.0.1:8010/api/health`

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
không dính quota. Đây là engine TTS **mặc định**. Bước dịch vẫn dùng Gemini qua Vertex AI.

Bản v3turbo (mode mặc định của SDK, `vieneu>=3.6.0`) có sẵn **23 giọng preset** đủ vùng miền (Bắc / Trung / Nam) và phong cách (tin tức, kể chuyện, tự nhiên...), đọc thẳng từ `vieneu/assets/voices_v3_turbo.json` nên UI liệt kê được mà không phải nạp model:

| Giọng | Đặc trưng | Giọng | Đặc trưng |
|---|---|---|---|
| Adam | Nam · Nam · Tự nhiên (mặc định) | Trúc Ly | Nữ · Bắc · Tự nhiên |
| Minh Đức | Nam · Bắc · Tin tức | Mai Anh | Nữ · Bắc · Tin tức |
| Phạm Tuyên | Nam · Bắc · Tự nhiên | Ngọc Linh | Nữ · Bắc · Kể chuyện |
| Thái Sơn | Nam · Nam · Kể chuyện | Đoan Trang | Nữ · Bắc · Tự nhiên |
| Xuân Vĩnh | Nam · Bắc · Tự nhiên | Thục Đoan | Nữ · Nam · Kể chuyện |
| Thanh Bình | Nam · Bắc · Kể chuyện | Thùy Dung | Nữ · Nam · Tin tức |
| Minh Triết | Nam · Nam · Tin tức | Ngọc Trân | Nữ · Trung · Tự nhiên |
| Quang Sơn | Nam · Trung · Tự nhiên | Mỹ Duyên | Nữ · Nam · Đọc truyện |
| Đức Trí | Nam · Nam · Đọc truyện | Quỳnh Anh | Nữ · Bắc · Đọc truyện |
| Mạnh Dũng | Nam · Bắc · Tự nhiên | Kim Thanh | Nữ · Nam · Đọc truyện |
| Minh Quân | Nam · Bắc · Tự nhiên | Ngọc Huyền | Nữ · Bắc · Tự nhiên |
| Anh Khôi | Nam · Bắc · Kể chuyện | | |

Chọn giọng trên dropdown "Giọng nói" (lưu vào `jobs.voice`, thắng cấu hình env cho job đó),
hoặc đặt mặc định qua `VIDEO_DUB_VIENEU_VOICE`, hoặc nhân bản giọng riêng qua
`VIDEO_DUB_VIENEU_REF_AUDIO` (wav 3-5s, thắng preset trong env). Đặt sai tên giọng thì
backend báo lỗi ngay khi **bắt đầu xử lý** job và log cảnh báo lúc khởi động — không để tới
bước tạo giọng mới vỡ sau khi đã tốn STT + tiền dịch.

Đây là engine TTS duy nhất: bước tạo giọng chạy hoàn toàn trên máy bạn, không gọi cloud và
không tốn tiền API. Chỉ bước **dịch** mới gọi Gemini/DeepSeek.

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

## Stable output naming

YouTube jobs keep the original YouTube title as the display/output stem. The download filename may use the video ID internally, but it must never become the user-facing title. `backend/app/service.py` sanitizes the title and falls back to `video_<id>` only when YouTube provides no title. Always pass the URL directly to the pipeline so metadata is preserved; do not pre-download and invoke the pipeline with an ID-only filename.
