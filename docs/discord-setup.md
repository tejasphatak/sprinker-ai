# Discord setup

sprinkler-ai uses Discord in two independent ways. You can enable either, both,
or neither.

| | What | Direction | Setup |
|---|---|---|---|
| **Webhook** | Daily plan / errors / lawn-care recommendations | Outbound only | 1 minute |
| **Bot** | On-demand `!diagnose` photo replies | Bidirectional | 5 minutes |

## 1. Webhook (notifications only)

The simpler option. Use this if you just want the daily plan posted to a channel.

1. In Discord, open the server → **Server Settings → Integrations → Webhooks → New Webhook**.
2. Pick the channel. Copy the webhook URL.
3. Paste it into `config.yaml` under `notifications.discord_webhook_url`.

Done. Your daily 4 am run will post a rich embed. Treat the URL like a password —
anyone who has it can post to your channel as the webhook.

## 2. Bot (interactive `!diagnose`)

Use this if you want to post a close-up photo of a sketchy patch of lawn and have
the bot reply with a diagnosis.

### 2a. Create the bot application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**.
2. Give it a name (e.g. "Lawn Bot"). Hit Create.
3. **Bot tab** (left sidebar) → scroll to **Privileged Gateway Intents** → enable **MESSAGE CONTENT INTENT** → Save.
4. Same Bot tab → **Reset Token** → copy the token (shown once).
   Paste it into `.env` as `DISCORD_BOT_TOKEN=...`.
5. **OAuth2 → URL Generator** in the left sidebar. Scopes: `bot`. Bot Permissions:
   `Read Messages/View Channels`, `Send Messages`, `Read Message History`,
   `Attach Files`, `Embed Links`. Copy the generated URL, open it in your browser,
   and authorize the bot into your server.

### 2b. (Optional) Restrict to one channel

The bot listens in every channel by default. To pin it to one channel:

1. In Discord client → **User Settings → Advanced → Developer Mode** → toggle on.
2. Right-click the channel → **Copy Channel ID**.
3. Add to `.env`: `DISCORD_DIAGNOSE_CHANNEL_ID=<the id>`.

### 2c. Run the bot

```bash
.venv/bin/sprinkler-ai-bot
```

You should see `logged in as YourBotName#1234` on stderr. Test by posting an image
in your chosen channel with `!diagnose` (or `!check`) at the start of the message —
optionally followed by a caption. The bot replies with findings + actions.

To run it as a background service, copy the systemd unit:

```bash
mkdir -p ~/.config/systemd/user
cp contrib/sprinkler-ai-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now sprinkler-ai-bot.service
```

Check logs with `journalctl --user -u sprinkler-ai-bot.service -f`.

## Troubleshooting

- **Bot logs in but ignores my messages.** You forgot to enable MESSAGE CONTENT
  INTENT on the Bot page. Toggle it, save, restart the bot.
- **"DISCORD_BOT_TOKEN not set" error.** The .env file isn't being loaded.
  Confirm it's in the repo root (next to `pyproject.toml`) and the systemd unit's
  `WorkingDirectory` points there.
- **Pasted token, doesn't work.** You probably pasted the OAuth Client Secret
  instead of the Bot Token. The Client Secret comes from the OAuth2 page; the Bot
  Token comes from the Bot page. They're different.
