# ai-discord

Discord group chat between server members, Claude (Anthropic) and Quill (GPT, OpenAI). One bot process decides who speaks and posts both AIs through a webhook. See `PLAN.md` for the full spec.

Status: milestone 3. Claude and Quill reply in a fixed order (Claude first); self-organized turns follow in milestone 4.

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

- Messages in the allowed channels, from every member, are sent to Anthropic and OpenAI to generate replies. Both keep API data for a limited time for abuse monitoring and does not train on it by default.
- Transcripts are stored locally in `transcripts.sqlite`.
- No message content is logged, also not with `DEBUG=true`.
- Each member except the owner can start `USER_ROUNDS_PER_HOUR` replies per hour.

## Avatars

Put `claude.png` and `gpt.png` (Quill) into `avatars/` (jpg, webp and gif work too), square, at least 128×128. They are uploaded to the persona webhooks when the bot starts; after replacing a file, restart the bot. Without a file, the persona gets Discord's default icon.
