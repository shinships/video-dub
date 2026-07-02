# Dùng AI Chat để điều khiển tính năng không có trên UI

UI ([frontend/src/App.jsx](frontend/src/App.jsx)) chỉ hiện những tuỳ chọn phổ biến (giọng,
phong cách, tốc độ, cao độ...). Một số tuỳ chọn tồn tại sẵn ở backend nhưng **cố tình không
làm UI riêng** — vì chỉ vài người dùng cần, hoặc còn đang thử nghiệm. Với các tuỳ chọn này,
thay vì bấm UI, bạn **yêu cầu trực tiếp trong chat** với Claude Code đang mở ở repo này;
Claude sẽ gọi API/DB giúp và báo lại kết quả.

## Cách hoạt động

1. Bạn mô tả ý muốn bằng ngôn ngữ tự nhiên (không cần biết tên field/API).
2. Claude tra [backend/app/main.py](backend/app/main.py) (endpoint `PATCH /api/jobs/{id}`)
   hoặc DB ([backend/app/db.py](backend/app/db.py)) để biết field/giá trị hợp lệ.
3. Claude thực thi (gọi API thật hoặc `UPDATE` trực tiếp qua `app.db.connect()`), rồi
   **verify lại bằng cách đọc lại job** — không chỉ báo "đã xong" suông.
4. Nếu tuỳ chọn không có sẵn, Claude sẽ nói rõ cần thêm code (field DB/Pydantic mới) trước
   khi dùng được.

Yêu cầu: cần có phiên Claude Code đang chạy trong repo này (không phải tính năng của bản
thân app "Lồng Tiếng AI" — không hoạt động nếu chỉ mở UI mà không có Claude).

## Danh sách tuỳ chọn hiện hỗ trợ qua chat

| Muốn gì | Field | Giá trị hợp lệ | Ghi chú |
|---|---|---|---|
| Đổi engine TTS cho **một job cụ thể** mà không đổi cấu hình chung | `tts_engine` trên job (qua `PATCH /api/jobs/{id}`) | `"gemini"` \| `"vieneu"` \| bỏ trống (dùng mặc định toàn cục `VIDEO_DUB_TTS_ENGINE`) | Cho phép nhiều job chạy song song với engine khác nhau — vd job A dùng VieNeu (local, miễn phí), job B dùng Gemini (cloud). Xem [pipeline.py `resolve_tts_engine`](backend/app/pipeline.py). |

Ví dụ yêu cầu trong chat:

> "Cho job vừa upload dùng VieNeu thay vì Gemini."
>
> "Video ABC.mp4 dịch xong rồi, đổi sang giọng cloud cho job đó vì VieNeu đọc sai vài từ tiếng Anh xen kẽ."

## Khi nào nên "thăng cấp" lên UI thật

Nếu một tuỳ chọn qua-chat trở thành thứ bạn yêu cầu thường xuyên, hãy nói Claude làm UI
riêng (dropdown/toggle trong `App.jsx`) thay vì tiếp tục nhờ qua chat mỗi lần — hiệu quả hơn
về lâu dài.
