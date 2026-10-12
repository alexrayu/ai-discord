# ai-discord

Discord group chat between server members, Claude and GPT. One bot process decides who speaks and posts both AIs through a webhook. See `PLAN.md` for the full spec.

Status: milestone 1 (skeleton). The bot echoes every human message in the allowed channels back through the webhook as "Claude".

## Discord setup

1. Open https://discord.com/developers/applications, create an application, open **Bot**.
2. Reset and copy the bot token.
3. Under **Privileged Gateway Intents**, enable **Message Content Intent**.
4. Under **OAuth2 → URL Generator**, select scope `bot` and these permissions:
   View Channels, Send Messages, Send Messages in Threads, Read Message History, Manage Webhooks.
   Open the generated URL and invite the bot to your server.
5. In Discord, enable **Settings → Advanced → Developer Mode**. Then right-click each channel → Copy Channel ID (or Copy Link: the last number in the URL is the channel ID).

`OWNER_USER_ID` is optional. It names the user allowed to run bot commands; when empty, the server owner is used.

## Install and run

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # then fill in the values
.venv/bin/python bot.py
```

The bot exits at startup with a list of what is missing or invalid in `.env`.

Tests: `.venv/bin/pytest`

## Privacy

- Messages are read from Discord and posted back to Discord. Nothing else leaves the machine in this milestone.
- No message content is logged, also not with `DEBUG=true`.
