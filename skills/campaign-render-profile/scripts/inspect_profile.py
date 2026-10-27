from pathlib import Path
import hashlib
import json

relative = Path("marketing/autoyou-feature-video-campaign")
report_name = Path("qa/render_reports/agent_notes_agent.json")
campaign = next((p / relative for p in (Path.cwd(), *Path.cwd().parents)
                 if (p / relative / report_name).is_file()), None)
if campaign is None:
    raise SystemExit("Run this helper from inside the AutoYou workspace.")
report = json.loads((campaign / report_name).read_text())
video = campaign / "videos" / (report["item_id"] + ".mp4")
digest = hashlib.sha256(video.read_bytes()).hexdigest()
if digest != report["video_sha256"]:
    raise SystemExit("Video hash differs from its render report. Regenerate the report.")
print("Current genuine film profile")
print("Feature:", report["item_id"].replace("agent_", "", 1).replace("_", " "))
print("Canvas:", report["dimensions"], "at", report["fps"], "fps")
print("Narrator:", report["narration_voice"])
print("Native source assets:", len(report["source_media"]))
print("Recorded interaction shots:", report["native_recording_shots"])
print("Video hash verified: yes")
