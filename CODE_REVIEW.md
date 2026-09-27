# Code Review sơ bộ — video-dub (static review, chưa toàn diện, 2026-08-26)

**Lưu ý phạm vi**: đây là review tĩnh, đọc chọn lọc (không đọc 100% mọi dòng của 2 file lớn nhất: `pipeline.py` 1856 dòng đọc ~760 dòng các phần trọng yếu; frontend đọc `App.jsx`, `api.js`, `UploadModal.jsx`, `SegmentRow.jsx` đầy đủ, còn `SettingsColumn/TranscriptTable/PreviewColumn/Topbar/Stepper/Toast` CHƯA đọc nội dung). Không chạy pytest, không cài dependency, không đo coverage thật. Dùng để định hướng, không thay thế code review đầy đủ + chạy test thật.

Phạm vi: đọc tĩnh `backend/app/{config,db,main,service,pipeline}.py` (pipeline.py đọc ~760/1856 dòng — các phần cấu hình, helper TTS/Vbee/VieNeu, `process`/`_real_process_sync`/`_separate`/`_transcribe`/`_detect_speakers`, và toàn bộ `_render`/`_write_srt`), `frontend/src/{App.jsx,api.js}` đầy đủ, danh sách file frontend còn lại (chưa đọc nội dung `SettingsColumn/TranscriptTable/SegmentRow/UploadModal/PreviewColumn/Topbar/Stepper/Toast`), và tên hàm test trong `backend/tests/*` (chưa chạy, chưa đọc nội dung từng test). Không cài đặt, không chạy được (thiếu `.env`, venv, node_modules).

## Bug xác nhận (có bằng chứng dòng)

1. **Thông báo lỗi giới hạn thời lượng sai — xác nhận qua 4 nguồn**:
   - `backend/app/service.py`: `MAX_DURATION_SECONDS = 14400` (giá trị = 4 giờ) nhưng message raise là `"Video vượt giới hạn 30 phút."` (sai — không khớp giá trị check thật).
   - `backend/app/pipeline.py` (`_real_process_sync`): check `metadata["duration"] > 14400`, message đúng `"Video vượt giới hạn 4 giờ."`.
   - `README.md`: quảng cáo "dịch và lồng tiếng Việt cho video tiếng Anh **dưới 30 phút**".
   - `frontend/src/components/UploadModal.jsx`: hiển thị cho người dùng "MP4, MKV hoặc MOV · **tối đa 30 phút**".
   - Kết luận: giới hạn kỹ thuật THẬT là 4 giờ (14400s), nhưng README + UI đều nói 30 phút, và message lỗi ở service.py cũng nói 30 phút dù check là 4 giờ. Đây là bug xác nhận chắc chắn (không phải suy đoán) — không rõ ý định ban đầu là 30 phút (rồi tăng hằng số lên nhưng quên sửa text) hay 4 giờ (rồi quên cập nhật README/UI/1 message). Cần hỏi chủ dự án ý định thật trước khi sửa.

## Rủi ro / code smell (chưa phải bug đã xác nhận khai thác được)

2. **Xây cột SQL động bằng f-string** — `backend/app/db.py`, hàm `update_job`/`update_segment`: `columns = ", ".join(f"{key} = ?" for key in fields)` nối trực tiếp tên cột vào câu SQL. Hiện tại mọi call site truyền key cứng (an toàn), nhưng đây là pattern không có whitelist — nếu sau này có endpoint truyền key từ request body không qua Pydantic model cố định, sẽ mở đường SQL injection qua tên cột.

3. **Đoạn code style khác biệt trong `_synth_vieneu`** (`pipeline.py`, hàm `_synth_vieneu`): logic chunk text bằng regex thủ công (`max_len = 50` hard-code, không có hằng số đặt tên/comment giải thích như phần còn lại của file), retry-on-exception bằng cách chia đôi chunk khi ONNX lỗi bộ nhớ, `import re`/`import numpy as np`/`import gc` cục bộ trong hàm thay vì đầu file. Không phải bug, nhưng khác hẳn phong cách comment-kỹ-lưỡng của toàn bộ file — khả năng là patch thêm sau, thiếu review/refactor để nhất quán. Nên: đặt `VIENEU_MAX_CHUNK_CHARS` thành hằng số ở đầu file, viết docstring giải thích tại sao cần chunk (tránh OOM ONNX) và tại sao retry chia đôi.

4. **Frontend không có test tự động** — `frontend/package.json` không có script `test`, không thấy file `*.test.jsx`/`*.spec.jsx` trong `frontend/src`. Toàn bộ UI (upload, SSE reconnect, timeline editor, segment audio playback) chỉ được kiểm chứng thủ công.

5. **`pipeline.py` dài 1856 dòng, class `Pipeline` gộp nhiều trách nhiệm** (STT, TTS 2 engine, dịch, dò giọng nói, mix audio FFmpeg, export/render, SRT). Về mặt kiến trúc dễ bảo trì ngắn hạn (1 người, 1 project) nhưng khó test đơn vị & khó mở rộng khi thêm engine TTS/STT thứ 3.

6. **`_active_gcloud_credentials`** (pipeline.py) gọi subprocess `gcloud auth print-access-token` — không tự cache trong hàm (có TTL cache `CLIENT_TTL_SECONDS` ở chỗ khác dùng client, nhưng hàm credentials này gọi lại mỗi lần được invoke) — có thể chậm nếu gọi thường xuyên trong 1 job dài. Chưa xác minh có thực sự gọi lặp lại trong 1 job hay chỉ 1 lần/job (chưa đọc hết chỗ gọi hàm này).

## Điểm mạnh (giữ nguyên từ review trước, đã xác nhận thêm qua App.jsx)

- Comment giải thích quyết định kỹ thuật rất chi tiết và nhất quán (trừ mục 3 ở trên).
- `_render`: dùng `filter_complex_script` (file) + subprocess arg-list (không `shell=True`) — an toàn trước injection dù ghép text người dùng (`translated_text`) vào SRT, vì SRT ghi file text thuần, không qua shell.
- `App.jsx`: SSE reconnect có exponential backoff (`Math.min(15000, 1000*2**attempts++)`), cleanup đúng trong `useEffect` return (đóng EventSource, clear timer), lưu `LAST_JOB_KEY` vào localStorage để khôi phục job sau reload, xử lý lỗi qua toast nhất quán. Chất lượng frontend logic tốt dù không có test.
- Demucs fallback 2 lớp (CUDA→CPU→karaoke-trick center-channel-cancel) không im lặng bỏ audio.

## Ma trận hành vi ↔ test (từ tên 52 test trong test_pipeline.py + 6 trong test_api.py)

Có test cho (logic thuần, dễ test — đúng chỗ nên test):
- `_atempo_chain`/`segment_tempo`/`_pitch_chain` (kẹp biên, không méo tiếng).
- `_strip_json`/`_parse_translations`/`translate` retry khi thiếu index, fallback tiếng Anh khi retry lỗi.
- `merge_transcripts`/`split_sentences` (gộp câu, ranh giới câu, viết hoa/viết thường, khoảng lặng) — phần logic tinh vi nhất của app, test khá kỹ (9 test).
- `classify_gender`/`assign_speakers`/`segment_median_f0` (dò giới tính 2 giọng).
- `resolve_segment_voice`/`resolve_tts_engine`/`ensure_engine_voices_ready`/`unknown_vieneu_voices` (chọn giọng, validate config).
- Vbee: `vbee_request_payload`, `vbee_read`, `vbee_sync_payload`, `vbee_should_try_sync`, `vbee_sync_outcome`, `parse_vbee_voices`.
- `_write_srt` scale theo speed.
- API: health, demo job, danh sách voices (2 nguồn: fallback env + Vbee API), patch job reject engine không hợp lệ, 404 khi thiếu file, patch segment chỉ đổi đúng 1 segment.

KHÔNG có test cho (gap thực sự, không phải suy đoán):
- `_render` — toàn bộ logic build FFmpeg filter_complex (ducking, sidechain, mix, batch >30 segments) không có unit test nào chạy qua FFmpeg thật hoặc mock subprocess để kiểm filter string sinh ra đúng.
- `_separate` (Demucs fallback CUDA→CPU→center-channel-cancel) — không test được nhánh fallback nào.
- `process`/`_real_process_sync` end-to-end (toàn bộ orchestration 1 job) — không có test tích hợp.
- `_synth_vieneu` chunking logic (chia câu dài, retry khi ONNX OOM) — 0 test, đây cũng là đoạn code lạc phong cách (mục 3) nên rủi ro cao hơn phần còn lại.
- `regenerate`/`export` (pipeline.py dòng ~1506-1636) — chưa đọc nội dung, chưa có test tên khớp.
- `download_source`/`create_job_from_source` (service.py, dùng bởi CLI `tools/run_video_dub_job.py`) — không validate URL scheme/host trước khi đưa cho yt-dlp. Đã xác minh **không có endpoint web nào gọi tới `download_source`** (grep `main.py` không thấy `download_source`/`is_url`/`create_job_from_source`) — rủi ro SSRF chỉ tồn tại ở đường CLI cục bộ (người vận hành tự gõ lệnh), mức độ nghiêm trọng thấp hơn nếu từng lo là lỗ hổng web.
- SSE (`/api/jobs/{id}/events`) — không có test cho reconnect/nhiều client cùng subscribe/queue leak khi client không đóng kết nối.

## Việc CHƯA làm (giới hạn còn lại của review này)

- Chưa đọc nội dung frontend components: `SettingsColumn`, `TranscriptTable`, `SegmentRow`, `UploadModal`, `PreviewColumn`, `Topbar`, `Stepper`, `Toast` — chỉ xem `App.jsx` (nơi gọi các component này) và `api.js`.
- Chưa đọc nội dung `pipeline.py` phần `regenerate`/`export` (dòng ~1506-1636) và `_with_backoff`/dịch Gemini chi tiết (dòng ~726-800).
- Chưa chạy `pytest`, chưa cài dependency, chưa xác minh 58 test hiện có thực sự PASS trên máy này (không có venv).
- `git status` xác nhận repo sạch trước khi review; **`CODE_REVIEW.md` là file mới do review này tạo ra, chưa commit** — cần hỏi ý trước khi commit vào repo của người dùng.

## Đề xuất ưu tiên

1. Sửa message "30 phút" → "4 giờ" trong `service.py`, hoặc statement rõ ràng lại chính sách giới hạn thật (đồng bộ README/service/pipeline).
2. Thêm whitelist cột hợp lệ trong `db.py` trước khi ghép f-string (phòng thủ chiều sâu).
3. Nếu định mở app ra ngoài localhost: soát lại `download_source`/yt-dlp cho SSRF, thêm auth.
4. Việc tách file/thêm test là cải thiện dài hạn, không khẩn cấp cho use-case local-only hiện tại.
