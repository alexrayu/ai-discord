# ai-discord

Discord bot for editorial reviews of manga chapters by two AIs: Claude (Anthropic) and Quill (GPT, OpenAI). See `PLAN.md` for the full spec.

A member posts pages (images), a PDF or a script file in a review channel; the bot answers with a **Request review** button. A click opens a thread on the upload, and the AIs post three steps there through webhooks: Claude's analysis, Quill's critique of it (checking every page citation), and Claude's final review. The text of the upload message, if any, counts as the author's note on intent; only the final step sees it, so the first two read cold. Images are numbered as pages in filename order (`p2` before `p10`). The AIs don't chat with each other: other messages in the channel are ignored, and in a review thread each member message gets one follow-up answer (see Reviews).

## Discord setup

1. Open https://discord.com/developers/applications, create an application, open **Bot**.
2. Reset and copy the bot token.
3. Under **Privileged Gateway Intents**, enable **Message Content Intent**.
4. Under **OAuth2 → URL Generator**, select scope `bot` and these permissions:
   View Channels, Send Messages, Send Messages in Threads, Create Public Threads, Read Message History, Manage Webhooks.
   Open the generated URL and invite the bot to your server.
5. In Discord, enable **Settings → Advanced → Developer Mode**. Then right-click each channel → Copy Channel ID (or Copy Link: the last number in the URL is the channel ID).

`OWNER_USER_ID` is optional. It names the user allowed to run bot commands; when empty, the server owner is used.

## Install and run

```
# PDF rendering (WeasyPrint) needs Pango; on Debian/Ubuntu: apt install libpango-1.0-0 libpangoft2-1.0-0 fonts-noto-cjk
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # then fill in the values
.venv/bin/python bot.py
```

The bot exits at startup with a list of what is missing or invalid in `.env`.

Tests: `.venv/bin/pytest`

## Owner commands

Only the owner (`OWNER_USER_ID`, or the server owner when empty) can run these.

- `!reload`: reload the prompt files without a restart. A running review keeps the prompts it started with.
- `!status`: models, efforts and the hourly review limit.

## Reviews

- Each member except the owner can request `USER_REVIEWS_PER_HOUR` reviews per hour.
- Input: up to 10 images per message (Discord's limit), PDFs up to 100 MB, text files up to 1 MB (first 100,000 characters). Other files are skipped; an upload where every file is over its limit gets the reason instead of a button.
- PDFs up to 20 MB in total go to the models as PDFs. Larger ones are rendered to page images in memory, like uploaded pages. PDFs over 100 pages are always rendered (Claude takes at most 100 PDF pages). A review takes at most 100 pages; image quality and size step down so a request stays under the API size limit.
- For an earlier upload (posted while the bot was offline, a lost button, or a second opinion): right-click the message → **Apps** → **Request review**. Same checks and limit; another run on the same upload posts into its existing thread, and the PDF takes the newest final review. If the menu entry is missing, re-invite the bot with the `applications.commands` scope as well.
- When the review is done, the button message links to the thread and offers **Download PDF**: the final review as an A4 PDF, sent only to whoever clicks. It is rebuilt from the thread on every click, so it works after restarts; deleting the thread makes it unavailable.
- Follow-up questions: after the review, any member's message in the review thread gets an answer from Claude, or from Quill when it contains `@Quill`. The AI sees the three review steps and the thread's earlier questions and answers as text; pages the question names (`p. 4`, `pp. 3-5`, `page 12`, at most 5) are attached as images. Every member message there counts as a question, so members discussing among themselves trigger answers too. Cap: `FOLLOWUPS_PER_HOUR` per member (default 30), owner exempt. Cost: about 1 cent per text-only question, plus about 1 cent per cited page.
- If a step fails, the thread shows a notice and the button comes back; a retry posts into the same thread.
- If the bot restarts during a review, that review is lost and its button stays disabled; post the files again.
- Cost: every page goes to both models, and Claude reads the material twice (the second time mostly from cache). A long PDF can mean 100k+ input tokens per review.
- The review prompts live in `prompts/`: `review_rules.md` (shared criteria and rules), `claude.md` / `gpt.md` (personas), `analysis.md`, `critique.md`, `final.md` (one per step).

## Privacy

- Uploads in the review channels, from every member who presses the button, are sent to Anthropic and OpenAI, together with the upload message text. Both keep API data for a limited time for abuse monitoring and don't train on it by default.
- Nothing is stored locally: files are downloaded into memory per review and dropped afterwards. Review PDFs are rendered in memory without fetching any URL the review text may contain.
- No message content is logged, also not with `DEBUG=true`.
- Text inside the uploaded material is untrusted input for the AIs.

## Avatars

Put `claude.png` and `gpt.png` (Quill) into `avatars/` (jpg, webp and gif work too), square, at least 128×128. They are uploaded to the persona webhooks when the bot starts; after replacing a file, restart the bot. Without a file, the persona gets Discord's default icon.
