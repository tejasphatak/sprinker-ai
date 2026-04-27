# sprinkler-ai

> **AI-driven Rainbird irrigation scheduler** with optional camera vision,
> soil-moisture sensing, and a Discord bot that diagnoses lawn photos.

> ⚠️  **Alpha software.** Early-stage, single-author project. The control loop
> *will* turn your sprinklers on. Run `--dry-run` for a week before trusting
> automation. No warranty — see [LICENSE](LICENSE). Bugs and rough edges expected;
> please file issues.

> **"asmaan saaf hai, do din tak badal nazar nahi aaenge."**
> *the sky is clear — no clouds for the next two days*
> — Sahadev, *Swades* (2004)

## What it does

A small Python service that runs on any always-on Linux box on your home network
and replaces the schedule programmed into your Rainbird controller with one that
adapts to today. Once a day (4 am by default) it asks Claude how many minutes
each zone should run, given:

- **Weather** — past 3 days of actual rainfall + 5-day forecast + ET₀
  (evapotranspiration) from Open-Meteo (free, no key)
- **Your zones** — sun/shade, slope, soil, plant type, precipitation rate
- **The forecast** — if rain is coming in 48 h, prefer to skip
- **Your history** — last 14 days of decisions, fed back so it doesn't repeat mistakes
- **The hardware rain sensor** — trusted before any LLM call (skip + exit if wet)
- **Soil moisture (optional)** — direct VWC% from Ecowitt WH51 probes per zone
- **Camera vision (optional)** — daylight Nest snapshots through Claude Vision

## Capabilities

### 🌧️  Smart daily plan
- **Per-zone minutes**, not one-size-fits-all. Shaded zones get less, full-sun zones
  get more, mulched beds get short pulses.
- **5-round refinement loop** baked into the prompt — pushes the AI past its instinct
  to over-water. Most days the answer is much smaller than common "1 inch a week" advice.
- **Cycle-and-soak** for sloped zones — splits a 15-minute run into 3×5-minute pulses
  with infiltration pauses so water doesn't sheet off into the street.
- **Time-of-day aware** — won't water at 4 pm on cool-season turf (fungal risk),
  prefers 4–8 am.
- **Hard rain-sensor short-circuit** — if the controller's wired rain sensor is wet,
  the script skips entirely without even calling the AI.
- **Forecast discounting** — subtracts expected next-5-day rainfall from each zone's
  need before deciding.

### 🌱  Lawn-care recommendations
The same daily run flags non-watering tasks when conditions actively warrant them.
Not boilerplate — only when the data says now:
- **Mowing height** adjustments for incoming heat or fall transition
- **Fertilization windows** keyed to grass type and soil temperature
- **Pre-emergent crabgrass** when the 5-day soil-temp average crosses 50–55 °F
- **Aeration + overseeding** in the right cool-season / warm-season window
- **Pest watch** — grub damage in late summer, armyworms after storms, chinch bugs
  in heat
- **Disease watch** — brown patch on humid summer nights, large patch on Zoysia in
  cool wet falls, etc., adapted to your declared grass type
- **Sprinkler maintenance cues** from camera vision (visible dry stripes, stuck heads)

### 📷  Optional camera vision
If you have Nest cameras, a separate mid-morning timer grabs one frame per camera
through Claude Vision and looks for **drought stress, over-watering, broken
sprinklers, mow-overdue, and weed patches**. Findings feed into the next morning's
plan. Includes property-boundary awareness — the model is told which lawn in the
frame is *yours* so it doesn't flag the neighbor's bald patch.

### 🌡️  Optional soil-moisture sensing
Plug an Ecowitt GW1200 + a WH51 probe into one or more zones. Direct VWC%
readings become the primary signal for those zones (overriding ET math), and
nearby unsensored zones are inferred by similarity.

### 💬  Discord notifications + diagnosis bot
- **Webhook** posts a rich daily-plan embed (per-zone fields, soil readings,
  recommendations) to your channel.
- **Bot** listens for `!diagnose` posts with an attached photo and replies with
  findings + action items — drought, fungus, weeds, pests, drainage. Adapts to
  your declared grass type and climate.

### 🛡️  Safety + observability
- `--dry-run` prints the plan without ever calling the controller's irrigate command.
- Every decision (water / skip / dry-run / error) is appended to `data/history.jsonl`
  with reason, weather snapshot, and per-zone minutes.
- Two LLM providers — auto-falls back from `claude` CLI to `gemini` CLI on failure,
  so a Claude outage doesn't leave your lawn dry.

## Architecture

```
sprinkler-ai/
├── src/sprinkler_ai/
│   ├── cli.py            # daily 4 am entrypoint (orchestrates everything)
│   ├── weather.py        # Open-Meteo client (free, no auth)
│   ├── rainbird_client.py# pyrainbird async wrapper
│   ├── ecowitt.py        # WH51 soil-probe local API
│   ├── nest.py           # OAuth + WebRTC frame grab + Claude vision
│   ├── planner.py        # builds Claude prompt, parses JSON plan
│   ├── notify.py         # Discord webhook (rich embeds)
│   ├── discord_bot.py    # !diagnose photo bot (long-running)
│   ├── diagnose.py       # single-photo Claude vision turf diagnostician
│   ├── history.py        # 14-day rolling JSONL log
│   ├── config.py         # config.yaml + .env loader
│   └── init.py           # interactive setup wizard
├── contrib/              # systemd unit templates (user-mode)
├── docs/
│   ├── discord-setup.md  # webhook + bot walkthroughs
│   └── nest-setup.md     # Google Device Access OAuth dance
├── config.example.yaml   # template — copy or run sprinkler-ai-init
├── .env.example
└── install.sh            # bootstrap: venv + deps + interactive config
```

## Quickstart

### Requirements

- A Linux box on the same network as your Rainbird ESP-series controller
- Python 3.11 or newer
- The `claude` CLI installed and signed in: <https://claude.ai/code>
  - Optional: `gemini` CLI for fallback
- A Rainbird controller with the LNK Wi-Fi adapter (tested with 6.x firmware)

### Install

```bash
git clone https://github.com/tejasphatak/sahadev-the-lawnbot.git sprinkler-ai
cd sprinkler-ai
./install.sh
```

`install.sh` creates a venv, installs the package, and launches the interactive
setup wizard (`sprinkler-ai-init`) which writes `config.yaml` and `.env` from
your answers. Both files are gitignored.

If you'd rather edit by hand:
```bash
cp config.example.yaml config.yaml
cp .env.example .env
$EDITOR config.yaml .env
```

### Test before automating

```bash
.venv/bin/sprinkler-ai --dry-run
```

This fetches weather, queries the AI, prints the plan, posts it to Discord (if
configured), logs a dry-run entry — and never calls the controller's irrigate
command. **Run this daily for a week before trusting the automation.** Watch for
plans that match your gut on rain days vs. dry days.

### Enable the daily timer

```bash
mkdir -p ~/.config/systemd/user
cp contrib/sprinkler-ai.service ~/.config/systemd/user/
cp contrib/sprinkler-ai.timer   ~/.config/systemd/user/
sudo loginctl enable-linger $USER     # so the timer fires when you're not logged in
systemctl --user daemon-reload
systemctl --user enable --now sprinkler-ai.timer
systemctl --user list-timers sprinkler-ai.timer
```

Logs to journald: `journalctl --user -u sprinkler-ai.service`.

### Optional add-ons

- **Discord** — see [docs/discord-setup.md](docs/discord-setup.md). Webhook ≈ 1 minute,
  bot ≈ 5 minutes.
- **Nest cameras** — see [docs/nest-setup.md](docs/nest-setup.md). Costs $5 for the
  Google Device Access project; somewhat fiddly OAuth dance once.
- **Ecowitt soil sensors** — buy a GW1200 + as many WH51 probes as you want zones.
  Pair them in WSView Plus, find the gateway's local IP, set `ecowitt_host` in
  `config.yaml`. Done.

## Why the `claude` CLI instead of the API?

The planner shells out to your local `claude -p` and uses your existing Claude
subscription — no separate API key, no per-token billing, no secret to rotate.
For one LLM call a day, this is both cheaper and simpler than the SDK.

If `claude` fails (rate limit, transient outage), it auto-falls back to `gemini`
using your authenticated Google account. The plan still ships.

The tradeoff: it must run on a machine where `claude` is installed and signed in.
On a small always-on Linux box (Pi, NUC, old laptop) that's fine.

## Why "Sahadev"?

It's the default name for the Discord bot persona. Sahadev is a young
weather-watcher in the 2004 Indian film *Swades* — a kid in the village
panchayat known for predicting the sky. The line you might remember is his
confident reassurance to the village elders that the next two days will be
clear and cloudless. A weather-watcher who tells your sprinkler whether to skip
a day felt like the right namesake.

Override it in `config.yaml` under `bot.name` — call yours whatever you like.

## Safety + disclaimers

This is alpha software talking to a physical actuator that consumes water.

- Always run `--dry-run` for at least a week before enabling the timer. Compare the
  AI's plan against your judgment on hot days, rainy days, post-mow days.
- The Rainbird hardware rain sensor is trusted unconditionally — if it's wet, the
  script exits before calling Claude. That's the safety floor.
- There's **no hard cap** on minutes-per-day. The prompt instructs Claude toward
  the minimum, but a buggy prompt or model regression could in principle propose
  a long run. Keep an eye on `journalctl --user -u sprinkler-ai.service` for the
  first month.
- Don't commit `.env` or `config.yaml`. Both are gitignored. Both contain secrets
  or near-PII (your zone descriptions, slope, neighbor shadows, etc.).
- Software is provided as-is under MIT — no warranty for soaked basements, dry
  lawns, or angry HOAs. See [LICENSE](LICENSE).

## Contributing

Issues and PRs welcome. The project is intentionally small and single-purpose —
it isn't trying to become Home Assistant. Things that fit:
- Other irrigation-controller backends (Hunter Hydrawise, OpenSprinkler, Rachio)
  *as additional optional modules — Rainbird stays the reference*
- More soil-sensor backends
- Better property-boundary detection in the vision module
- Translation of the planner prompt for warm-season turf regions
