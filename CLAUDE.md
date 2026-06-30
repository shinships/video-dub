# CLAUDE.md

Hướng dẫn cho **Claude Code** — vai trò **lập trình chính** của dự án này.
Sau khi viết/sửa code, bàn giao cho **Codex review** (xem [AGENTS.md](AGENTS.md)).

## Dự án là gì

"Lồng Tiếng AI" — web app local dịch & lồng tiếng Việt cho video tiếng Anh (< 30 phút).
Luồng: tách audio → tách thoại/nền → STT → dịch (Gemini) → duyệt/sửa → TTS → mix giữ
nền gốc → xuất MP4 + SRT.

## Kiến trúc

- **Backend** — FastAPI (Python 3.11), chạy port **8010**.
  - [backend/app/main.py](backend/app/main.py) — API, SSE (`/events`), hàng đợi job, lifecycle.
  - [backend/app/pipeline.py](backend/app/pipeline.py) — toàn bộ xử lý media: Demucs, STT
    (faster-whisper/Google), dịch theo lô, TTS, FFmpeg render/mix.
  - [backend/app/db.py](backend/app/db.py) — SQLite (`data/video_dub.sqlite3`), schema +
    migration idempotent trong `init_db`/`_migrate`.
  - [backend/app/config.py](backend/app/config.py) — `Settings` đọc từ biến môi trường.
- **Frontend** — React 19 + Vite, port **5173**, một file chính
  [frontend/src/App.jsx](frontend/src/App.jsx). Có `fallbackJob` để UI chạy khi backend tắt.
- **Dữ liệu** — `data/uploads/` (video gốc), `data/jobs/<id>/` (audio/stems/segment mp3/output).

## Lệnh hay dùng (Windows PowerShell)

```powershell
.\setup.ps1     # tạo .venv, cài requirements-core, pnpm install (đủ chạy demo mode)
.\start.ps1     # nạp .env, bật uvicorn:8010 + vite:5173 (cần .venv và .env)
```

Cài thêm cho pipeline thật:
```powershell
.\.venv\Scripts\python -m pip install -r backend\requirements-cloud.txt
.\.venv\Scripts\python -m pip install -r backend\requirements-audio.txt   # demucs + faster-whisper
```

Test:
```powershell
cd backend; ..\.venv\Scripts\python -m pytest -q
```

## Pipeline (đọc kỹ trước khi sửa)

`Pipeline.process` → `_real_process_sync`:
1. `probe` video, ffmpeg tách `source.wav`.
2. `_separate` (Demucs `--two-stems vocals`) → `no_vocals.wav` (nền) + `vocals.wav`.
3. `_transcribe` → dispatch theo `settings.stt_engine`: `_transcribe_whisper` (mặc định,
   local, không cần GCS) hoặc `_transcribe_google` (STT V2 batch qua GCS).
4. `_translate` — **pass ngữ cảnh 1 lần** (`_build_context`: tóm tắt + glossary), rồi
   **dịch theo lô** (`_translate_chunk`, JSON, song song qua `ThreadPoolExecutor`),
   có khống chế độ dài (giây/ký tự) để khớp lồng tiếng.

`Pipeline.export`:
5. TTS song song (`asyncio.gather` + `Semaphore(TTS_WORKERS)`) qua `_synthesize_segment`,
   có **vòng khớp độ dài**: đo TTS thật, nếu dài quá `FIT_TOLERANCE` thì `_rewrite_shorter`
   rồi synth lại (≤ `FIT_MAX_RETRIES`).
6. `_render` (FFmpeg): mỗi đoạn `atempo` (kẹp `ATEMPO_MIN..MAX`) + `adelay`; bus thoại
   chuẩn `-16 LUFS`; **giữ nền gốc bằng ducking động** (`sidechaincompress`); mix cuối qua
   `alimiter`. **KHÔNG** dùng `amix` mặc định cho bước trộn nền+thoại (normalize=1 sẽ chia đôi).

## Quy ước & ràng buộc

- **Chuỗi hiển thị/UI/lỗi bằng tiếng Việt.** Comment giải thích "tại sao", ngắn gọn.
- **Import nặng đặt trong hàm** (`google.*`, `faster_whisper`, `texttospeech`…) để app chạy
  được ở demo mode và test không cần cài cloud/audio deps.
- Tham số mix/dịch/khớp là **hằng số module đầu file** `pipeline.py` (`NARRATION_LUFS`,
  `DUCK_*`, `ATEMPO_*`, `TRANSLATE_*`, `FIT_*`, `TTS_WORKERS`). Sửa hành vi qua hằng số này.
- Thêm cột DB: cập nhật `CREATE TABLE` **và** `_migrate` trong [db.py](backend/app/db.py).
- `effective_demo_mode = True` khi thiếu FFmpeg hoặc cloud config → pipeline chạy giả lập
  (`DEMO_SEGMENTS`), không gọi cloud. Job `demo` được seed lúc khởi động.
- Cấu hình qua biến môi trường (xem [.env.example](.env.example)); không hard-code key/secret.
- Subprocess gọi qua helper `run()` (đã set `CREATE_NO_WINDOW` trên Windows).

## Khi sửa FFmpeg filtergraph

Luôn validate cú pháp bằng input giả lập trước khi coi là xong:
```bash
ffmpeg -hide_banner -v error -f lavfi -i "sine=d=3" ... -filter_complex "<graph>" -map "[mix]" -f null -
```

## Definition of done (trước khi bàn giao Codex)

1. `pytest` xanh; thêm test cho logic thuần mới (xem [backend/tests/test_pipeline.py](backend/tests/test_pipeline.py)).
2. Filtergraph FFmpeg mới đã validate.
3. `python -m py_compile` sạch; không thêm import thừa.
4. Cập nhật [README.md](README.md) / [.env.example](.env.example) nếu đổi env/luồng.
5. **Không tự commit/push trừ khi user yêu cầu.** Nhánh mặc định `master`.
