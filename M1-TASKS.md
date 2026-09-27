# Milestone 1 — Nền tảng sản phẩm (bảng theo dõi)

Kế hoạch đầy đủ (so sánh local vs SaaS, chi phí API, gói premium, rủi ro):
`~/.claude/plans/c-code-d-n-bubbly-pond.md`.

Mục tiêu M1: một bản build mà **người lạ** cài lên máy họ, tự cấu hình qua UI, lồng tiếng
xong một video thật, và đo được chính xác video đó tốn bao nhiêu tiền API.

Quyết định đã chốt (không mở lại trong M1):
- Bán credit API dịch (Gemini/DeepSeek) — chi phí biến đổi duy nhất.
- TTS chỉ VieNeu (local, miễn phí). **Vbee đã gỡ hẳn.**
- Hỗ trợ cả macOS và Windows ngay từ đầu.
- Local app trước, SaaS sau (chênh chi phí biến đổi ~5:1 nghiêng về local).

Trạng thái: **8/9 xong** — chỉ còn H và phần cuối của D. Chưa commit gì (theo CLAUDE.md).
Cập nhật 12/09: dọn 8,68GB dữ liệu thật, tắt thinking ở cả 4 call site LLM, xong C, G, I, F.

| | Hạng mục | Trạng thái |
|---|---|---|
| A | Gỡ Vbee | ✅ xong |
| B | Demo mode minh bạch | ✅ xong |
| C | Cài đặt UI + keychain | ✅ xong |
| D | Vòng đời dữ liệu | 🟡 một phần (đã dọn 8,68GB) |
| E | Đo chi phí thật | ✅ xong |
| F | Khoá API cục bộ | ✅ xong |
| G | Hàng đợi bền + huỷ thật | ✅ xong |
| H | Cross-platform | ⬜ |
| I | Thống nhất giới hạn thời lượng | ✅ xong |

---

## ✅ A — Gỡ Vbee

`pipeline.py` 2.218 → ~1.960 dòng. Xoá `_synth_vbee*`, `fetch_vbee_voices`,
`parse_vbee_voices`, nhóm hằng `VBEE_*`, các field `vbee_*` trong `config.py`,
`_vbee_voice_options` trong `main.py`, dropdown engine ở `SettingsColumn.jsx`.

`TTS_ENGINE = "vieneu"` là hằng duy nhất; `resolve_tts_engine` **ép job cũ có
`tts_engine='vbee'` về VieNeu** thay vì để hỏng. Tài liệu đã cập nhật (`.env.example`,
`README.md`, `AI-CHAT.md`, `CLAUDE.md`). Chuỗi "Vbee" còn lại trong code chỉ là comment
giải thích lịch sử — cố ý giữ.

## ✅ B — Demo mode minh bạch

Lỗi thương mại nguy hiểm nhất đã chặn: trước đây gõ sai API key ⇒ **âm thầm** nhận 7 câu
phụ đề bịa sẵn, không báo lỗi.

- `effective_demo_mode` giờ **chỉ** bật khi `VIDEO_DUB_DEMO_MODE=true` tường minh.
- `missing_requirements` trả danh sách `{key, message}` tiếng Việt; `cloud_ready` suy ra từ
  chính nó để hai chỗ không trôi lệch.
- `/api/health` trả `missing` + `ready`; `POST /api/jobs` trả **422 nói rõ thiếu gì**.
- `seed_demo_job()` chỉ chạy ở demo mode — không ghi job giả vào DB của khách hàng nữa.
- Frontend: gỡ hẳn `fallbackJob`, thêm `BlankState.jsx` (4 trạng thái: đang kết nối /
  mất backend / thiếu cấu hình / chưa có dự án).

⚠️ Hệ quả đã xử lý: test trước đây **xanh giả** vì chạy trên DB production còn sót job
`demo`. `backend/tests/conftest.py` nay đặt `VIDEO_DUB_DATA_DIR` = tempdir **trước mọi
import `app.*`** (bắt buộc, vì `Settings` đọc env lúc import). Đừng phá thứ tự này.

## ✅ C — Cài đặt UI + keychain (12/09/2026)

**Blocker kiến trúc đã gỡ.** `Settings` từng là `@dataclass(frozen=True)` với default
`os.getenv(...)` — mà default của dataclass được evaluate **lúc định nghĩa class**, tức lúc
import. Nay `Settings` là value object thuần (không tự đọc gì) và `SettingsStore` lo việc đọc:

**keychain → `settings.json` trong `data_dir` → biến môi trường → mặc định**

Giá trị bấm lưu trong app thắng `.env` — ngược lại thì người dùng sửa trong Cài đặt sẽ "không
ăn" mà chẳng hiểu vì sao. Env vẫn là nguồn hợp lệ cho CLI/CI/test.

- `settings` là **proxy** trỏ vào ảnh chụp hiện tại → ~90 chỗ gọi `settings.x` không phải sửa
  dòng nào, mà vẫn thấy giá trị mới ngay sau `reload()`.
- `data_dir` **cố tình** chỉ đọc được từ env: `settings.json` nằm bên trong chính nó.
- Secret chỉ đi vào **keychain OS**; `settings.json` bị lọc cả khi ghi lẫn khi đọc, nên key
  dán tay vào file đó cũng không được dùng. Không có keychain → **báo lỗi**, không âm thầm
  hạ cấp xuống ghi file thường.
- `GET /api/settings` **không trả giá trị secret**, chỉ trả cờ đã-đặt-hay-chưa + nguồn của
  từng field (`env`/`keychain`/`file`/`default`) để UI giải thích ô mình không tự điền.
- `PUT /api/settings` chỉ nhận whitelist `EDITABLE_FIELDS` (21 field) và validate **trước
  khi ghi bất cứ thứ gì** — một field sai thì không lưu nửa vời. Ghi `settings.json` nguyên tử
  (tmp + replace). Thêm field vào `Settings` **không** tự động cho sửa qua API.
- `SettingsDialog.jsx` mới (bánh răng ở thanh trên cùng, và nút "Mở Cài đặt" trong màn hình
  thiếu cấu hình). Chỉ gửi field thực sự đổi — gửi cả form sẽ âm thầm "đóng băng" giá trị đang
  đến từ `.env` thành giá trị lưu trong file.

**Đã gỡ `_prefer_adc_over_stray_credentials()`.** Nó `os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS")`
ngay lúc import — xoá vĩnh viễn khỏi cả tiến trình, phá luôn mọi thư viện Google khác. Thay
bằng context manager `google_auth_scope()` chỉ che biến đó trong lúc **dựng** client Google
rồi trả lại nguyên trạng. Hai hành vi từng cùng đọc `VIDEO_DUB_USE_GCLOUD_AUTH` với hai phép
thử khác nhau nay tách thành `google_prefer_adc` và `google_gcloud_token` (mặc định giữ y như cũ).

Cũng sửa luôn: khoá cache client Vertex nay gồm project/region — trước đây là hằng
`"genai_vertex"` nên đổi project trong Cài đặt vẫn dùng client cũ.

### Kiểm chứng

Chạy server thật trên cổng riêng với thư mục dữ liệu tạm (không đụng dữ liệu của bạn):

| Kiểm tra | Kết quả |
|---|---|
| `GET /api/settings` có lộ API key không | không — trả `""` + `secrets_set` |
| `PUT` field ngoài whitelist (`data_dir`, `deepseek_base_url`, `cost`) | 422, cả ba |
| `PUT` sai enum (`stt_engine: "khong-ton-tai"`) | 422, liệt kê giá trị hợp lệ |
| `PUT` secret khi máy không có keychain | 422 kèm hướng dẫn, không ghi file |
| `"WHISPER"` gửi lên chữ hoa | chuẩn hoá thành `"whisper"` trước khi lưu |
| **Đổi cài đặt có hiệu lực không cần khởi động lại** | `multi_speaker` false→true trên **cùng PID** |
| Round-trip keychain macOS thật | ghi/đọc/xoá đều đúng (dùng service name riêng, đã xoá) |

21 test mới (16 cho store, 5 cho endpoint). Tổng **125 test xanh**.

### Còn thiếu so với mô tả trong plan

Hai dòng trong danh sách dialog của plan **chưa làm**, có chủ đích:

- **Thư mục xuất** — hiện không có field nào đọc nó (bản xuất luôn nằm trong `data/jobs/<id>/`).
- **Giới hạn thời lượng** — đây chính là mục I, nơi 4 chỗ đang nói 4 kiểu. Thêm field thứ 5
  vào lúc này làm mọi thứ tệ hơn.

Thêm ô cấu hình mà không có gì đọc nó là nói dối người dùng, nên tôi để lại cho đúng bước.

## 🟡 D — Vòng đời dữ liệu

Đã xong:
- `DELETE /api/jobs/{id}` (`remove_job`) → `delete_job_files` rồi `delete_job`; segments tự
  cascade nhờ `PRAGMA foreign_keys = ON`.
- `cleanup_job_intermediates(job_id)` chạy sau khi render xong: xoá `narration-batch-*.wav`,
  `filter-batch-*.txt`, `filter-complex.txt`, `source.wav`, `demucs/*/*/vocals.wav`.
  **Cố ý GIỮ** `no_vocals.wav` và `segment-*.wav` để còn export lại được.

Còn lại:
- [ ] Tự dọn job cũ hơn N ngày (cần có Cài đặt ở C trước).
- [ ] UI thư viện dự án: hiện dung lượng từng job + nút xoá.

**Nguyên nhân gốc của 13GB rác**: `narration-batch-*.wav` dùng offset `adelay` **tuyệt đối**
nên mỗi file trải dài từ giây 0 ⇒ dung lượng tăng O(n²). Đo thực tế: narration-batch 4,98GB
· demucs 3,55GB · source.wav 1,92GB, trong khi output thật chỉ 1,71GB.

✅ **Đã dọn (12/09/2026)**: chạy `cleanup_job_intermediates` trên cả 44 job, giải phóng
**8,68GB** — `data/` 13G → 5,1G, đĩa trống 31Gi → 39Gi. Kết quả còn nguyên: 14 file xuất,
40 `no_vocals.wav`, 1.099 `segment-*.wav`; 0 file `narration-batch`/`source`/`vocals` sót lại.

Hai job kẹt `processing` (4/9 và 7/9) hoá ra là **job ma** — đúng triệu chứng của mục G
(hàng đợi trong RAM, crash là kẹt vĩnh viễn). Script dọn có rào an toàn: chỉ bỏ qua job
`processing`/`queued` mà file vừa được chạm trong 2 giờ.

## ✅ E — Đo chi phí thật

`jobs.cost` giờ chứa số đo thật (trước đây chỉ nhánh demo mới ghi):

```json
{ "model": "deepseek-v4-pro",
  "stages": { "context": {…}, "translate": {…}, "review": {…}, "rewrite": {…} },
  "total": { "input_tokens": 1390, "cached_input_tokens": 256,
             "output_tokens": 1843, "calls": 3 },
  "usd": 0.009144, "vnd": 238, "free_local": ["stt","tts","separate","render"] }
```

- `read_response_usage` đọc **cả hai dạng**: `usage_metadata` (google-genai) và `usage` kiểu
  OpenAI (DeepSeek).
- **Cộng `thoughts_token_count` vào output** — thinking token nằm ngoài
  `candidates_token_count` nhưng vẫn bị tính **giá output**. Bỏ sót là tính thiếu tiền.
- `merge_cost` **cộng dồn**, không ghi đè: job export nhiều lần thì chi phí phải cộng.
- `UsageMeter` khoá bằng `threading.Lock` vì các lô dịch chạy song song trong
  `ThreadPoolExecutor`.
- UI: panel chi phí ghi rõ Whisper/Demucs/VieNeu "Miễn phí · local", chỉ dòng Dịch có tiền.

**Lỗi tính tiền phát hiện nhờ việc đo**: `_MultiKeyDeepSeekClient` chỉ đọc `temperature` và
**đánh rơi `thinking_config`** — pipeline *đã* yêu cầu tắt thinking cho bước dịch nhưng yêu
cầu đó không tới API. Đã thêm `deepseek_reasoning_payload()` chuyển `thinking_budget=0` →
`reasoning_effort="none"`.

A/B trên API thật, cùng một đầu vào 2 câu:

| | trước | sau |
|---|---|---|
| translate (output token) | 5.662 | **79** |
| review (output token) | 327 | **1** |
| tổng | 804đ | **238đ** |

**Rẻ đi 3,4 lần**, chất lượng dịch không đổi. (`reasoning_effort="minimal"` cho 922 token —
**tệ hơn cả mặc định**, đừng dùng.)

Chiếu lên video 30 phút: **~2.540đ (DeepSeek peak) / ~1.989đ (Gemini)** ≈ **66–85đ/phút
video** — khớp ước tính trong plan, giờ là số đo.

### ✅ Đã tắt thinking ở cả 4 call site (12/09/2026)

`_build_context` và `_rewrite_shorter` trước đây không tắt thinking và chiếm gần hết phần chi
phí còn lại. Nay cả 4 lời gọi đi qua **`generate_without_thinking()`** — một helper chung, vì
nhánh lùi (model không hỗ trợ `thinking_budget=0` → INVALID_ARGUMENT) trước đó đã bị chép lại
ở từng call site và thêm hai bản nữa là sớm muộn trôi lệch.

Đo A/B trên API DeepSeek thật, cùng đầu vào:

| | output token | tiền |
|---|---|---|
| `_build_context` trước → sau | 2.304 → **273** | 243,8đ → **36,2đ** |
| `_rewrite_shorter` trước → sau | 3.743 → **47** | 393,1đ → **14,1đ** |
| **tổng 2 lời gọi** | 6.047 → **320** | 636,9đ → **50,3đ** |

**Rẻ đi ~12,7 lần.** Chất lượng không đổi: bản `_build_context` mới cho đúng cặp xưng hô
`tôi – bạn` và cùng bộ glossary; bản có thinking chỉ thêm hai mục vô nghĩa
(`Singapore => Singapore`, `Changi => Changi`). Câu `_rewrite_shorter` rút gọn vẫn tự nhiên
và giữ đủ ý.

Lỗi quota/mạng vẫn nổi lên cho `_with_backoff` xử lý — helper chỉ nuốt đúng lỗi
"không hỗ trợ thinking".

### Ba đòn bẩy giảm chi phí chưa làm (xếp theo hiệu quả)

1. **Bỏ nhân ba văn bản nguồn** — `pipeline.py` gửi `prev_context_src` và `next_context_src`
   cho **từng** câu, mỗi câu xuất hiện 3 lần trong payload. Các câu cùng lô đã nằm cạnh nhau
   trong mảng JSON rồi; chỉ cần prev của câu đầu lô và next của câu cuối lô. **−40% input.**
2. **Prompt caching** — prefix (~1.800 token) lặp nguyên vẹn ở cả 8 lô nhưng đang trả giá
   đầy đủ 8 lần. Prompt đã đặt đúng thứ tự cache-friendly, chỉ cần bật. **−15% input nữa.**
3. **`_review_translations` tự tắt ở video dài** — `REVIEW_MAX_CHARS=24000` nghĩa là video
   30' thường **vượt trần và bị bỏ qua**, tức mất bước đảm bảo nhất quán ở đúng những video
   dài cần nó nhất. Sửa thành cửa sổ trượt: tốn thêm tiền nhưng là khác biệt chất lượng
   đáng bán.

## ✅ F — Khoá API cục bộ (12/09/2026)

App giữ API key và credit, nên cổng 8010 mở cho mọi tiến trình trên máy là đủ để một script
bất kỳ chạy dưới cùng tài khoản đọc hoặc sửa cấu hình. CORS không chặn được `curl` — nó chỉ
ràng buộc trình duyệt.

- Token áp cho **toàn bộ** route qua `dependencies=[Depends(require_token)]` ở cấp app. Gắn
  theo từng route thì thêm endpoint mới là quên, mà quên kiểu này không ai phát hiện ra.
- Nhận token ba đường: header `X-Video-Dub-Token`, `Authorization: Bearer`, hoặc **cookie**.
  Cookie là bắt buộc chứ không phải tiện nghi: `<video src>`, `<audio src>`, link tải và
  `EventSource` **không đặt được header**, mà nhét token vào query string thì nó chui thẳng
  vào log truy cập của uvicorn. Xác thực bằng header thì server phát luôn cookie
  (`HttpOnly`, `SameSite=strict`) cho những chỗ đó dùng.
- So sánh bằng `secrets.compare_digest` — so chuỗi thường thoát sớm ở byte đầu khác nhau, đủ
  để dò dần từng ký tự.
- Token nằm ở `<data_dir>/api-token`, tạo với `0o600` **ngay từ `os.open`** (ghi xong mới siết
  quyền là còn một cửa sổ đọc trộm). Ổn định qua các lần khởi động, `VIDEO_DUB_API_TOKEN` ghi đè
  để bản đóng gói Tauri truyền sẵn cho sidecar.
- Frontend gọi **cùng origin** (`/api`): dev server proxy sang uvicorn và tự gắn token. Token
  không bao giờ nằm trong JS của trình duyệt, và không còn request cross-origin nào.
  `VIDEO_DUB_API_TARGET` đổi được đích proxy.

**Lỗi bắt được khi chạy thật**: token chỉ được sinh ở request đầu tiên, nên dòng log lúc khởi
động chỉ tới một file chưa tồn tại và dev proxy không có gì để đọc. Nay sinh ngay trong
`lifespan`, có test riêng.

### Kiểm chứng (server thật)

| | |
|---|---|
| gọi thẳng backend không token | 401 (cả `GET /api/health` lẫn `PUT /api/settings`) |
| token sai, và token đúng nhưng cắt 1 ký tự | 401 |
| header / bearer / cookie, token đúng | 200 cả ba |
| cookie phát ra | `HttpOnly; Path=/; SameSite=strict` |
| qua dev proxy, trình duyệt không hề có token | 200 |
| token có lọt vào bundle JS không | không |
| quyền file token | `-rw-------` |

12 test, là file DUY NHẤT gỡ ghi đè token trong conftest (các test khác chạy với xác thực đã
bỏ qua để tập trung vào thứ chúng kiểm).

## ✅ G — Hàng đợi bền và huỷ thật (12/09/2026)

Ba lỗi, đều thuộc loại "im lặng":

**1. Job kẹt `processing` vĩnh viễn.** Hàng đợi nằm trong RAM, khởi động không hề nạp lại việc
dang dở. Đo trên dữ liệu thật: 2/44 job kẹt từ 4/9 và 7/9 — không chạy, không hỏng, không xoá
được, không có đường quay lại. Nay `resume_interrupted()` chạy trước khi nhận request đầu tiên:

| Tình trạng | Xử lý |
|---|---|
| `queued` | xếp lại (chưa tiêu gì) |
| `processing` ở pha export **và** còn đủ file | chạy tiếp — miễn phí, dùng lại TTS đã có |
| `processing` ở pha dịch | **chỉ đánh dấu hỏng** kèm lý do; nút Thử lại đã có sẵn |

Không tự chạy lại pha dịch là có chủ đích: người dùng mới chỉ mở app lên, chưa bấm gì, mà chạy
lại pha đó là gọi API và **tính tiền họ lần nữa**.

**2. Export đi vòng qua hàng đợi.** `export` và `regenerate` dùng `BackgroundTasks`, nên N lần
export song song sinh N×`TTS_WORKERS` tiến trình TTS cùng lúc. Nay cả ba loại việc
(`process`/`export`/`regenerate`) là `Task` trong cùng một hàng đợi, một worker. `enqueue()`
chống trùng — kiểm chứng bằng 5 request song song: 2 nhận, 3 trả `already_queued`.

**3. Huỷ không huỷ thật.** Cờ huỷ chỉ được đọc ở ranh giới `_stage()`, nên Demucs/Whisper/FFmpeg
đang chạy vẫn chạy tới hết. Nay `run()` dùng `Popen` và đăng ký tiến trình vào `_JobProcesses`
theo `CURRENT_JOB` (ContextVar — `asyncio.to_thread` copy context nên thread TTS/render tự biết
job của mình, không phải thêm tham số vào cả chục chỗ gọi). `POST /cancel` giết thẳng và trả về
số tiến trình đã giết. Tiến trình bị ta giết làm `run()` ném `CancelledError` chứ không phải
`CalledProcessError` — nếu không, nguyên nhân thật (người dùng bấm huỷ) bị che sau một thông
báo "FFmpeg thất bại".

### Một lỗi tìm ra nhờ đi soi dữ liệu thật

Bản đầu quyết định "chạy lại được hay không" bằng cột `stage`. Nhưng trong dữ liệu của bạn có
một job mang `stage="export"`, `progress=88` mà **thư mục rỗng và 0 phân đoạn** — chạy lại là
vỡ ở `job["artifacts"]["background"]` với `KeyError: 'background'`, một thông báo vô nghĩa mà
lại trông y hệt lỗi thật. Nay `can_resume_export(job)` đòi nền đã tách **còn trên đĩa** và có
phân đoạn; thiếu thì báo bằng tiếng Việt tử tế thay vì KeyError. `retry` cũng dùng chung hàm này.

### Kiểm chứng

Dựng đúng ba tình huống trong DB tạm rồi khởi động server thật:

| Job | Trước | Sau khi khởi động |
|---|---|---|
| có nền + phân đoạn, stage=export | `processing` mãi | chạy tiếp, tiến tới stage `voice` |
| stage=export nhưng thư mục rỗng | `processing` mãi | `failed` + lý do rõ ràng |
| stage=translate | `processing` mãi | `failed` + cảnh báo chạy lại sẽ tính phí |

Thêm test giết tiến trình thật (`sleep 30` bị kết liễu trong <10s và báo "đã huỷ"), test
`run()` giữ nguyên hợp đồng cũ (`CalledProcessError` kèm `stderr`, giết tiến trình khi quá giờ),
và test cờ huỷ được xoá sau mỗi lần chạy — quên xoá thì lần bấm Thử lại sau chết ngay ở lệnh
subprocess đầu tiên.

16 test mới. Tổng **141 test xanh**.

### Ảnh hưởng tới dữ liệu hiện có của bạn

Hai job ma thật sẽ được xử lý ngay lần khởi động backend tới:
- `2632531f…` (stage=voice, 23/60 câu đã có audio, nền còn nguyên) → **tự chạy tiếp**, đọc nốt
  37 câu còn lại rồi render. TTS chạy local nên gần như không tốn tiền.
- `2949820c…` (stage=export, thư mục rỗng) → đánh dấu hỏng kèm lý do, chờ bạn quyết.

## ⬜ H — Cross-platform (macOS + Windows)

- [ ] [tools/file_lock.py:12](tools/file_lock.py) — `import fcntl` ở module scope ⇒ **không
      import nổi trên Windows**, kéo `watch_folder.py` và `telegram_dub_bot.py` chết theo.
      Thêm nhánh `msvcrt`.
- [ ] [main.py:350](backend/app/main.py) `detect_gpu` — chỉ biết `nvidia-smi`. Thêm Apple
      MPS (`torch.backends.mps.is_available()`); **máy dev của bạn đang luôn hiện
      "Không phát hiện"**.
- [ ] [tools/watch_folder.py](tools/watch_folder.py) — hardcode `/Users/mktmda/Movies/AutoDub`
      và đường dẫn tuyệt đối sang project `video-compress` khác. Đưa vào cấu hình.
- [ ] `pipeline.py` — `sys.executable -m demucs` sẽ hỏng khi đóng gói (frozen app không phải
      interpreter). Gọi Demucs qua API Python trực tiếp.
- [ ] Thêm `setup.sh` / `start.sh` cho macOS — **hiện không tồn tại**, dù macOS mới là nền
      tảng đang chạy thật. `setup.ps1`/`start.ps1` từ tháng 8 đã lỗi thời.

## ✅ I — Thống nhất giới hạn thời lượng (12/09/2026)

Bốn chỗ nói bốn con số. Tệ nhất: `service.py` cho phép 14400s (4 giờ) nhưng câu báo lỗi ngay
dưới nó ghi **"vượt giới hạn 30 phút"** — người tải video 45 phút lên sẽ được nhận, rồi đọc
một thông báo nói giới hạn là 30 phút.

Nay một nguồn duy nhất: `settings.max_duration_minutes` (mặc định 240), kiểm qua
`check_duration()` trong `pipeline.py`. `service.MAX_DURATION_SECONDS` đã xoá; `/api/health`
trả `duration_limit` để UI hiện đúng con số thật thay vì ghi cứng; ô sửa nằm trong Cài đặt —
đây cũng là dòng còn thiếu của bước C, nay đã có thứ thật để đọc.

Thông báo lỗi giờ nói cả hai vế: *"Video dài 45 phút, vượt giới hạn 4 giờ."*

Kèm theo: `_settings_payload` phân biệt field kiểu số (`type: "number"`) — trước đó ô int bị
coi là text, form gửi `"240"` lên và bị `_validate` từ chối, trông như form hỏng dù người dùng
gõ đúng.

---

## Việc chặn phát hành, ngoài A–I

- [ ] **Repo chưa có LICENSE.** Phải thêm trước khi phát hành. (Torch BSD-3, Demucs MIT,
      faster-whisper MIT, VieNeu-TTS-v3-Turbo Apache-2.0 — đều thoả, **cho phép dùng thương
      mại audio sinh từ giọng preset**. Rủi ro bản quyền TTS coi như đã gỡ.)
- [ ] **Lớp automation macOS chưa vào git**: `tools/watch_folder.py` (667 dòng, 21 test),
      `tools/telegram_dub_bot.py` (918 dòng), toàn bộ script shell, `CODE_REVIEW.md` đều
      **untracked**. Đây là tài sản của gói Studio đang không được version-control.
- [ ] `vieneu` phá pin dependency (kéo gradio, nâng fastapi 0.115→0.139). Bản đóng gói phải
      khoá phiên bản hoặc tách VieNeu ra tiến trình riêng.
- [ ] Đo tốc độ VieNeu trên CPU máy yếu trước khi hứa thời gian xử lý (~300 câu TTS trên CPU
      là bước chậm nhất).
- [ ] `yt-dlp` trong `service.download_source` không kiểm scheme/host ⇒ SSRF + rủi ro ToS/DMCA
      nếu đưa lên UI. Giữ ở CLI hoặc bỏ khỏi bản thương mại.

## Chưa kiểm chứng được

- Kiểm tra UI bằng trình duyệt **thất bại**: extension Chrome trả "Frame with ID 0 is showing
  error page" với cả `127.0.0.1` lẫn `localhost`, ở nhiều cổng. Nghi extension thiếu quyền
  localhost. Các thay đổi frontend (B) mới chỉ xác nhận qua production build xanh.

## Definition of done (mỗi bước, theo CLAUDE.md)

1. `cd backend && ../.venv/bin/python -m pytest -q` xanh (hiện **156 test**).
2. Filtergraph FFmpeg mới đã validate bằng input `sine=d=3`.
3. `python -m py_compile` sạch, không import thừa.
4. Cập nhật `README.md` / `.env.example` nếu đổi env hoặc luồng.
5. **Không tự commit/push trừ khi được yêu cầu.** Nhánh mặc định `master`.
