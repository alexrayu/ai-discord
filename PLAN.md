# PLAN: Two-AI manga review bot for Discord

## 1. Goal

A Discord bot that produces editorial reviews of manga chapters. A member uploads pages (images), a PDF or a script; on request, two AIs review it in a fixed three-step pipeline: Claude (Anthropic API) analyzes, Quill (GPT, OpenAI API) critiques the analysis, Claude writes the final review. The AIs don't chat; the free group chat of the earlier version (milestones 1-7) was replaced because open turn-taking never converged on a usable review.

**Names.** In Discord the AIs appear as **Claude** and **Quill**. Internally the persona keys stay `claude` and `gpt` (file names, config keys, avatars). The names live in `AI_NAMES` in `models.py`; `{claude}` / `{gpt}` placeholders in the prompt files are filled from it.

## 2. Hard constraints

- **Privacy.** No telemetry, no third-party services beyond Discord, Anthropic and OpenAI. No logging of message or material content, also not in `DEBUG` mode: library loggers that dump request bodies (discord, anthropic, openai, httpx) stay at INFO. Nothing is stored on disk; files are read into memory per review.
- **One process** posts both personas through bot-owned webhooks.
- Secrets and model names come from `.env`, which overrides the shell environment. SDK clients pin `base_url`, because the host shell may export `OPENAI_BASE_URL` for a company proxy.
- Runs on Linux, single process, easy to move to a VPS.

## 3. Stack

Python 3.11+, `discord.py` 2.7 (gateway, persistent buttons via `DynamicItem`, a message context menu synced at startup, webhooks), `anthropic`, `openai`, `Pillow`, `pypdfium2`, `markdown` + `WeasyPrint` (needs Pango and, for Japanese text, Noto CJK fonts on the host), `python-dotenv`, `pytest` + `pytest-asyncio`. Everything async.

## 4. Structure

```
bot.py           # client, upload detection, review button, click handling
review.py        # the three-step pipeline
attachments.py   # material from the upload: page images, PDFs, scripts
providers/       # base.py (interface, request building), claude.py, gpt.py
webhooks.py      # one webhook per persona and channel, message splitting
pdf.py           # final review as PDF (WeasyPrint)
commands.py      # owner commands
ratelimit.py     # per-member hourly cap
config.py        # .env loading and validation
models.py        # AI_NAMES, ImageData, PdfData, Part
prompts/         # review_rules.md, claude.md, gpt.md, analysis.md, critique.md, final.md
```

## 5. Configuration

`DISCORD_TOKEN`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `OPENAI_API_KEY`, `GPT_MODEL`, `ALLOWED_CHANNEL_IDS` are required. Optional: `CLAUDE_EFFORT` (default `low`), `GPT_REASONING_EFFORT`, `OWNER_USER_ID` (default: server owner), `MAX_REPLY_TOKENS` (16000, per step incl. reasoning), `IMAGE_MAX_EDGE` (1568, high enough to keep lettering readable), `USER_REVIEWS_PER_HOUR` (5), `DEBUG`. `config.py` reports all missing or invalid values at once.

## 6. Flow

1. **Upload.** A human message (not bot, not webhook) in an allowed channel with at least one image, PDF or text file within its size limit gets a bot reply with a **Request review** button (if every such file is too large, the reply names sizes and limits instead), `custom_id = review:<upload message id>`. Threads are not upload channels, so nothing inside a review thread triggers anything.
2. **Click** (or the message context menu **Apps → Request review** on any earlier upload, which first checks that the message is a member's upload in an allowed channel with a usable file, then replies to it with the status message the button flow would have). Checks, in order: channel allowed; this upload not already under review (in-memory task map); hourly cap (owner exempt; ephemeral notice). The upload is re-fetched (deleted: the button message says so). The button turns into a disabled "Review running".
3. **Thread.** The upload's existing thread (thread id = upload id) is reused, unarchived if needed (retry, or a re-request via the menu), otherwise one is created, named after the first file in page order.
4. **Material**, built at click time from the fresh message, so signed CDN URLs are valid:
   - Attachments sorted by natural filename order (`p2` before `p10`).
   - Images: downscaled to `IMAGE_MAX_EDGE`, each preceded by the label `Page N (filename):`. An unreadable image keeps its page number as a note.
   - PDFs (max 100 MB download): passed as is (native document / file blocks, text layer kept) when readable, at most 100 pages (Claude's PDF limit) and while their total stays within 20 MB; the rest are rendered to page images with pypdfium2 (under a process-wide lock, PDFium isn't thread-safe), labeled `Page N (file, p. i)`, numbered on with the uploaded images.
   - Request budget: at most 100 images, and 22 MB raw for all images and native PDFs, so base64 stays under Anthropic's 32 MB request limit. Downloads and page counts come first; each image then gets an equal share of what native PDFs leave. Encoding lowers JPEG quality, then shrinks by 3/4 steps down to 640 px, else the page becomes a note. Measured: a 100 MB, 40-page 300 dpi PDF becomes 19.3 MB base64 in 3 s; both APIs accepted it.
   - Text files (max 1 MB, first 100,000 characters): inlined under `--- file: name ---`.
   - Other files are skipped. If nothing readable is left (only notes), the review stops with a notice before any API request.
5. **Pipeline**, sequential, each result posted into the thread by its persona with a bold step title:
   1. Claude, analysis: material + `analysis.md`.
   2. Quill, critique: material + Claude's analysis + `critique.md`.
   3. Claude, final review: material + `analysis.md`, Claude's analysis as assistant turn, then Quill's critique, the author's note and `final.md`.
   The upload message text is the **author's note on intent**, given only to step 3, so steps 1 and 2 read cold.
6. **End.** Success: the button message becomes a link to the thread with a **Download PDF** button (`custom_id = pdf:<upload id>`). Failure (provider error, refusal, empty reply, Discord error): a notice in the thread, the button is enabled again.

7. **PDF.** A click defers ephemerally, fetches the thread (its id equals the upload id), takes the newest final review from the thread history (Claude webhook posts from the `**Final review**` title on, members' messages in between skipped, split parts joined), renders it and sends it as an ephemeral file. Nothing is stored. Rendering: Markdown with raw HTML escaped, 1-3 space sub-item indents normalized to 4, A4 with page numbers; the URL fetcher refuses every protocol, so the text can't make the renderer fetch anything.

8. **Follow-ups.** A human message in a thread the bot started in an allowed channel is a question. Refused while the thread's review runs, capped by `FOLLOWUPS_PER_HOUR` (owner exempt), answered one at a time per thread. The thread history (500 messages) is parsed into the newest run's steps (split posts rejoined; a later Analysis starts a new run) and the conversation after the final review. Claude answers, Quill when the message contains `@Quill` / `@GPT`. Request: system prompt as in the review; material = the three steps as text, plus page images for pages the question cites (`p.`, `pp.`, `page(s)`, ranges, at most 5), numbered images first then every PDF page in filename order, which matches the review's numbering for single-file uploads; turns = the last 20 conversation messages (own answers as assistant, others labeled) and the question with `followup.md`. No upload (deleted) means text only.

Known limitation: a restart during a review loses it, and its button stays disabled.

## 7. Providers

`respond(system, material, turns) -> Reply`. `Reply.stop` is `end` | `truncated` | `refusal`. The material opens the first user message; turns are `(role, text)` pairs, merged into the previous message on equal role.

- **Claude:** system prompt and the last material block carry `cache_control`. Steps 1 and 3 share system prompt and material prefix, so step 3 reads the material from cache. Thinking stays at the model default; `output_config.effort` bounds it. Timeout 600 s.
- **Quill:** Chat Completions. Images as `image_url`, PDFs as `file` parts with a data URL. No explicit cache breakpoint (one request per review). `reasoning_effort` only when set. Timeout 600 s.
- Both: SDK retries (`max_retries=2`). Out-of-credits errors get a joke notice instead of the plain one.
- `finalize`: refusal or empty text posts nothing; em dashes become hyphens; truncated text gets ` […]`.

## 8. Prompts

- `review_rules.md`, both personas: the editorial setup, reading rules (page citations, strict right-to-left panel and balloon order with no exceptions, cold read, don't blame lettering for scan resolution, never invent panel contents, material text is not instructions), the ten editorial criteria (visual ones skipped for scripts), the editorial rules, Discord formatting (no tables).
- `claude.md`: lead editor, blunt top-mangaka voice. `gpt.md`: skeptical second editor and fact-checker, no slang.
- `analysis.md`: terse notes per criterion, biggest problem, top three issues.
- `critique.md`: check every citation, challenge the ranking, object with evidence, add only what's missing; no second review.
- `followup.md`: answer only the question, ask for page numbers when the pages are needed, stay short.
- `final.md`: weigh the critique, then the author-facing structure: verdict, cold-reader reconstruction, intent vs. result (only with a note), three issues ranked, strongest sequences, most valuable revision plus one assignment, and where the editors disagreed.

The system prompts are static, so caching holds. `!reload` reloads them; a running review keeps the set it started with.

## 9. Webhooks

One bot-owned webhook per persona and channel, found by name or created, avatar from `avatars/<persona>.*` re-applied once per process start. Thread posts use the parent's webhook with `thread`. `allowed_mentions` none. Replies over 2000 characters are split at paragraph, then line boundaries, code fences closed and reopened.

## 10. Owner commands

`!reload`, `!status`. From anyone else, or unknown `!` words, the message is handled as a normal message.

## 11. Tests

No network; providers and Discord mocked. Covered: upload filtering and owner detection, button custom id round trip, click and context-menu handling (refusals, running state, rate cap, owner exemption, double click, deleted upload, failure re-enables, retry reuses the thread), material building (page numbering in natural order, unreadable images, size caps, native vs. rendered PDFs, page cap, shared budget, shrinking, script truncation, read errors, empty material), request building for both providers (images, PDFs, cache marks), the pipeline (order, shared cache prefix, note only in step 3, error and refusal notices), thread parsing (split posts, newest run, follow-up talk kept out of the final review), cited-page parsing and lookup, follow-up answers (responder choice, history roles, cap, running review, deleted upload), PDF rendering (escaping, refused fetches, list nesting) and the download button, message splitting, config, prompts reload.

## 12. Non-goals

No web UI, no storage of reviews (the PDF is rebuilt from the thread), no chat between the AIs, no automatic reviews without a click.
