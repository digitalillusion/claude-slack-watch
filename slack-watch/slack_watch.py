#!/usr/bin/env python3
"""Slack watch: relays incoming Slack messages to a Claude Code session, on the schedule in a config file.

  slack_watch.py check    Validate the config and print, as JSON, the cron entries of each window's timed check,
                          the windows open now, and each recurring task's next run as a one-shot cron entry. Exits 1
                          when the config has errors. Needs no Slack token.
  slack_watch.py listen   Hold a Socket Mode connection (app token in SLACK_APP_TOKEN) and append one JSON line per
                          message that an open window watches to the events file. A Claude Code Monitor tails that
                          file and wakes the session, which reads the message through the Slack connector.

The config is SLACK_WATCH_CONFIG, default ~/.config/slack-watch/config.json, in the format SKILL.md (next to this
file) describes. `listen` re-reads it whenever it changes and keeps the previous one when an edit does not validate.
It leaves "my_threads" to the timed checks, since telling a thread the person took part in needs an API call per
message, and skips the person's own messages, bots, edits and deletions. It never writes message text, only where
and when.
"""

import datetime
import json
import logging
import os
import re
import signal
import sys
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONFIG = os.path.expanduser(os.environ.get("SLACK_WATCH_CONFIG", "~/.config/slack-watch/config.json"))
DEFAULT_EVENTS = "~/.local/state/slack-watch/events.jsonl"
# Plain messages and the subtypes that are still someone writing.
WRITTEN_SUBTYPES = {None, "thread_broadcast", "file_share"}
WATCHABLE = {"dms", "group_dms", "mentions", "my_threads"}
# Where a task posts its result: a DM to the person, or only the session report (a channel is "channel:<name>").
POST_TARGETS = {"me", "session"}
# A task run schedules its next run later than this, so a run that fires a little early (a one-shot job on :00 or
# :30 can, by up to 90 s) does not schedule itself again for the same time.
RESCHEDULE_MARGIN = datetime.timedelta(minutes=2)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("slack-watch")


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def day_set(spec: str) -> set[int]:
    """Weekday numbers (Monday = 0) of a spec such as "mon-fri", "sat,sun", "fri-mon" or "*"."""
    if spec in ("", "*"):
        return set(range(7))
    days: set[int] = set()
    for part in spec.lower().split(","):
        first, _, last = part.strip().partition("-")
        if first not in DAYS or (last and last not in DAYS):
            raise ValueError(f"days {spec!r}: use mon … sun, ranges such as mon-fri, commas, or *")
        a = DAYS.index(first)
        b = DAYS.index(last) if last else a
        days.update(range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)])
    return days


def minutes(hhmm: str) -> int:
    match = TIME.match(str(hhmm))
    if not match:
        raise ValueError(f"time {hhmm!r} is not HH:MM")
    return int(match[1]) * 60 + int(match[2])


def covers(window: dict, weekday: int, minute: int) -> bool:
    """Whether the window covers a minute of a weekday. `from` = `to` means all day; a window that runs past
    midnight belongs to the day it starts on."""
    start, end = minutes(window["from"]), minutes(window["to"])
    days = day_set(window.get("days", "*"))
    if start == end:
        return weekday in days
    if start < end:
        return weekday in days and start <= minute < end
    if minute >= start:
        return weekday in days
    return minute < end and (weekday - 1) % 7 in days


def is_open(window: dict, now: datetime.datetime) -> bool:
    return covers(window, now.weekday(), now.hour * 60 + now.minute)


def problems(config: dict) -> tuple[list[str], list[str]]:
    """Errors that make the config unusable, and warnings."""
    errors: list[str] = []
    warnings: list[str] = []
    if not str(config.get("slack", {}).get("user_id", "")).startswith(("U", "W")):
        errors.append("slack.user_id: missing or not a Slack user id (U…)")
    try:
        here = datetime.datetime.now(ZoneInfo(config.get("timezone", "UTC")))
        if here.utcoffset() != here.astimezone().utcoffset():
            warnings.append(
                f"timezone {config.get('timezone')} is not this machine's: windows follow it, but the timed checks "
                "run on the machine's clock, so their hours are shifted"
            )
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"timezone: unknown zone {config.get('timezone')!r}")
    channels = config.get("channels", {})
    if not isinstance(channels, dict) or not all(isinstance(v, str) and v[:1] in ("C", "G", "D") for v in channels.values()):
        errors.append("channels: must map short names to channel ids (C…, G… or D…)")
        channels = {}
    windows = config.get("windows")
    if not isinstance(windows, list) or not windows:
        errors.append("windows: at least one window is needed")
        windows = []
    names = [window.get("name") for window in windows]
    for i, window in enumerate(windows):
        label = f"windows[{i}] ({window.get('name') or 'unnamed'})"
        if not window.get("name") or names.count(window["name"]) > 1:
            errors.append(f"{label}: needs a name of its own")
        for check in (lambda: minutes(window.get("from")), lambda: minutes(window.get("to")), lambda: day_set(window.get("days", "*"))):
            try:
                check()
            except ValueError as error:
                errors.append(f"{label}: {error}")
        every = window.get("every_minutes")
        if not (isinstance(every, int) and every >= 5 and (60 % every == 0 or (every % 60 == 0 and 1440 % every == 0))):
            errors.append(f"{label}: every_minutes must be at least 5 and divide 60 (5, 10, 15, 20, 30) or be whole hours that divide a day (60, 120, 180, 240, 360, 480, 720, 1440)")
        lookback = window.get("lookback_minutes", 0)
        if not (isinstance(lookback, int) and lookback >= 0):
            errors.append(f"{label}: lookback_minutes must be a whole number of minutes, 0 or more")
        watch = window.get("watch")
        if not isinstance(watch, list) or not watch:
            errors.append(f"{label}: watch lists nothing")
            watch = []
        for item in watch:
            if item not in WATCHABLE and not (isinstance(item, str) and item.startswith("channel:") and item[8:] in channels):
                errors.append(f"{label}: unknown watch item {item!r} (use {', '.join(sorted(WATCHABLE))} or channel:<a key of channels>)")
    tasks = config.get("tasks", [])
    if not isinstance(tasks, list):
        errors.append("tasks: must be a list")
        tasks = []
    task_names = [task.get("name") for task in tasks]
    for i, task in enumerate(tasks):
        label = f"tasks[{i}] ({task.get('name') or 'unnamed'})"
        if not task.get("name") or task_names.count(task["name"]) > 1:
            errors.append(f"{label}: needs a name of its own")
        try:
            day_set(task.get("days", "*"))
            if not task_times(task):
                errors.append(f"{label}: at needs at least one time")
        except ValueError as error:
            errors.append(f"{label}: {error}")
        if not (isinstance(task.get("instructions"), str) and task["instructions"].strip()):
            errors.append(f"{label}: instructions are missing")
        post_to = task.get("post_to", "me")
        if post_to not in POST_TARGETS and not (isinstance(post_to, str) and post_to.startswith("channel:") and post_to[8:] in channels):
            errors.append(f"{label}: post_to must be {', '.join(sorted(POST_TARGETS))} or channel:<a key of channels>")
    return errors, warnings


def cron_field(values: list[int], size: int) -> str:
    """A cron field for values out of 0 … size-1: "*", or ranges such as "0-8,18-23"."""
    values = sorted(values)
    if len(values) == size:
        return "*"
    parts, start = [], values[0]
    for previous, value in zip(values, [*values[1:], None]):
        if value != previous + 1:
            parts.append(str(start) if start == previous else f"{start}-{previous}")
            start = value
    return ",".join(parts)


def crons(window: dict) -> list[str]:
    """Cron entries (this machine's clock) for the window's timed check: one per group of weekdays with the same
    hours. Checks fall off :00 and :30. An hour is kept when one of its checks falls inside the window, so a window
    that does not start or end on the hour can get a check just outside it: the check skips a closed window."""
    every = window["every_minutes"]
    if every < 60:
        offset = min(7, every - 1)
        minute_field = f"{offset}-59/{every}"
        fires = [(hour, minute) for hour in range(24) for minute in range(offset, 60, every)]
    else:
        first = minutes(window["from"]) // 60
        minute_field = "7"
        fires = [(hour, 7) for hour in range(24) if (hour - first) % (every // 60) == 0]
    days_by_hours: dict[tuple[int, ...], list[int]] = {}
    for weekday in range(7):
        hours = tuple(sorted({hour for hour, minute in fires if covers(window, weekday, hour * 60 + minute)}))
        if hours:
            # Cron counts weekdays from Sunday = 0.
            days_by_hours.setdefault(hours, []).append((weekday + 1) % 7)
    return [f"{minute_field} {cron_field(list(hours), 24)} * * {cron_field(days, 7)}" for hours, days in days_by_hours.items()]


def task_times(task: dict) -> list[int]:
    """The task's `at`, one HH:MM or a list of them, as minutes of the day."""
    at = task.get("at")
    return sorted(minutes(time) for time in (at if isinstance(at, list) else [at]))


def next_run(task: dict, now: datetime.datetime) -> datetime.datetime:
    """The task's first run later than `now` plus RESCHEDULE_MARGIN, in `now`'s timezone."""
    days = day_set(task.get("days", "*"))
    earliest = now + RESCHEDULE_MARGIN
    for offset in range(8):
        day = (now + datetime.timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        for minute in task_times(task):
            run = datetime.datetime.combine(day, datetime.time(minute // 60, minute % 60), now.tzinfo)
            if run > earliest:
                return run
    raise ValueError(f"task {task.get('name')!r} has no run in the next week")


def task_report(task: dict, now: datetime.datetime) -> dict:
    """The task's next run, and the one-shot cron entry (this machine's clock) that fires it. Tasks run from one-shot
    jobs, each scheduling the next, because a recurring daily job can fire up to 30 minutes late."""
    run = next_run(task, now)
    local = run.astimezone()
    return {
        "name": task["name"],
        "next": run.isoformat(timespec="minutes"),
        "cron": f"{local.minute} {local.hour} {local.day} {local.month} *",
        "post_to": task.get("post_to", "me"),
    }


def events_file(config: dict) -> str:
    return os.path.expanduser(config.get("listener", {}).get("events_file", DEFAULT_EVENTS))


def check() -> int:
    report: dict = {"config": CONFIG}
    try:
        config = load(CONFIG)
    except (OSError, ValueError) as error:
        report["errors"] = [f"cannot read the config: {error}"]
        print(json.dumps(report, indent=2))
        return 1
    errors, warnings = problems(config)
    report |= {"errors": errors, "warnings": warnings}
    if not errors:
        now = datetime.datetime.now(ZoneInfo(config.get("timezone", "UTC")))
        report["now"] = now.isoformat(timespec="minutes")
        report["windows"] = [{"name": w["name"], "crons": crons(w), "open": is_open(w, now)} for w in config["windows"]]
        report["open_now"] = [w["name"] for w in config["windows"] if is_open(w, now)]
        report["tasks"] = [task_report(task, now) for task in config.get("tasks", [])]
        report["events_file"] = events_file(config)
        report["service"] = config.get("listener", {}).get("service", "slack-watch")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if errors else 0


class Config:
    """The config file, re-read when it changes. An edit that does not validate is logged and ignored."""

    def __init__(self, path: str):
        self.path = path
        self.mtime: float | None = None
        self.data: dict = {}

    def current(self) -> dict:
        mtime = os.stat(self.path).st_mtime
        if mtime != self.mtime:
            self.mtime = mtime
            try:
                data = load(self.path)
                errors, _ = problems(data)
            except (OSError, ValueError) as error:
                errors = [str(error)]
            if errors:
                log.error("config %s not applied: %s", self.path, "; ".join(errors))
            else:
                self.data = data
                log.info("config loaded from %s", self.path)
        if not self.data:
            raise ValueError(f"no valid config at {self.path}")
        return self.data


def watched(config: dict, event: dict) -> str | None:
    """What a window open now watches this message as, or None."""
    me = config["slack"]["user_id"]
    if event.get("type") != "message" or event.get("subtype") not in WRITTEN_SUBTYPES:
        return None
    if event.get("bot_id") or event.get("user") in (None, me):
        return None
    now = datetime.datetime.now(ZoneInfo(config.get("timezone", "UTC")))
    named = {channel_id: name for name, channel_id in config.get("channels", {}).items()}
    for window in config["windows"]:
        if not is_open(window, now):
            continue
        watch = set(window["watch"])
        channel = event.get("channel")
        if channel in named and f"channel:{named[channel]}" in watch:
            return f"channel:{named[channel]}"
        if event.get("channel_type") == "im" and "dms" in watch:
            return "dm"
        if event.get("channel_type") == "mpim" and "group_dms" in watch:
            return "group_dm"
        if f"<@{me}>" in event.get("text", "") and "mentions" in watch:
            return "mention"
    return None


def listen() -> None:
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.response import SocketModeResponse

    config = Config(CONFIG)
    try:
        events = events_file(config.current())
    except (OSError, ValueError) as error:
        sys.exit(f"slack-watch: {error}")
    os.makedirs(os.path.dirname(events), exist_ok=True)

    def handle(client, request) -> None:
        # Acknowledge first, or Slack redelivers the envelope.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        if request.type != "events_api":
            return
        event = request.payload.get("event", {})
        try:
            kind = watched(config.current(), event)
        except (OSError, ValueError) as error:
            log.error("message skipped: %s", error)
            return
        if not kind:
            return
        line = {
            "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "kind": kind,
            "channel": event.get("channel"),
            "user": event.get("user"),
            "ts": event.get("ts"),
            "thread_ts": event.get("thread_ts"),
        }
        with open(events, "a", encoding="utf-8") as out:
            out.write(json.dumps(line) + "\n")
        log.info("recorded %s %s %s", kind, line["channel"], line["ts"])

    client = SocketModeClient(app_token=os.environ["SLACK_APP_TOKEN"])
    client.socket_mode_request_listeners.append(handle)
    client.connect()
    log.info("connected; writing to %s", events)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    client.close()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "listen"
    if command == "check":
        sys.exit(check())
    if command == "listen":
        listen()
    else:
        sys.exit(f"usage: {sys.argv[0]} [check|listen]")
