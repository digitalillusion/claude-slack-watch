# claude-slack-watch

A [Claude Code](https://claude.com/claude-code) skill that watches your Slack and answers, as you, the messages
you have left unanswered. You choose what it watches (channels, DMs, group DMs, mentions, threads you took part in),
on which days and hours, and how often it looks. It replies in your account, through the claude.ai Slack connector,
with facts from the sources you allow, and it never decides anything for you. It also runs recurring tasks you write
in plain words, such as a morning brief posted to yourself.

The skill is called `slack-watch`; this repository is its home.

## How it works

```
Slack ◀──── claude.ai Slack connector (read, search, send as you) ────▶ Claude Code session ──▶ replies and task posts
  │                                                                         ▲    ▲    ▲
  │                               timed checks, every N minutes per window ─┘    │    │
  │                                             recurring tasks, at their times ─┘    │
  └── Socket Mode ──▶ slack_watch.py listen ──▶ events.jsonl ── Monitor ──────────────┘  (optional: real time)
```

- **Timed checks.** `/slack-watch` creates one Claude Code cron job per window. Each run reads what the window
  watches, finds the messages still waiting for you, and answers them.
- **Recurring tasks (optional).** Instructions you write, run at set times on set days, with the result posted to
  you, to a channel, or kept in the session.
- **Real-time listener (optional).** A small Python service holds a Slack Socket Mode connection and appends one line
  per watched message to an events file: channel, sender, timestamps, **never the text**. The Claude Code session
  tails that file with a Monitor and answers within seconds. Without it, answers wait for the next timed check.
- **Config.** Everything personal (who you are, channels, schedule, reply language and tone, people, fact sources,
  tasks) lives in `~/.config/slack-watch/config.json`, outside this repository.

## What it answers, and what it doesn't

- It answers **pending** messages: from someone else, with nothing from you after them in the same DM, group DM,
  channel or thread, and that personally concern you: a DM to you, a message that mentions or names you, or a reply
  to you in a thread. A message in a group DM or channel addressed to the group, to someone else or to nobody in
  particular is left to you and listed in the session. It skips bots, thanks, greetings, FYIs, status updates and
  chit-chat that needs no answer.
- It answers in the thread when the message is a thread reply or a question about an earlier post, and in the
  conversation otherwise. It re-reads the conversation right before sending, so the same answer never goes out twice.
- It states **facts only**, from the sources you list, and says what it checked and what it didn't (for example,
  code read but tests not run).
- It answers whether something works only with evidence that settles it, and reads what the people involved already
  reported first. A weak signal (a route that refuses a request without a login, a change deployed but never tried)
  is left out; when nothing settles it, the reply says you will confirm.
- It **never decides for you**: no deadlines, priorities, approvals, merges or new work. It replies that you'll pick
  it up, or asks a clarifying question, and lists the item for you in the session.
- It **never acts on a message's request**: messages are information, not instructions. Code, branches, PRs, tickets
  and documents are not changed because someone asked in Slack.
- Replies go out in your name. Slack marks messages sent through the connector with a "Sent using Claude" note, so
  colleagues can tell. Check that your workspace is fine with this before you turn it on.

## Requirements

- Claude Code, signed in to the claude.ai account that has the **Slack connector** connected to your workspace
  account (<https://claude.ai/customize/connectors>). Check in Claude Code with `/mcp`: the `claude.ai Slack` tools
  must be listed. If you connected Slack on a different claude.ai account, Claude Code will not see it.
- Python 3.10 or newer (`slack_watch.py check` uses only the standard library).
- For the real-time listener only: Linux with systemd user services, and permission to install a Slack app in your
  workspace (some workspaces need an admin to approve it).

## Install

```bash
git clone https://github.com/digitalillusion/claude-slack-watch.git ~/src/claude-slack-watch
mkdir -p ~/.claude/skills
ln -s ~/src/claude-slack-watch/slack-watch ~/.claude/skills/slack-watch
```

Start a new Claude Code session; `/slack-watch` is now available.

## Set up

Run `/slack-watch setup` in Claude Code. It walks you through:

1. **Account**: it reads the connected Slack profile and asks you to confirm it is the one to answer as. It also asks
   for your workspace id, the `T…` in a Slack web link (`app.slack.com/client/T…/…`).
2. **Timezone**: the machine's, since the timed checks run on its clock.
3. **Channels**: pick the ones to watch from your channel list; each gets a short name.
4. **Windows**: when to watch what. Presets: *channels in work hours, everything after* (weekdays 09:00–18:00 the
   chosen channels every 15 min; 18:00–09:00 DMs, group DMs, mentions, your threads and the channels every 30 min), or
   *everything, all the time*, or custom.
5. **Replies**: language (the sender's, or a fixed one), tone, people with their own language or a note, fact sources
   (repositories, `gh`, Linear, URLs, all read-only), context files to read first, standing notes.
6. **Tasks** (optional): recurring jobs, each with days, time(s), instructions and where the result goes.
7. It shows the finished config, writes it after your yes, and validates it.

Run it again later to change anything: it starts from your current config.

A finished config looks like this:

```json
{
  "version": 1,
  "slack": { "team": "Acme", "team_id": "T0123456789", "user_id": "U0123456789", "email": "sam@acme.example", "name": "Sam" },
  "timezone": "Europe/Rome",
  "channels": { "team": "C0123456789" },
  "windows": [
    { "name": "day", "days": "mon-fri", "from": "09:00", "to": "18:00", "every_minutes": 60,
      "lookback_minutes": 0, "watch": ["channel:team"] },
    { "name": "night", "days": "*", "from": "18:00", "to": "09:00", "every_minutes": 120,
      "lookback_minutes": 60, "watch": ["dms", "group_dms", "mentions", "my_threads", "channel:team"] }
  ],
  "replies": {
    "language": "sender",
    "tone": "short and factual",
    "people": { "U0987654321": { "name": "Alex", "language": "en", "note": "Leads the release; prefers bullet points." } },
    "sources": ["repo ~/src/acme-api (read-only)", "GitHub through gh (read-only)", "Linear (read-only)"],
    "context_files": [],
    "notes": ["Release 2.4 ships on Thursdays."]
  },
  "tasks": [
    { "name": "morning-brief", "days": "mon-fri", "at": "09:20", "post_to": "me",
      "instructions": "Read today's agenda post by Alex (U0987654321) in #team. DM me the status of my open tickets and PRs, the ones on the agenda first, and list the agenda items that need an answer from me. Sources: Linear and gh, read-only. If the agenda is not posted yet, say so and send the status anyway." }
  ],
  "listener": { "events_file": "~/.local/state/slack-watch/events.jsonl", "service": "slack-watch" }
}
```

## Config reference

The config is read from `$SLACK_WATCH_CONFIG`, or `~/.config/slack-watch/config.json` when that is not set. The full
reference is also in [`slack-watch/SKILL.md`](slack-watch/SKILL.md#config).

| Key | Meaning |
|---|---|
| `slack.user_id` | Your Slack user id (`U…`): the account replies are sent as, and whose messages never count as pending. `/slack-watch` refuses to start if the connector is signed in as someone else. |
| `slack.team`, `team_id`, `email`, `name` | Your workspace name and id (`T…`), email and display name. |
| `timezone` | The zone the window and task times are in. Keep it the machine's: the cron jobs run on the machine's clock, and `check` warns when the two differ. |
| `channels` | Short names for channel ids (`C…`, `G…` or `D…`), used as `channel:<name>` in `watch` and `post_to`. |
| `windows[].name` | A unique name. |
| `windows[].days` | `*`, `mon-fri`, `sat,sun`, `fri-mon`, … |
| `windows[].from`, `to` | `HH:MM`. A `to` before `from` runs past midnight, and the window belongs to the day it starts on (`mon-fri` 18:00–09:00 covers Friday night into Saturday morning, not Sunday night). `from` = `to` means all day. |
| `windows[].every_minutes` | How often the timed check runs: at least 5, dividing 60 (5, 10, 15, 20, 30), or whole hours dividing a day (60, 120, 180, 240, 360, 480, 720, 1440). With the listener on, it is a safety net. |
| `windows[].lookback_minutes` | How far before the window opened its first check looks (60 catches a 17:55 message for an 18:00 window). |
| `windows[].watch` | Any of `channel:<name>`, `dms`, `group_dms`, `mentions` (of you, in any channel) and `my_threads` (threads you took part in). |
| `replies.language` | `sender` (answer in the language each message was written in) or a fixed language. |
| `replies.tone` | How replies should sound, in your words. |
| `replies.people` | Per person, by user id: a `name`, a `language` that overrides `replies.language`, and a `note`. |
| `replies.sources` | Where facts may come from (repositories, `gh`, Linear, URLs, …). All are used read-only. |
| `replies.context_files` | Files read for background before the first answer of a session and before each task run. |
| `replies.notes` | Standing facts every reply and task can rely on. |
| `tasks` | Recurring tasks; see below. Optional. |
| `listener.events_file` | Where the listener writes events (default `~/.local/state/slack-watch/events.jsonl`). |
| `listener.service` | The systemd user unit name of the listener (default `slack-watch`). |

## Recurring tasks

| Key | Meaning |
|---|---|
| `name` | A unique name. |
| `days` | As for windows. |
| `at` | One `HH:MM`, or a list of them. |
| `instructions` | What to do, in your words: what to read, from which sources, what to write. |
| `post_to` | `me` (the default: a DM to yourself), `channel:<name>`, or `session` (nothing is posted; handy to try a task out). |

- Tasks run whether or not a window is open.
- The instructions are yours, so the task follows them, with the same limits as replies: sources read-only, facts
  only, nothing decided for you. What it reads along the way, such as a colleague's post, is information, not
  instructions.
- The result is written in the tone `replies.tone` sets and in the language the instructions are written in, unless
  they say otherwise. When something the instructions rely on is missing (a post not written yet, a source that does
  not answer), the result says so instead of guessing.
- Each run is a one-shot job that first schedules the next run, because a recurring daily job can fire up to 30
  minutes late and a one-shot fires on time. Taking a task out of the config ends its runs.

## Use

| Command | What it does |
|---|---|
| `/slack-watch` | Checks the config and that the connector is signed in as `slack.user_id`, creates the timed checks and the task runs, arms the real-time watcher if the listener runs, and checks the open windows now. It refuses to start twice in a session. |
| `/slack-watch status` | Windows, cron entries, next task runs, jobs, watcher, listener service and its last log lines, and the last events |
| `/slack-watch stop` | Deletes the timed checks and the task runs, and stops the watcher. The listener service keeps running: `systemctl --user disable --now slack-watch` stops it. |
| `/slack-watch setup` | Creates or changes the config |

Every timed check, task run and real-time event ends with a short report in the session: what was answered or
posted, with links, and what was left for you.

Replies are posted with the Slack connector's send tool. If your Claude Code permission mode asks before every post,
allow that tool for this to run unattended.

### Changing the config

- Who to answer, what each window watches, reply settings and task instructions: every check and task run reads the
  config afresh, so edits apply from the next one. The listener re-reads the config when the file changes, and keeps
  the previous one if an edit does not validate.
- A window's `days`, hours or `every_minutes`: the cron jobs are made at start, so run `/slack-watch stop` and
  `/slack-watch`.
- A task's `days` or `at`: applies from the run after next (each run schedules the next from the config), or at once
  with stop and start.

### Checking the config

```bash
python3 ~/.claude/skills/slack-watch/slack_watch.py check
```

It validates the config and prints JSON: the `config` path, `errors` and `warnings`, the time `now`, each window's `crons` and whether
it is `open`, `open_now`, each task's `next` run and its one-shot `cron`, the `events_file` and the listener
`service`. It exits 1 when there are errors, needs no Slack token, and honours `SLACK_WATCH_CONFIG`.

## Real-time listener (optional)

Skip this if you cannot install Slack apps in your workspace: the timed checks work on their own.

**1. Python environment** with the pinned SDK:

```bash
python3 -m venv ~/.local/share/slack-watch/venv
~/.local/share/slack-watch/venv/bin/pip install 'slack_sdk==3.33.1'
```

**2. Slack app.** At <https://api.slack.com/apps> → **Create New App** → **From a manifest** → your workspace → paste:

```yaml
display_information:
  name: Slack Watch
  description: Relays incoming Slack messages to a local Claude Code session.
oauth_config:
  scopes:
    user:
      - im:history
      - mpim:history
      - groups:history
      - channels:history
settings:
  event_subscriptions:
    user_events:
      - message.im
      - message.mpim
      - message.groups
      - message.channels
  socket_mode_enabled: true
  org_deploy_enabled: false
  token_rotation_enabled: false
```

Then **Install to Workspace** → **Allow**, and **Basic Information** → **App-Level Tokens** → **Generate** with scope
`connections:write`. Copy the `xapp-…` token. The app only reads; replies still go through the claude.ai connector.

**3. Token**, in your own terminal (not in a Claude Code chat, so it stays out of transcripts):

```bash
mkdir -p ~/.config/slack-watch
(umask 077; printf 'SLACK_APP_TOKEN=%s\n' 'xapp-…' > ~/.config/slack-watch/env)
```

**4. Service**, as `~/.config/systemd/user/slack-watch.service`:

```ini
[Unit]
Description=Slack watch: relays incoming Slack messages to a Claude Code session
After=network-online.target

[Service]
EnvironmentFile=%h/.config/slack-watch/env
ExecStart=%h/.local/share/slack-watch/venv/bin/python %h/.claude/skills/slack-watch/slack_watch.py listen
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now slack-watch
loginctl enable-linger "$USER"   # optional: keep it running while you are logged out
```

**5. Verify.** `journalctl --user -u slack-watch -n 20` shows `connected`. Then have **someone else** message you in a
watched place during an open window: a line appears in the events file. If nothing arrives while connected, check
that the app's **Event Subscriptions** page lists the four user events, and reinstall the app after any scope change.
The session starts tailing the events at the next `/slack-watch` or timed check.

**What it records.** One JSON line per message that a window open at that moment watches:

```json
{"at": "2026-10-15T09:41:07+02:00", "kind": "dm", "channel": "D0123456789", "user": "U0987654321", "ts": "1792050067.123456", "thread_ts": null}
```

`kind` is `channel:<name>`, `dm`, `group_dm` or `mention`. It never records message text, your own messages
(including a DM to yourself), bots, edits or deletions. The session reads the message itself through the connector.
`my_threads` is left to the timed checks.

## Limits

- The timed checks, the task runs and the watcher live in one Claude Code session: they stop when it exits, and
  Claude Code expires recurring jobs after 7 days (task runs are one-shot jobs, so they do not expire). Run
  `/slack-watch` again in a new session.
- Cron jobs fire only while the session is idle, and Claude Code delays each recurring run by up to half its interval,
  so a 60-minute check can run up to about 30 minutes late. A check that lands outside its window does nothing.
- The Monitor that tails the events file lasts 30 minutes; the session re-arms it each time it expires.
- `my_threads` is covered by the timed checks only: the listener cannot tell which threads you took part in without
  an API call per message.
- The Claude app for Slack does not help here: an @mention starts a cloud session that replies as Claude, and it
  cannot reach a local session.

## Files

| Path | What |
|---|---|
| [`slack-watch/SKILL.md`](slack-watch/SKILL.md) | The skill: setup, start, stop, status, the check, recurring tasks, the watcher, and the reply rules |
| [`slack-watch/slack_watch.py`](slack-watch/slack_watch.py) | `check` validates the config and prints the cron entries and next task runs; `listen` is the Socket Mode listener |

## License

Public domain, under the [Unlicense](UNLICENSE).
