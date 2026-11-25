"""Wall-clock recurrence shared with the iOS and Android reminder contract."""
import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _zone(name):
    value = str(name or "UTC").strip()
    return timezone.utc if value.upper() in {"UTC", "ETC/UTC", "Z"} else ZoneInfo(value)


MAX_INTERVAL = {"daily":365, "weekly":52, "monthly":24, "yearly":10}


def next_alert(rule):
    if rule.get("completed"):
        return rule.get("snoozed_until") if rule.get("repeat") else None
    if rule.get("repeat") and rule.get("snoozed_until"):
        return min(rule["due_at"], rule["snoozed_until"])
    return rule["due_at"]


def reschedule(row, timestamp_s, iso):
    row.update(timestamp_s=timestamp_s, iso=iso)
    if row.get("native_reminder"):
        row["native_reminder"].update(due_at=round(timestamp_s * 1000), completed=False, snoozed_until=None)


def next_occurrence(rule, after_ms, zone):
    frequency, interval = rule["frequency"], rule.get("interval", 1)
    if rule.get("count") is not None and rule.get("fired", 0) >= rule["count"]:
        return None
    first = date.fromisoformat(rule["start"])
    last = date.fromisoformat(rule["until"]) if rule.get("until") else date.max
    local = max(first, datetime.fromtimestamp(after_ms / 1000, zone).date())
    weekdays = rule.get("weekdays") or [first.isoweekday()]
    first_monday = first.toordinal() - first.weekday()
    duration = {"daily":interval+1,"weekly":7*interval+7,"monthly":31*interval+62,"yearly":366*interval+366}[frequency]
    for _ in range(duration + 1):
        if local > last:
            return None
        days = (local-first).days
        if frequency == "daily":
            matches = days % interval == 0
        elif frequency == "weekly":
            matches = local.isoweekday() in weekdays and ((local.toordinal()-first_monday)//7) % interval == 0
        else:
            months = (local.year-first.year)*12 + local.month-first.month
            matches = months % (interval if frequency == "monthly" else interval*12) == 0 and local.day == min(first.day,calendar.monthrange(local.year,local.month)[1])
        if matches:
            wall = datetime(local.year,local.month,local.day,rule["hour"],rule["minute"],tzinfo=zone)
            instant = round(wall.timestamp()*1000)
            if instant > after_ms:
                return instant
        if local == date.max:
            break
        local += timedelta(days=1)
    return None


def after_delivery(row, now):
    rule = dict(row["native_reminder"])
    repeat, due = rule.get("repeat"), rule["due_at"]
    now_ms = round(now*1000)
    if rule.get("snoozed_until") and rule["snoozed_until"] <= now_ms + 1000:
        rule["snoozed_until"] = None
    if not repeat:
        rule["completed"] = True
    elif not rule.get("completed") and due <= now_ms + 1000:
        zone = _zone(row.get("native_time_zone") or "UTC")
        repeat = dict(repeat)
        if repeat.get("count") is not None:
            repeat["fired"] = min(repeat["count"],repeat.get("fired",0)+1)
            last = due
            while (missed := next_occurrence(repeat,last,zone)) is not None and missed <= now_ms:
                repeat["fired"] += 1
                last = missed
        rule["repeat"] = repeat
        following = next_occurrence(repeat,max(due,now_ms),zone)
        if following is None:
            rule.update(completed=True,snoozed_until=None)
        else:
            rule["due_at"] = following
    next_ms = next_alert(rule) or rule["due_at"]
    return {**row, "native_reminder":rule,"updated_at_s":now,"timestamp_s":next_ms/1000,
            "iso":datetime.fromtimestamp(next_ms/1000,timezone.utc).isoformat()}
