# sprinkler-ai

> **AI-driven Rainbird irrigation scheduler.** Optional camera vision,
> soil-moisture sensing, and a Discord bot that diagnoses lawn photos.

> ⚠️ **Alpha** — single-author, early days. The control loop *will* turn your
> sprinklers on. Run `--dry-run` for a week first. MIT, no warranty.

> **"asmaan saaf hai, do din tak badal nazar nahi aaenge."**
> *the sky is clear — no clouds for the next two days*
> — Sahadev, *Swades* (2004)

A small Python service that runs on any always-on Linux box and replaces your
Rainbird's static schedule with a daily AI-decided plan. Once a morning it asks
Claude how many minutes each zone should run, fed weather, ET, soil-probe data,
camera vision, and the last 14 days of decisions.

## Features

- **Per-zone minutes** based on sun, slope, soil, plant type, and a 5-round
  refinement loop that pushes past the AI's instinct to over-water
- **Cycle-and-soak** for sloped zones (splits long runs into pulses with
  infiltration pauses)
- **Forecast-aware** — skips today if rain >5 mm is likely in 48 h
- **Hardware rain-sensor short-circuit** — if it's wet, exits before any LLM call
- **Time-of-day rule** — refuses to water at 4 pm on cool-season turf (fungal risk)
- **Discord notifications** — rich daily-plan embeds + on-demand `!diagnose`
  bot for lawn photos
- **Optional camera vision** (Nest) — daylight snapshots through Claude Vision
  flag drought, over-watering, broken heads, mow-overdue
- **Optional soil moisture** (Ecowitt WH51) — direct VWC% becomes the primary
  signal for sensored zones
- **Lawn-care recommendations** — mowing, fertilization, pre-emergent timing,
  pest/disease watch, only when the data says now
- **Gemini fallback** — auto-switches if the `claude` CLI fails
- **JSONL history** — every decision logged for the next plan to learn from

## Quickstart

```bash
git clone https://github.com/tejasphatak/sahadev-the-lawnbot.git sprinkler-ai
cd sprinkler-ai && ./install.sh           # venv + deps + interactive config wizard
.venv/bin/sprinkler-ai --dry-run          # test — never touches the controller
```

Once the dry-run plans look right, enable the daily timer:

```bash
mkdir -p ~/.config/systemd/user
cp contrib/sprinkler-ai.{service,timer} ~/.config/systemd/user/
sudo loginctl enable-linger $USER
systemctl --user enable --now sprinkler-ai.timer
```

Logs: `journalctl --user -u sprinkler-ai.service`.

**Requirements:** Python 3.11+, a Linux box on the controller's network, the
[`claude` CLI](https://claude.ai/code) signed in, a Rainbird ESP with LNK Wi-Fi.

## Optional add-ons

| | Setup time | Doc |
|---|---|---|
| Discord webhook (notifications) | 1 min | [docs/discord-setup.md](docs/discord-setup.md) |
| Discord bot (`!diagnose` photos) | 5 min | [docs/discord-setup.md](docs/discord-setup.md) |
| Ecowitt GW1200 + WH51 soil probes | local IP in `config.yaml` | — |
| Nest cameras (vision) | $5 + OAuth dance | [docs/nest-setup.md](docs/nest-setup.md) |

## Configure

Either run `sprinkler-ai-init` for the wizard, or copy and edit by hand:

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

Both files are gitignored. They hold zone descriptions, location, controller
credentials, and any optional service tokens.

## Why "Sahadev"?

The default Discord-bot name is a wink at the young weather-watcher in *Swades*
(2004) — the kid who confidently tells the village elders that the sky will
hold for two more days. A weather-watcher naming a sprinkler bot felt right.
Override in `config.yaml` under `bot.name`.

## Why the `claude` CLI instead of the API?

One LLM call a day on your existing Claude subscription — no API key, no
per-token billing. Auto-falls back to `gemini` if Claude fails.

## Safety

- Run `--dry-run` for ≥1 week before enabling the timer. Compare plans against
  your gut on hot days, rainy days, post-mow days.
- The hardware rain sensor is the safety floor — wet ⇒ exits before any LLM call.
- **No hard minute cap.** The prompt steers toward the minimum, but watch the
  first month's `journalctl` for sanity.
- Don't commit `.env` or `config.yaml` (gitignored).

## Contributing

Issues and PRs welcome. Things that fit: other controller backends (Hunter,
OpenSprinkler, Rachio) as optional modules with Rainbird as the reference; more
soil-sensor backends; warm-season turf prompt translations; better property-
boundary detection in vision.

MIT — see [LICENSE](LICENSE).
