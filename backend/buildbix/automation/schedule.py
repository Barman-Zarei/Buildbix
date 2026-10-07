"""Human-friendly schedules -> structured schedule (UTC). Advanced users can read/edit the structured form."""
import re
from datetime import datetime, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class ScheduleError(ValueError):
    pass


def parse_schedule(text: str) -> dict:
    t = text.strip().lower()
    if m := re.fullmatch(r"every (\d+) (minute|minutes|hour|hours)", t):
        n = int(m.group(1))
        minutes = n if m.group(2).startswith("minute") else n * 60
        if not 5 <= minutes <= 60 * 24 * 30:
            raise ScheduleError("Interval must be between 5 minutes and 30 days")
        return {"kind": "interval", "minutes": minutes}
    if m := re.fullmatch(r"(?:every day|daily) at (\d{1,2}):(\d{2})", t):
        return {"kind": "daily", **_hm(m.group(1), m.group(2))}
    if m := re.fullmatch(rf"every ({'|'.join(DAYS)}) at (\d{{1,2}}):(\d{{2}})", t):
        return {"kind": "weekly", "weekday": DAYS.index(m.group(1)), **_hm(m.group(2), m.group(3))}
    raise ScheduleError("Try: 'every day at 03:00', 'every monday at 09:30', 'every 6 hours' (times are UTC)")


def _hm(h: str, m: str) -> dict:
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ScheduleError("Invalid time")
    return {"hour": h, "minute": m}


def next_run(schedule: dict, after: datetime) -> datetime:
    kind = schedule["kind"]
    if kind == "interval":
        return after + timedelta(minutes=schedule["minutes"])
    cand = after.replace(hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0)
    if kind == "daily":
        return cand if cand > after else cand + timedelta(days=1)
    if kind == "weekly":
        days_ahead = (schedule["weekday"] - cand.weekday()) % 7
        cand += timedelta(days=days_ahead)
        return cand if cand > after else cand + timedelta(days=7)
    raise ScheduleError("Unknown schedule kind")
