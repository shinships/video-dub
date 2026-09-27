# Plan: lồng tiếng tự nhiên, hết ngắt quãng giữa câu (27/09/2026)

## Chẩn đoán (đọc code `backend/app/pipeline.py`)

**Gốc lỗi #1 — `_synth_vieneu` băm câu 50 ký tự** (dòng ~666)
- `max_len = 50` → câu Việt 150 ký tự bị cắt 3-4 mảnh, cắt tại MỌI dấu phẩy, còn cắt cứng giữa cụm từ (`rfind(' ')`).
- Mỗi mảnh `infer()` riêng → mỗi mảnh có lặng đầu/đuôi riêng + ngữ điệu reset (lên giọng đầu, hạ giọng cuối như hết câu).
- `np.concatenate` nối thẳng, không cắt lặng, không crossfade → nghe "khựng" giữa câu. **Đây gần như chắc là lỗi chính** [suy luận, cần nghe A/B xác nhận].
- `_trim_silence` chỉ cắt 2 đầu FILE, không đụng lặng giữa các mảnh.

**Gốc lỗi #2 — lặng giữa các segment**
- Mỗi segment đặt đúng mốc gốc; thoại Việt ngắn hơn khung Anh → lỗ lặng. Nếu STT vẫn tách 1 câu thành 2 segment (khe > 1.6s, câu > 24s, không dấu câu) thì lỗ rơi GIỮA câu.

**Gốc lỗi #3 — tempo nhảy theo từng đoạn**
- `segment_tempo` mỗi đoạn một tốc (0.9-1.15) + job speed 1.1 → nhịp đọc lúc nhanh lúc chậm giữa 2 câu liền nhau.

## Plan (ưu tiên theo tác động / công sức)

### P0 — Fix ngắt quãng (1-2 buổi)
1. **Đo trần thật của VieNeu**: test infer 80/120/200/300 ký tự, đo RAM + chất lượng. Nâng `max_len` lên mức an toàn (dự kiến 150-250) [phỏng đoán, phải đo].
2. **Chỉ chia ở ranh giới mạnh**: ưu tiên `. ? ! ; :`, dấu phẩy chỉ dùng khi bắt buộc; không bao giờ cắt cứng giữa cụm.
3. **Ghép mảnh sạch**: trim lặng đầu/đuôi TỪNG mảnh (numpy, ngưỡng -45dB) → chèn khoảng nghỉ có chủ đích (phẩy ~120ms, chấm ~250ms) → crossfade 10-20ms.
4. Test: câu dài 3 mảnh cũ vs mới, đo tổng lặng nội câu (mục tiêu: không khoảng lặng > 300ms trong 1 câu sau dấu phẩy).

### P1 — Nhịp liền mạch (1-2 buổi)
5. **Làm mượt tempo**: tempo đoạn i = trung bình trượt với đoạn i-1/i+1, giới hạn chênh ≤ 5%.
6. **Dời/giãn đoạn**: cho thoại Việt bắt đầu sớm hơn tối đa 0.2s hoặc tràn vào lặng phía sau (đã có SPILL_GUARD) thay vì tăng tốc.
7. **Log cảnh báo** mỗi lần `merge_transcripts` bị buộc tách do chạm trần → biết lỗi còn từ STT hay không.

### P2 — Tự nhiên hơn (tuỳ, sau khi P0-P1 đạt)
8. Prompt dịch: văn nói, câu ngắn gọn, tránh cấu trúc dịch word-by-word; ghi chú chỗ ngắt tự nhiên bằng dấu phẩy.
9. Chuẩn hoá thêm cho TTS: viết tắt (AI, API, SaaS), đơn vị, ký hiệu %/$.
10. Khoảng nghỉ giữa câu trong cùng segment theo độ dài câu.

## Rủi ro
- VieNeu câu dài có thể OOM / đọc lệch cuối câu → phải đo, giữ fallback chia đôi.
- Mảnh dài hơn = TTS lâu hơn mỗi call, tổng thời gian có thể không đổi hoặc tăng nhẹ.
- Crossfade sai tham số có thể nuốt phụ âm đầu mảnh.

## Điều kiện huỷ / đo kết quả
- Test cố định: 2 video đã từng bị lỗi, render trước/sau.
- Đạt: anh nghe không thấy khựng giữa câu ở ≥ 9/10 câu dài mẫu; lặng nội câu tối đa ≤ 300ms.
- Nếu P0 xong mà vẫn khựng → gốc lỗi nằm ở STT/segment (#2), chuyển trọng tâm sang P1.7.
