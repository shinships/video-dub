# CLAUDE.md

Hướng dẫn cho **Claude Code** — vai trò **lập trình chính** của dự án này.
Sau khi viết/sửa code, bàn giao cho **Codex review** (xem [AGENTS.md](AGENTS.md)).

## Dự án là gì

"Lồng Tiếng AI" — web app local dịch & lồng tiếng Việt cho video tiếng Anh (< 30 phút).
Luồng: tách audio → tách thoại/nền → STT → dịch (Gemini) → duyệt/sửa → TTS → mix giữ
nền gốc → xuất MP4 + SRT.

## Kiến trúc

- **Backend** — FastAPI (Python 3.11), chạy port **8010**, bind **127.0.0.1**.
  - [backend/app/auth.py](backend/app/auth.py) — token cục bộ áp cho **toàn bộ** route qua
    `dependencies=[Depends(require_token)]` ở cấp app (gắn từng route thì thêm endpoint mới là
    quên). Nhận token qua header `X-Video-Dub-Token`, `Authorization: Bearer`, hoặc cookie —
    cookie là bắt buộc vì `<video src>`, link tải và `EventSource` không đặt được header, mà
    nhét token vào query string thì nó chui vào log của uvicorn. Token nằm ở
    `<data_dir>/api-token` (0600), ổn định qua các lần khởi động, `VIDEO_DUB_API_TOKEN` ghi đè.
  - [backend/app/main.py](backend/app/main.py) — API, SSE (`/events`), hàng đợi job, lifecycle.
    **Mọi việc nặng đi qua một hàng đợi duy nhất** (`Task` = process | export | regenerate,
    một worker): export/regenerate từng dùng `BackgroundTasks` nên đi vòng, N export song song
    sinh N×`TTS_WORKERS` tiến trình TTS. `enqueue()` chống trùng, bấm hai lần không render hai
    lần. Lúc khởi động `resume_interrupted()` dọn việc dang dở — hàng đợi nằm trong RAM nên
    không có bước này là job kẹt `processing` vĩnh viễn.
  - [backend/app/pipeline.py](backend/app/pipeline.py) — toàn bộ xử lý media: Demucs, STT
    (faster-whisper/Google), dịch theo lô, TTS, FFmpeg render/mix.
  - [backend/app/db.py](backend/app/db.py) — SQLite (`data/video_dub.sqlite3`), schema +
    migration idempotent trong `init_db`/`_migrate`.
  - [backend/app/config.py](backend/app/config.py) — `SettingsStore` đọc cấu hình theo thứ tự
    **keychain → `settings.json` trong `data_dir` → biến môi trường → mặc định**, có `reload()`.
    `Settings` nay là value object thuần (không tự đọc gì); `settings` là **proxy** trỏ vào ảnh
    chụp hiện tại nên `settings.x` ở ~90 chỗ gọi thấy giá trị mới ngay sau khi lưu, không cần
    khởi động lại. Sửa được từ ngoài **chỉ** qua whitelist `EDITABLE_FIELDS` (field có `label`
    trong `FIELD_SPECS`) — thêm field vào `Settings` KHÔNG tự động cho sửa qua API.
- **Frontend** — React 19 + Vite, port **5173**, một file chính
  [frontend/src/App.jsx](frontend/src/App.jsx). Gọi API **cùng origin** (`/api`): dev server
  proxy sang uvicorn và tự gắn token, nên token không bao giờ nằm trong JS của trình duyệt và
  không còn request cross-origin nào. Chưa có dự án thì hiện `BlankState`, không dựng job giả.
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
   local, không cần GCS, `word_timestamps=True`) hoặc `_transcribe_google` (STT V2 batch qua
   GCS); rồi `split_sentences` **tách mảnh STT tại ranh giới câu theo mốc từng từ** (mảnh
   Whisper ~10s hay đứt giữa câu) và `merge_transcripts` **gộp các mẩu thành câu trọn vẹn**
   (theo dấu kết câu + khe lặng, trần `MERGE_*`) để dịch đủ ngữ cảnh, giọng liền mạch và ít
   call TTS hơn.
4. `_translate` — **pass ngữ cảnh 1 lần** (`_build_context`: tóm tắt + glossary), rồi
   **dịch theo lô** (`_translate_chunk`, JSON, song song qua `ThreadPoolExecutor`),
   có khống chế độ dài (giây/ký tự) để khớp lồng tiếng; cuối cùng `_review_translations`
   **soát lại nhất quán 1 lời gọi** (dọn lệch xưng hô/glossary giữa các lô song song).

   **Đo chi phí**: mọi lời gọi LLM đi qua `_record_usage(meter, stage, response)`; `UsageMeter`
   gom token thật theo bước (`read_response_usage` đọc được cả `usage_metadata` của google-genai
   lẫn `usage` kiểu OpenAI của DeepSeek, **cộng cả `thoughts_token_count`** vì thinking bị tính
   giá output), quy ra tiền theo `MODEL_PRICING_USD_PER_M` rồi `merge_cost` **cộng dồn** vào cột
   `jobs.cost` (job export nhiều lần thì chi phí phải cộng, không ghi đè). Dịch là bước DUY NHẤT
   tốn tiền — STT/tách nền/TTS/render đều chạy local.

   **Tắt thinking**: cả 4 lời gọi LLM đi qua `generate_without_thinking()` — chúng là việc bám
   chỉ dẫn chứ không phải suy luận nhiều bước, mà thinking bị tính giá output (đắt gấp 3 input).
   Helper tự lùi về config mặc định nếu model từ chối `thinking_budget=0`, và `deepseek_reasoning_payload`
   dịch ý định đó sang `reasoning_effort="none"` cho DeepSeek. Đo thật: 6.047 -> 320 token output,
   chất lượng glossary/xưng hô không đổi. **Thêm lời gọi LLM mới thì gọi qua helper này.**

   Nếu job bật `multi_speaker`: trước khi dịch, `_detect_speakers` dò giới tính từng đoạn theo
   cao độ (F0) trên đúng file audio đã sinh transcript (`segment_median_f0` + `assign_speakers`,
   ngưỡng `GENDER_F0_THRESHOLD`), gán `speaker` ('male'/'female') để lưu vào cột `segments.speaker`.

`Pipeline.export`:
5. TTS song song (`asyncio.gather` + `Semaphore(TTS_WORKERS)`) qua `_synthesize_segment`,
   có **vòng khớp độ dài**: đo TTS thật, nếu dài quá `FIT_TOLERANCE` thì `_rewrite_shorter`
   rồi synth lại (≤ `FIT_MAX_RETRIES`). Trước khi đo, `_trim_silence` cắt lặng đầu/đuôi audio
   TTS (đo chính xác, bớt vào câu trễ). Giọng mỗi đoạn do `resolve_segment_voice` chọn theo
   `speaker` khi bật `multi_speaker` (nam/nữ), ngược lại dùng giọng chọn trên UI (`jobs.voice`)
   rồi mới tới giọng mặc định trong env. Danh sách giọng cho UI: `vieneu_preset_voices` (đọc
   `assets/voices_v3_turbo.json` trong package, **không nạp model**); tên giọng VieNeu sai được
   `ensure_engine_voices_ready` chặn ngay đầu `_real_process_sync`/`export` thay vì vỡ ở bước TTS.
   **VieNeu là engine TTS duy nhất** (hằng `TTS_ENGINE`) — Vbee và Gemini TTS đã gỡ, nên bước
   tạo giọng chạy 100% local và không tốn tiền API. Job cũ trong DB còn `tts_engine='vbee'`
   được `resolve_tts_engine` ép về VieNeu thay vì để hỏng.
6. `_render` (FFmpeg): mỗi đoạn `atempo` theo `segment_tempo` (không kéo chậm câu ngắn;
   câu dài tràn sang khoảng lặng trước khi tăng tốc, kẹp `ATEMPO_MAX`) + `adelay`; bus thoại
   chuẩn `-16 LUFS`; **giữ nền gốc bằng ducking động** (`sidechaincompress`); mix cuối qua
   `alimiter`. **KHÔNG** dùng `amix` mặc định cho bước trộn nền+thoại (normalize=1 sẽ chia đôi).

## Quy ước & ràng buộc

- **Chuỗi hiển thị/UI/lỗi bằng tiếng Việt.** Comment giải thích "tại sao", ngắn gọn.
- **Import nặng đặt trong hàm** (`google.*`, `faster_whisper`, `texttospeech`…) để app chạy
  được ở demo mode và test không cần cài cloud/audio deps.
- Tham số mix/dịch/khớp là **hằng số module đầu file** `pipeline.py` (`NARRATION_LUFS`,
  `DUCK_*`, `ATEMPO_*`, `TRANSLATE_*`, `FIT_*`, `TTS_WORKERS`, `GENDER_*`). Sửa hành vi qua hằng số này.
- Thêm cột DB: cập nhật `CREATE TABLE` **và** `_migrate` trong [db.py](backend/app/db.py).
- **Giới hạn thời lượng có đúng một nguồn**: `settings.max_duration_minutes`, kiểm qua
  `check_duration()` trong `pipeline.py`. Trước đây bốn chỗ nói bốn con số khác nhau.
- `effective_demo_mode = True` khi thiếu FFmpeg hoặc cloud config → pipeline chạy giả lập
  (`DEMO_SEGMENTS`), không gọi cloud. Job `demo` được seed lúc khởi động.
- Cấu hình qua `SettingsStore` (xem [.env.example](.env.example)); không hard-code key/secret.
  **API key chỉ đi vào keychain OS**, không bao giờ ghi vào `settings.json` hay `.env` từ code;
  `GET /api/settings` cũng không trả giá trị secret ra ngoài, chỉ trả cờ đã-đặt-hay-chưa.
- Test phải đặt `VIDEO_DUB_DATA_DIR` **và** `VIDEO_DUB_USE_KEYCHAIN=false` trước khi import
  `app.config` (xem [conftest.py](backend/tests/conftest.py)): keychain xếp trước env nên nếu
  không tắt, test sẽ đọc phải key thật trên máy dev.
- Subprocess gọi qua helper `run()` (đã set `CREATE_NO_WINDOW` trên Windows). `run()` dùng
  `Popen` + đăng ký vào `_JobProcesses` theo `CURRENT_JOB` (ContextVar, `asyncio.to_thread`
  copy context nên thread TTS/render vẫn biết job của mình), để **huỷ là giết được thật** —
  trước đây cờ huỷ chỉ đọc ở ranh giới `_stage()`, Demucs/FFmpeg đang chạy vẫn chạy tới hết.
  Tiến trình bị giết làm `run()` ném `asyncio.CancelledError`, không phải `CalledProcessError`.
- **Chạy lại việc dở phải xét file có thật, không tin cột `stage`**: `can_resume_export(job)`
  đòi nền đã tách còn trên đĩa **và** có phân đoạn. Chạy lại pha export là miễn phí (dùng lại
  TTS đã có); chạy lại pha dịch là gọi API và tính tiền lần nữa nên **không bao giờ tự động**.

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
