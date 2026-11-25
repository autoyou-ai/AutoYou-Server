"""Replicate reminders in the scheduler's own store. Delivery requires a chosen target."""
import datetime
import hashlib
import json
import re
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from shared.reminder_repeat import MAX_INTERVAL, next_alert

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from shared import content_access, scheduler_service as scheduler
from shared.paired_content import require_pair
from autoyou_agents.shared_tools import scheduler_mission_control as mission


def published(row):
    due = int(round(float(row.get("timestamp_s", 0)) * 1000))
    rule = dict(row.get("native_reminder") or {"due_at": due, "critical": True, "completed": False})
    title, _, body = str(row.get("message", "")).partition("\n")
    result = {"id": str(row["id"]), "title": title, "body": body, "reminder": rule,
              "tone": row.get("native_tone"), "time_zone": row.get("native_time_zone", ""),
              "scheduled": not row.get("collection_only") and next_alert(rule) is not None}
    value = json.dumps([result, row.get("timestamp_s"), row.get("creator_owner_key"), row.get("delivery_target")], sort_keys=True)
    result["revision"] = hashlib.sha256(value.encode()).hexdigest()
    return result


def _verify(request, action):
    require_pair(request, "reminders_agent")
    return mission._api_auth_error("reminders_agent", request) or content_access.deny(request, action)


def _tone_path(digest):
    if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise HTTPException(400, "Invalid reminder tone")
    from shared.platform_runtime import get_service_data_dir
    return get_service_data_dir("reminders_agent/tones", anchor=__file__) / digest


def _rule(data):
    rule = data.get("reminder")
    if not isinstance(rule, dict) or set(rule) - {"due_at", "critical", "completed", "repeat", "snoozed_until", "tone_attachment_id"}:
        raise HTTPException(400, "Invalid reminder rule")
    for key in ("due_at", "snoozed_until"):
        val = rule.get(key)
        if key == "snoozed_until" and val is None:
            continue
        if type(val) is not int or not 0 < val <= 253402300799000:
            raise HTTPException(400, "Invalid reminder time")
    if any(type(rule.get(key, False)) is not bool for key in ("critical", "completed")):
        raise HTTPException(400, "Invalid reminder state")
    repeat = rule.get("repeat")
    if repeat is not None:
        if not isinstance(repeat, dict) or repeat.get("frequency") not in {"daily","weekly","monthly","yearly"}:
            raise HTTPException(400, "Invalid reminder repeat rule")
        if set(repeat) - {"frequency", "interval", "hour", "minute", "fired", "count", "weekdays", "start", "until"}:
            raise HTTPException(400, "Unknown repeat field")
        for key, low, high in (("interval",1,MAX_INTERVAL[repeat["frequency"]]),("hour",0,23),("minute",0,59),("fired",0,1000000),("count",1,999)):
            val = repeat.get(key, {"interval":1,"fired":0}.get(key))
            if key == "count" and val is None:
                continue
            if type(val) is not int or not low <= val <= high:
                raise HTTPException(400, "Invalid repeat rule")
        if repeat.get("count") is not None and repeat.get("fired", 0) > repeat["count"]:
            raise HTTPException(400, "Invalid number of occurrences")
        try:
            start = datetime.date.fromisoformat(repeat["start"])
            if repeat.get("until") and datetime.date.fromisoformat(repeat["until"]) < start:
                raise ValueError("end precedes start")
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(400, "Invalid repeat dates") from e
        days = repeat.get("weekdays", [])
        if not isinstance(days, list) or len(days) > 7 or any(type(d) is not int or not 1 <= d <= 7 for d in days) or len(set(days)) != len(days):
            raise HTTPException(400, "Invalid repeat weekdays")
    tone = data.get("tone")
    if tone is not None:
        if not isinstance(tone, dict) or not isinstance(tone.get("name"), str) or not 1 <= len(tone["name"]) <= 128 or not re.fullmatch(r"audio/[a-zA-Z0-9.+-]+", str(tone.get("mime",""))):
            raise HTTPException(400, "Invalid reminder tone")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", str(rule.get("tone_attachment_id",""))) or not _tone_path(tone.get("digest")).is_file():
            raise HTTPException(400, "Upload the reminder tone first")
    elif rule.get("tone_attachment_id"):
        raise HTTPException(400, "The reminder tone is missing")
    return rule


def register(app):
    @app.get("/api/sync/reminders")
    def catalog(request: Request):
        denied = _verify(request, "read")
        if denied is not None:
            return denied
        rows = scheduler.load_json(scheduler.REMINDERS_FILE)
        return {"items":[{"id": value["id"], "revision": value["revision"]} for value in map(published, rows)]}

    @app.api_route("/api/sync/tones/{digest}", methods=["GET","PUT"])
    async def tone(digest: str, request: Request):
        from shared import content_files
        from filelock import FileLock
        denied = _verify(request, "edit" if request.method == "PUT" else "read")
        if denied is not None:
            return denied
        path = _tone_path(digest)
        if request.method == "GET":
            if not path.is_file():
                raise HTTPException(404, "Tone not found")
            return content_files.response(path, request, "application/octet-stream")
        try:
            offset = int(request.query_params["offset"])
            size = int(request.query_params["size"])
        except (KeyError, ValueError) as e:
            raise HTTPException(400, "Specify the tone length and offset") from e
        if not 0 <= offset < size <= 10 * 1024 * 1024:
            raise HTTPException(400, "Invalid reminder tone range")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > min(1024 * 1024, size-offset):
                raise HTTPException(413, "Tone chunk exceeds its range")
            raw.extend(chunk)
        if not raw:
            raise HTTPException(400, "Empty tone chunk")
        owner = hashlib.sha256(request.headers["x-autoyou-webrtc-session-id"].encode()).hexdigest()[:24]
        part = path.with_name(owner + "." + digest + ".part")
        with FileLock(str(path.parent / ".uploads.lock"), timeout=15):
            if path.exists():
                if content_files.size(path) != size:
                    raise HTTPException(409, "Wrong length for this tone")
                return {"received":size,"digest":digest}
            if not part.exists():
                for stale in path.parent.glob("*.part"):
                    if stale.stat().st_mtime < time.time()-86400:
                        content_files.discard(stale)
                if offset or len(list(path.parent.glob(owner + ".*.part"))) >= 4:
                    raise HTTPException(409, "Restart the tone upload")
                content_files.create(part)
            received = content_files.size(part)
            if offset == received:
                content_files.append(part, offset, bytes(raw))
                received += len(raw)
            elif offset + len(raw) > received or bytes(raw) != b"".join(content_files.read_range(part,offset,offset+len(raw))):
                raise HTTPException(409, "Restart the tone upload")
            if received == size:
                hasher = hashlib.sha256()
                for block in content_files.read_range(part,0,size):
                    hasher.update(block)
                if hasher.hexdigest() != digest:
                    content_files.discard(part)
                    raise HTTPException(400, "Incorrect tone checksum")
                content_files.finish_upload(part,path,size)
            return {"received":received, "digest":digest if received == size else None}

    @app.api_route("/api/sync/reminders/{identifier}", methods=["GET","PUT","DELETE"])
    async def record(identifier: str, request: Request):
        action = {"GET":"read","PUT":"edit","DELETE":"delete"}[request.method]
        denied = _verify(request, action)
        if denied is not None:
            return denied
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
            raise HTTPException(400, "Invalid reminder ID")
        data = None
        if request.method == "PUT":
            raw = bytearray()
            async for part in request.stream():
                if len(raw) + len(part) > 128 * 1024:
                    raise HTTPException(413, "Reminder is too large")
                raw.extend(part)
            try:
                data = json.loads(raw)
            except ValueError as e:
                raise HTTPException(400, "Invalid reminder") from e
            if not isinstance(data, dict) or not isinstance(data.get("title"),str) or not isinstance(data.get("body",""),str):
                raise HTTPException(400, "Invalid reminder")
            rule = _rule(data)
            message = (data["title"].strip() + "\n" + data.get("body","")).rstrip()
            if not message or len(message) > 64000:
                raise HTTPException(400, "Invalid reminder message")
            zone = data.get("time_zone","")
            try:
                ZoneInfo(zone or "UTC")
            except (ValueError, TypeError, ZoneInfoNotFoundError) as e:
                raise HTTPException(400, "Invalid reminder time zone") from e
            if not isinstance(zone,str) or len(zone) > 120:
                raise HTTPException(400, "Invalid reminder time zone")
        with scheduler.reminder_lock(scheduler.REMINDERS_FILE):
            rows = scheduler.load_json(scheduler.REMINDERS_FILE)
            old = next((row for row in rows if row.get("id") == identifier), None)
            if request.method == "GET":
                if old is None:
                    raise HTTPException(404, "Reminder not found")
                return published(old)
            expected = data.get("base_revision") if data is not None else request.query_params.get("base_revision")
            if old is None and request.method == "DELETE":
                return {"deleted":True}
            if expected != (published(old)["revision"] if old else ""):
                raise HTTPException(409, "Reminder changed on the computer")
            if request.method == "DELETE":
                scheduler.save_json(scheduler.REMINDERS_FILE,[row for row in rows if row is not old])
                return {"deleted":True}
            if old is None:
                try:
                    uuid.UUID(identifier)
                except ValueError as e:
                    raise HTTPException(400, "A new reminder needs a UUID") from e
            row = dict(old or {"id":identifier,"created_at_s":time.time(),"collection_only":True})
            selected = data.get("delivery_target_id")
            if selected:
                option = next((item for item in mission._build_target_catalog()["options"] if item["id"] == selected), None)
                if not option or not option.get("reply_target"):
                    raise HTTPException(400, "Choose an available reminder delivery target")
                owner, user, target = mission._resolve_target_context({"owner_key":option.get("owner_key"),"delivery_target":option["reply_target"]})
                row.update(creator_owner_key=owner, creator_user_id=user, delivery_target=target, lock_reply_target=True, collection_only=False)
            due = next_alert(rule) or rule["due_at"]
            row.update(message=message, timestamp_s=due/1000, iso=datetime.datetime.fromtimestamp(due/1000,datetime.timezone.utc).isoformat(),
                       updated_at_s=time.time(),native_reminder=rule,native_tone=data.get("tone"),native_time_zone=zone)
            scheduler.save_json(scheduler.REMINDERS_FILE,[row if item is old else item for item in rows] if old else [*rows,row])
            return published(row)
