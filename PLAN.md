# Cải thiện phát âm tiếng Anh trong giọng đọc Việt

Model triển khai theo yêu cầu: GPT-6-Sol.

## Mục tiêu

Video mới có lời dẫn tiếng Việt; thuật ngữ và tên riêng đọc gần phát âm tiếng Anh.
Áp dụng chung cho hệ thống, không tạo lại video cũ.

Hiện tại AI, HiggsField, iPhone đều chuyển thành âm vị Anh đúng trên môi trường này;
11 bài kiểm thử chuẩn hoá đã đạt. Chất lượng âm thanh thực tế cần được nghe xác nhận.

## Hướng thực hiện

- Tạo mẫu đọc bằng luồng TTS hiện tại: câu ngắn, câu dài, câu chứa đồng thời ba từ
  trên; dùng giọng mặc định đang cấu hình.
- Duy trì bộ xử lý song ngữ của VieNeu. Chuẩn hoá tên riêng bằng danh sách tường minh;
  chỉ bổ sung ngoại lệ phát âm khi mẫu đọc xác nhận có lỗi.
- Thay quy tắc đoán tên riêng từ chữ in hoa bằng danh sách tên đã biết, tránh đổi
  cách đọc chữ viết tắt ngoài danh sách.
- Chỉ xử lý văn bản đưa vào TTS; bản dịch và phụ đề giữ chính tả gốc. Phân biệt AI
  với đại từ tiếng Việt ai.
- Áp dụng cùng quy tắc khi tạo giọng lần đầu, tạo lại từng đoạn và đọc lại câu đã
  rút ngắn.

## Kiểm chứng và nghiệm thu

Bổ sung kiểm thử HiggsField, iPhone với các kiểu viết hoa/thường, dấu câu và số phiên
bản; kiểm tra không thay nhầm một phần từ. Tách kiểm thử logic thuần khỏi kiểm thử
cần cài VieNeu để demo mode vẫn chạy được.

Chạy toàn bộ pytest, biên dịch Python và xuất video mẫu để kiểm tra có giọng Việt,
tên riêng dễ nghe, không mất từ, lệch nhịp hoặc giảm nền gốc. Review thay đổi và kết
quả kiểm thử; chất lượng phát âm cần nghe xác nhận, không kết luận chỉ từ âm vị.

## Giới hạn mặc định

Giữ VieNeu chạy local, không thêm dịch vụ TTS trả phí. Không thay API, schema DB hay
giao diện; không đổi thông số mix và tốc độ đọc. Không tự commit/push.

## Kết quả triển khai — 05/10/2026

Đã thay heuristic chữ in hoa bằng danh sách tên đã biết, bổ sung Higgsfield, Claude,
Nvidia, Blender, Photoshop. Giữ bộ âm vị Anh gốc cho AI, Higgsfield, iPhone; không
thêm phiên âm Việt vì kiểm thử âm vị chưa xác nhận lỗi cần ngoại lệ mới.

Kiểm chứng: 209 test đạt (`PYTHONPATH=.:backend .venv/bin/python -m pytest
backend/tests -q` từ thư mục gốc), `py_compile` sạch. Mô phỏng chưa cài TTS local:
16 test logic thuần đạt, nhóm 6 test TTS được bỏ qua.

Mẫu dùng giọng Minh Quân, model đã có trên máy (offline), không gọi cloud:
`data/pronunciation-check/baseline.wav`, `improved.wav`,
`sample/dubbed-vi.mp4` và `sample/subtitles-vi.srt`. Video 10,61 giây, audio 48 kHz,
giải mã FFmpeg thành công; giữ nhãn filter, ducking, normalize=0 và limiter.
Đã kiểm tra phụ đề giữ nguyên HiggsField; độ tự nhiên/phát âm cần người dùng nghe
xác nhận, chưa coi việc đúng âm vị là bảo đảm chất lượng nghe.
