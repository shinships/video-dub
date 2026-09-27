"""Test cho hàng đợi bền và huỷ thật (bước G).

Hai lỗi được nhắm tới đều là lỗi "im lặng": job kẹt `processing` vĩnh viễn sau khi backend
tắt, và bấm Huỷ nhưng Demucs/FFmpeg vẫn chạy tới hết.
"""

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import app.main as main_module
from app.db import get_job, init_db, update_job
from app.main import RESUMABLE_STAGES, Task, enqueue, resume_interrupted
from app.db import connect, now_iso
from app.pipeline import CURRENT_JOB, cancel_job_processes, job_context, run


@pytest.fixture(autouse=True)
def _database():
    """DB của test nằm trong thư mục tạm mới toanh mỗi lần chạy nên phải tự tạo bảng."""
    init_db()


def _exportable(tmp_path, job_id: str, status: str, stage: str) -> str:
    """Job có ĐỦ thứ để export lại: file nền đã tách + ít nhất một phân đoạn."""
    _job(job_id, status, stage)
    background = tmp_path / f"{job_id}-no_vocals.wav"
    background.write_bytes(b"RIFF....WAVE")
    update_job(job_id, artifacts={"background": str(background)})
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO segments (id, job_id, position, start, end, source_text,"
            " translated_text, fit_score, updated_at) VALUES (?, ?, 1, 0, 1, 'a', 'b', 90, ?)",
            (f"{job_id}-seg", job_id, now_iso()),
        )
    return job_id


def _job(job_id: str, status: str, stage: str) -> str:
    """Chèn thẳng một row job. Không dùng `seed_demo_job` vì nó đặt id phân đoạn cố định
    (`demo-1`...) nên chỉ dựng được đúng một job."""
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs (id, name, status, stage, progress, artifacts, cost,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, 0, '{}', '{}', ?, ?)",
            (job_id, f"{job_id}.mp4", status, stage, now_iso(), now_iso()),
        )
    return job_id


def test_interrupted_export_job_is_resumed_not_re_translated(tmp_path):
    """Job chết ở pha export phải chạy tiếp bằng `export` — dùng lại TTS đã có, không tốn
    thêm đồng nào. Chạy lại `process` là dịch lại từ đầu và tính tiền lần nữa."""
    job_id = _exportable(tmp_path, "g-export", "processing", "export")

    resumed, interrupted = resume_interrupted()

    assert Task("export", job_id) in resumed
    assert job_id not in interrupted


def test_interrupted_translate_job_is_flagged_instead_of_silently_rebilled():
    """Pha dịch chạy lại là gọi API và tính tiền. Không được tự làm điều đó lúc khởi động —
    người dùng chỉ mở app lên, họ chưa bấm gì cả."""
    job_id = _job("g-translate", "processing", "translate")

    resumed, interrupted = resume_interrupted()

    assert job_id in interrupted
    assert Task("process", job_id) not in resumed

    after = get_job(job_id, include_segments=False)
    assert after["status"] == "failed"  # không còn kẹt "processing"
    assert "Thử lại" in after["error"] and "tính phí" in after["error"]


def test_queued_job_is_requeued():
    """Job mới xếp hàng thì chưa tiêu gì — cứ chạy lại, không cần hỏi."""
    job_id = _job("g-queued", "queued", "probe")

    resumed, _ = resume_interrupted()

    assert Task("process", job_id) in resumed


def test_resume_leaves_finished_jobs_alone():
    done = _job("g-done", "completed", "export")
    review = _job("g-review", "review", "translate")

    resumed, interrupted = resume_interrupted()

    ids = {task.job_id for task in resumed} | set(interrupted)
    assert done not in ids and review not in ids
    assert get_job(done, include_segments=False)["status"] == "completed"


def test_export_stage_set_matches_what_export_can_resume():
    """`export` bỏ qua câu đã có audio, nên hai stage này chạy lại là miễn phí. Nếu thêm
    stage mới vào pha export mà quên cập nhật ở đây, job sẽ bị dịch lại oan."""
    assert RESUMABLE_STAGES == {"voice", "export"}


def test_same_task_is_not_queued_twice():
    """Bấm Export hai lần không được thành hai lần render: mỗi lần render sinh
    TTS_WORKERS tiến trình, nhân đôi là đủ làm nghẹt máy người dùng."""

    async def scenario():
        main_module.pending_tasks.clear()
        while not main_module.work_queue.empty():
            main_module.work_queue.get_nowait()

        assert await enqueue(Task("export", "g-dup")) is True
        assert await enqueue(Task("export", "g-dup")) is False
        assert main_module.work_queue.qsize() == 1
        # Việc KHÁC trên cùng job vẫn vào được hàng bình thường.
        assert await enqueue(Task("regenerate", "g-dup", "seg-1")) is True

    asyncio.run(scenario())


def test_cancel_kills_a_running_subprocess():
    """Trước đây huỷ chỉ được đọc ở ranh giới `_stage()`, nên tiến trình đang chạy vẫn chạy
    tới hết. Đây là test cho hành vi giết thật, có tiến trình thật."""
    job_id = "g-kill"
    started = threading.Event()
    result: dict[str, object] = {}

    def worker():
        with job_context(job_id):
            started.set()
            try:
                run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=60)
                result["outcome"] = "hoàn thành"
            except asyncio.CancelledError:
                result["outcome"] = "đã huỷ"
            except BaseException as exc:  # noqa: BLE001
                result["outcome"] = f"lỗi khác: {type(exc).__name__}"

    thread = threading.Thread(target=worker)
    began = time.monotonic()
    thread.start()
    started.wait(timeout=5)
    time.sleep(0.4)  # để Popen kịp khởi động thật

    assert cancel_job_processes(job_id) == 1
    thread.join(timeout=10)

    assert not thread.is_alive()
    # Bị ta giết phải hiện ra là "đã huỷ", không phải "FFmpeg thất bại" — nếu không thì
    # nguyên nhân thật (người dùng bấm huỷ) bị che mất sau một thông báo lỗi kỹ thuật.
    assert result["outcome"] == "đã huỷ"
    assert time.monotonic() - began < 10  # chứ không phải chờ hết 30 giây


def test_cancel_flag_is_cleared_so_retry_works():
    """Quên xoá cờ huỷ thì lần bấm Thử lại sau sẽ chết ngay ở lệnh subprocess đầu tiên."""
    job_id = "g-retry"
    with job_context(job_id):
        cancel_job_processes(job_id)

    with job_context(job_id):
        done = run([sys.executable, "-c", "print('ok')"], timeout=30)
    assert done.stdout.strip() == "ok"


def test_run_still_reports_failures_the_old_way():
    """`run()` được viết lại để giết được tiến trình — hợp đồng cũ phải giữ nguyên vì chỗ bắt
    lỗi ở tầng trên đọc `exc.stderr` để dựng thông báo."""
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run([sys.executable, "-c", "import sys; sys.stderr.write('hỏng rồi'); sys.exit(3)"], timeout=30)

    assert caught.value.returncode == 3
    assert "hỏng rồi" in caught.value.stderr


def test_run_kills_the_process_on_timeout():
    """Quá giờ mà để tiến trình sống tiếp là để lại tiến trình mồ côi ăn CPU mãi."""
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", "import time; time.sleep(20)"], timeout=1)


def test_run_without_job_context_still_works():
    """`probe()` được gọi cả từ lúc upload (chưa có job nào) — không được đòi có ngữ cảnh."""
    assert CURRENT_JOB.get() is None
    assert run([sys.executable, "-c", "print('không thuộc job nào')"], timeout=30).returncode == 0


def test_export_stage_without_files_is_not_resumed():
    """Gặp thật trong dữ liệu: job mang stage="export" nhưng thư mục rỗng và 0 phân đoạn.
    Tin vào cột `stage` rồi xếp chạy lại thì vỡ ở `artifacts["background"]` với
    `KeyError: 'background'` — người dùng nhận một thông báo vô nghĩa."""
    job_id = _job("g-export-rong", "processing", "export")

    resumed, interrupted = resume_interrupted()

    assert Task("export", job_id) not in resumed
    assert job_id in interrupted


def test_can_resume_export_needs_both_background_and_segments(tmp_path):
    from app.db import get_job as fetch
    from app.pipeline import can_resume_export

    assert can_resume_export(fetch(_job("g-tron", "failed", "export"))) is False

    job_id = _exportable(tmp_path, "g-du", "failed", "export")
    assert can_resume_export(fetch(job_id)) is True

    # Nền bị xoá khỏi đĩa (dọn tay, chép máy khác) -> không còn export lại được.
    Path((fetch(job_id)["artifacts"])["background"]).unlink()
    assert can_resume_export(fetch(job_id)) is False
