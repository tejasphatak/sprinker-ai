"""Discord bot — listens for `!diagnose` photo posts and replies with lawn analysis.

Usage: post an image in the configured channel with `!diagnose` (or `!check`) in the
message text. The bot downloads the image, runs Claude vision, and replies in-thread
with findings + recommended actions.

Runs as a long-lived process. See contrib/sprinkler-ai-discord-bot.service for the
systemd unit.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

import discord

from .config import Config
from .diagnose import diagnose_photo


TRIGGER_PREFIXES = ("!diagnose", "!check")

_SEVERITY_DOT = {"info": "🔵", "watch": "🟡", "act": "🔴"}
_PRIORITY_DOT = {"high": "🔴", "medium": "🟡", "low": "🔵"}
_CONDITION_COLOR = {
    "healthy":       0x2ECC71,
    "minor_issues":  0x3498DB,
    "stressed":      0xF39C12,
    "severe":        0xE74C3C,
    "unknown":       0x95A5A6,
}


def _strip_trigger(content: str) -> tuple[bool, str]:
    text = content.strip()
    low = text.lower()
    for p in TRIGGER_PREFIXES:
        if low.startswith(p):
            return True, text[len(p):].strip()
    return False, ""


def _build_embed(result: dict, image_url: str | None = None) -> discord.Embed:
    if not result:
        return discord.Embed(
            title="🌱  Lawn Diagnosis",
            description="Sorry, I couldn't analyze that photo. (Check bot logs for details.)",
            color=0x95A5A6,
            timestamp=datetime.now(timezone.utc),
        )

    if not result.get("view_usable", True):
        return discord.Embed(
            title="🌱  Lawn Diagnosis",
            description=f"Couldn't read this photo — {result.get('view_block_reason', 'unclear image')}.",
            color=0x95A5A6,
            timestamp=datetime.now(timezone.utc),
        )

    condition = result.get("grass_condition", "unknown")
    color = _CONDITION_COLOR.get(condition, 0x3498DB)
    headline = result.get("headline", "").strip() or "Here's what I see."

    embed = discord.Embed(
        title=f"🌱  Lawn Diagnosis · {condition.replace('_', ' ').title()}",
        description=headline,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if image_url:
        embed.set_thumbnail(url=image_url)

    findings = result.get("findings") or []
    if findings:
        lines = []
        for f in findings[:4]:
            dot = _SEVERITY_DOT.get(f.get("severity", "info"), "•")
            issue = f.get("issue", "").strip() or "issue"
            evidence = f.get("evidence", "").strip()
            line = f"{dot} **{issue}**"
            if evidence:
                line += f" — {evidence}"
            lines.append(line[:500])
        embed.add_field(name="What I see", value="\n".join(lines)[:1024], inline=False)

    actions = result.get("actions") or []
    if actions:
        lines = []
        for a in actions[:4]:
            dot = _PRIORITY_DOT.get(a.get("priority", "medium"), "•")
            action = a.get("action", "").strip() or "act"
            when = a.get("when", "").strip()
            line = f"{dot} {action}"
            if when:
                line += f"  *({when})*"
            lines.append(line[:500])
        embed.add_field(name="What to do", value="\n".join(lines)[:1024], inline=False)

    uncertainty = (result.get("uncertainty") or "").strip()
    if uncertainty:
        embed.set_footer(text=f"Caveats: {uncertainty[:200]}")

    return embed


class LawnBot(discord.Client):
    def __init__(self, config: Config, allowed_channel_id: int | None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.allowed_channel_id = allowed_channel_id

    async def on_ready(self):
        name = self.config.bot.name or (self.user.name if self.user else "bot")
        print(f"[{name}] logged in as {self.user} (id={self.user.id})", file=sys.stderr)
        if self.allowed_channel_id:
            print(f"[{name}] restricted to channel_id={self.allowed_channel_id}", file=sys.stderr)
        else:
            print(f"[{name}] no channel restriction — listening everywhere", file=sys.stderr)

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.author == self.user:
            return
        if self.allowed_channel_id and message.channel.id != self.allowed_channel_id:
            return

        triggered, caption = _strip_trigger(message.content or "")
        if not triggered:
            return

        image_attachments = [
            a for a in message.attachments
            if (a.content_type or "").startswith("image/")
            or a.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".heic"))
        ]
        if not image_attachments:
            await message.reply(
                "🌱 I need an image attached to the message to diagnose. "
                "Try again with a close-up photo (1–2 ft from the grass).",
                mention_author=False,
            )
            return

        att = image_attachments[0]
        try:
            async with message.channel.typing():
                jpeg_bytes = await att.read()
                site_profile = {
                    "region": self.config.location.region,
                    "climate": self.config.location.climate,
                    "hardiness_zone": self.config.location.hardiness_zone,
                    "primary_grass_type": self.config.grass_type,
                    "default_soil_type": self.config.soil_type_default,
                }
                result = await asyncio.to_thread(
                    diagnose_photo,
                    self.config.claude_bin,
                    jpeg_bytes,
                    caption,
                    self.config.model,
                    self.config.gemini_bin,
                    self.config.gemini_model,
                    site_profile,
                )
            embed = _build_embed(result, image_url=att.url)
            await message.reply(embed=embed, mention_author=False)
        except Exception as e:
            print(f"[{self.config.bot.name}] error handling {message.id}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            try:
                await message.reply(
                    f"🌱 Something broke while analyzing that photo (`{type(e).__name__}`). "
                    "Check the bot logs.",
                    mention_author=False,
                )
            except Exception:
                pass


def main() -> int:
    config = Config.load()  # loads .env as a side effect
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not set in .env", file=sys.stderr)
        return 1

    channel_id_raw = os.environ.get("DISCORD_DIAGNOSE_CHANNEL_ID", "").strip()
    channel_id = int(channel_id_raw) if channel_id_raw.isdigit() else None

    bot = LawnBot(config, allowed_channel_id=channel_id)
    bot.run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
