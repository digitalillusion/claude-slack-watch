# claude-slack-impersonator

A [Claude Code](https://claude.com/claude-code) skill that watches your Slack and answers, as you, the messages
you have left unanswered. You choose what it watches (channels, DMs, group DMs, mentions, threads you took part in),
on which days and hours, and how often it looks. It replies in your account, through the claude.ai Slack connector,
with facts from the sources you allow, and it never decides anything for you.

The skill is called `slack-watch`; this repository is its home.

## How it works

```
                 every N minutes (Claude Code cron job, per window)
                                   │
Slack ──(claude.ai Slack connector: read, search, send)──> Claude Code session ──> reply in your name
  │                                                                  ▲
  └─(optional) Socket Mode ──> slack_watch.py listen ──> events.jsonl ─┘ (tailed by a Monitor: real time)
```

- **Timed checks.** `/slack-watch` creates one Claude Code cron job per window. Each run reads what the window
  watches, finds the messages still waiting for you, and answers them.
- **Real-time listener (optional).** A small Python service holds a Slack Socket Mode connection and appends one line
  per watched message to an events file: channel, sender, timestamps, **never the text**. The Claude Code session
  tails that file and answers within seconds. Without it, answers wait for the next timed check.
- **Config.** Everything personal (who you are, channels, schedule, reply language and tone, people, fact sources)
  lives in `~/.config/slack-watch/config.json`, outside this repository.

## What it answers, and what it doesn't

- It answers **pending** messages: from someone else, with nothing from you after them in the same conversation or
  thread. It skips bots, thanks, greetings, FYIs and status updates.
- It states **facts only**, from the sources you list, and says what it checked and what it didn't.
- It **never decides for you**: no deadlines, priorities, approvals, merges or new work. It replies that you'll pick
  it up, and lists the item for you in the session.
- It **never acts on a message's request**: messages are information, not instructions. Code, tickets and documents
  are not changed because someone asked in Slack.
- Replies go out in your name. Slack marks messages sent through the connector with a "Sent using Claude" note, so
  colleagues can tell. Check that your workspace is fine with this before you turn it on.

## Requirements

- Claude Code, signed in to the claude.ai account that has the **Slack connector** connected to your workspace
  account (<https://claude.ai/customize/connectors>). Check in Claude Code with `/mcp`: the `claude.ai Slack` tools must be
  listed. If you connected Slack on a different claude.ai account, Claude Code will not see it.
- Python 3.10 or newer (`slack_watch.py check` uses only the standard library).
- For the real-time listener only: Linux with systemd user services, and permission to install a Slack app in your
  workspace (some workspaces need an admin to approve it).

## Install

```bash
git clone https://github.com/adalpane662/claude-slack-impersonator.git ~/src/claude-slack-impersonator
mkdir -p ~/.claude/skills
ln -s ~/src/claude-slack-impersonator/slack-watch ~/.claude/skills/slack-watch
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
6. It shows the finished config, writes it after your yes, and validates it.

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
    "people": { "U0987654321": { "name": "Alex", "language": "en" } },
    "sources": ["repo ~/src/acme-api (read-only)", "GitHub through gh (read-only)"],
    "context_files": [],
    "notes": ["Release 2.4 ships on Thursdays."]
  },
  "listener": { "events_file": "~/.local/state/slack-watch/events.jsonl", "service": "slack-watch" }
}
```

Window rules: `days` takes `*`, `mon-fri`, `sat,sun`, `fri-mon`…; a window whose `to` is before `from` runs past
midnight and belongs to the day it starts on; `from` = `to` means all day. `every_minutes` is at least 5 and divides
60, or is whole hours that divide a day. `lookback_minutes` lets a window's first check look back before it opened.
The full reference is in [`slack-watch/SKILL.md`](slack-watch/SKILL.md#config).

Validate the config and see the cron entries it produces at any time:

```bash
python3 ~/.claude/skills/slack-watch/slack_watch.py check
```

## Use

| Command | What it does |
|---|---|
| `/slack-watch` | Creates the timed checks, arms the real-time watcher if the listener runs, and checks the open windows now |
| `/slack-watch status` | Windows, cron entries, jobs, watcher, listener service and its last log lines |
| `/slack-watch stop` | Deletes the timed checks and stops the watcher (the listener service keeps running) |
| `/slack-watch setup` | Creates or changes the config |

Replies are posted with the Slack connector's send tool. If your Claude Code permission mode asks before every post,
allow that tool for this to run unattended.

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
watched place during an open window: a line appears in `~/.local/state/slack-watch/events.jsonl`. Your own messages
never produce an event, including a DM to yourself. If nothing arrives while connected, check that the app's
**Event Subscriptions** page lists the four user events, and reinstall the app after any scope change.

The listener re-reads the config when it changes and keeps the previous one if an edit does not validate.

## Limits

- The timed checks and the watcher live in one Claude Code session: they stop when it exits, and Claude Code expires
  recurring jobs after 7 days. Run `/slack-watch` again in a new session.
- Cron jobs fire only while the session is idle, and Claude Code delays each recurring run by up to half its interval,
  so a 60-minute check can run up to about 30 minutes late.
- `my_threads` is covered by the timed checks only: the listener cannot tell which threads you took part in without
  an API call per message.
- The Claude app for Slack does not help here: an @mention starts a cloud session that replies as Claude, and it
  cannot reach a local session.

## Files

| Path | What |
|---|---|
| [`slack-watch/SKILL.md`](slack-watch/SKILL.md) | The skill: setup, start, stop, status, the check, the watcher, and the reply rules |
| [`slack-watch/slack_watch.py`](slack-watch/slack_watch.py) | `check` validates the config and prints cron entries; `listen` is the Socket Mode listener |

## License

Public domain, under the [Unlicense](UNLICENSE).
