import sys
import os
from pathlib import Path
import json
import time

sys.path.insert(0, '/Users/mktmda/Projects/vibe-coding/video-dub')
sys.path.insert(0, '/Users/mktmda/Projects/vibe-coding/video-dub/backend')

from app import pipeline
from app.db import get_job, update_job, update_segment
from app.config import settings

def run():
    job_id = "3310e0a7-f9c9-4d64-991c-734cc4594ddd"
    job = get_job(job_id, include_segments=False)
    if not job:
        print("Job not found!")
        return

    transcripts_file = Path(f"/Users/mktmda/Projects/vibe-coding/video-dub/data/jobs/{job_id}/transcripts.json")
    if not transcripts_file.exists():
        print("Transcripts file not ready yet!")
        return

    transcripts = json.loads(transcripts_file.read_text(encoding="utf-8"))
    print(f"Loaded {len(transcripts)} transcripts.")

    p = pipeline.Pipeline(hook=lambda *a: None)
    
    # Check if already translated
    existing_job = get_job(job_id, include_segments=True)
    segments = existing_job.get("segments") or []
    
    if not segments:
        print("Step 1: Translating with Gemini / Claude fallback...")
        update_job(job_id, status="processing", stage="translate", progress=45)
        translated, context = p._translate(transcripts, job.get("style", "Tự nhiên"))
        
        artifacts = (get_job(job_id, include_segments=False) or {}).get("artifacts") or {}
        if context:
            artifacts["translate_context"] = context
            update_job(job_id, artifacts=artifacts)

        p._replace_segments(
            job_id,
            [(item["text"], item["translated"]) for item in translated],
            4.8,
            [(item["start"], item["end"]) for item in translated],
        )
        update_job(job_id, status="processing", stage="tts", progress=60)
        print("Translation complete and saved to DB.")
    else:
        print(f"Already have {len(segments)} segments in DB, skipping translation.")

    print("Step 2: Exporting video...")
    import asyncio
    asyncio.run(p.export(job_id))
    print("Export complete!")

    output_dir = Path("/Users/mktmda/Movies")
    src_final = Path(f"/Users/mktmda/Projects/vibe-coding/video-dub/data/jobs/{job_id}/final.mp4")
    dest_final = output_dir / "How To Animate a Short Film with Blender + Higgsfield (Full Breakdown)_VN.mp4"
    if src_final.exists():
        import shutil
        shutil.copyfile(str(src_final), str(dest_final))
        print(f"Copied final video to: {dest_final}")
        
        # Nén 720p
        from tools.watch_folder import compress_video, send_telegram_video
        compressed = compress_video(dest_final)
        target_video = compressed if compressed and compressed.is_file() else dest_final
        
        print("Sending to Telegram AgentLab topic 3...")
        send_telegram_video(target_video, caption=f"✅ {dest_final.stem}")
        print("Telegram delivered!")

if __name__ == "__main__":
    run()
