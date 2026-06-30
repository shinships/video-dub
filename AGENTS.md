# AGENTS.md

Phân vai trong dự án này:

- **Claude Code** — lập trình chính (viết & sửa code). Hướng dẫn đầy đủ ở [CLAUDE.md](CLAUDE.md).
- **Codex** — **review code** do Claude tạo ra. File này định nghĩa cách review.

## Bối cảnh tối thiểu để review

Web app local dịch & lồng tiếng Việt cho video tiếng Anh. Backend FastAPI
([backend/app/](backend/app/)), frontend React/Vite ([frontend/src/App.jsx](frontend/src/App.jsx)),
SQLite. Lõi xử lý media ở [backend/app/pipeline.py](backend/app/pipeline.py): Demucs tách
nền → STT (faster-whisper/Google) → dịch theo lô (Gemini) → TTS → FFmpeg mix giữ nền gốc.
Đọc [CLAUDE.md](CLAUDE.md) để hiểu pipeline chi tiết và quy ước.

## Chạy & kiểm tra trước khi kết luận review

```powershell
cd backend; ..\.venv\Scripts\python -m pytest -q     # phải xanh
..\.venv\Scripts\python -m py_compile app\*.py        # biên dịch sạch
```
FFmpeg filtergraph: validate bằng input `sine` giả lập + `-f null -` (ví dụ trong CLAUDE.md).

## Trọng tâm review (theo thứ tự ưu tiên)

1. **Tính đúng pipeline media**
   - FFmpeg: nhãn filter khớp (`[sN]`, `[narr_mix]`, `[narr_key]`, `[mix]`), thứ tự input
     `-i` khớp index dùng trong `filter_complex`, `-map` đúng stream.
   - **Giữ nền gốc**: cảnh báo nếu quay lại `amix` normalize mặc định (chia đôi âm lượng),
     hoặc bỏ ducking/`alimiter`. Đây là yêu cầu sản phẩm số 1.
   - `atempo` phải nằm trong biên `ATEMPO_MIN..MAX` (không ép tốc độ tới mức méo giọng).
2. **Đồng bộ DB**: cột mới có cả trong `CREATE TABLE` lẫn `_migrate`? Truy vấn khớp schema?
3. **Phụ thuộc & demo mode**: import nặng (`google.*`, `faster_whisper`, `texttospeech`)
   phải nằm trong hàm, không ở top-level — nếu không sẽ vỡ test/demo mode.
4. **Đồng thời (concurrency)**: `ThreadPoolExecutor`/`asyncio.gather` có giữ đúng thứ tự
   kết quả theo index không? Có rò rỉ tài nguyên/giới hạn worker hợp lý không?
5. **Xử lý lỗi & edge case**: video không có thoại, STT/translate trả rỗng, segment thiếu
   `audio_path`, JSON dịch parse lỗi (`_parse_translations` trả `{}` → có fallback không?).
6. **Hợp đồng API/Frontend**: thay đổi field job/segment có khớp giữa
   [main.py](backend/app/main.py) và [App.jsx](frontend/src/App.jsx) không? Ràng buộc
   Pydantic (vd `speed` 0.5–2.0, `pitch` −6..6) hợp lý?
7. **Bảo mật/chi phí**: không hard-code secret; cảnh báo đường gọi cloud tốn tiền chạy ngầm.

## Quy ước cần tôn trọng (đừng đề xuất ngược lại)

- Chuỗi UI/lỗi **tiếng Việt**; tham số hành vi là **hằng số module** đầu `pipeline.py`.
- Comment giải thích "tại sao", không diễn giải code hiển nhiên.
- Không tự commit/push; nhánh mặc định `master`.

## Định dạng góp ý

Mỗi phát hiện: **mức độ** (blocker / nên sửa / tuỳ chọn) + `file:line` + lý do ngắn +
đề xuất sửa cụ thể. Ưu tiên ít phát hiện nhưng chắc, hơn là liệt kê dàn trải.
