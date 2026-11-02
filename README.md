# metaai-api

Unofficial Python SDK and API server for Meta AI — generate images, chat with Llama, and manage conversations. No API key required, just your browser cookies.

> ⚠️ Disclaimer: This is an unofficial, reverse-engineered client. It is not affiliated with, endorsed by, or sponsored by Meta. Use at your own risk and in compliance with Meta's Terms of Service. Cookie-based authentication may break at any time as Meta updates their platform.

> **Personal copy notice:** This repository is an unofficial personal copy based on the original [mir-ashiq/metaai-api](https://github.com/mir-ashiq/metaai-api), maintained separately from the upstream project. The upstream repository remains the original source of the project and its releases. This copy retains the upstream MIT license and copyright notices, and is not affiliated with, endorsed by, or sponsored by Meta.
>
> This working tree includes local SDK and REST API changes in addition to the upstream-derived code; see the generation API notes for current limitations.

> **🔄 Looking for Meta's former AI video creation features?**
>
> Meta's AI video creation experience has moved to **Vibes.ai**. For Vibes.ai video generation, image generation, TTS, lip-sync, timeline editing, media management, and other features, see **[VibesAI-api](https://github.com/mir-ashiq/VibesAI-api)**.
>
> **→ [VibesAI-api — Unofficial Python API for Vibes.ai](https://github.com/mir-ashiq/VibesAI-api)**

## What's New in v3.0.0

Meta AI uses **DGW (Datagram WebSocket)** for chat and image generation; the older send-message GraphQL methods no longer work because Meta changed that schema (including removing `RewriteOptionsInput`, `AttachmentInput`, `MentionInput`, and `sendMessageStream`). Media lookup is separate and currently uses Meta's `MediaLibraryFeed` GraphQL query.

This version uses **browser automation** as the primary method for chat and image generation. It prefers **Playwright** (pure Python; no Node.js required) when installed and falls back to `agent-browser`. The browser creates valid DGW messages natively; see the verification notes below for what has and has not been tested against a signed-in account.

### Breaking Changes from v2.x
- `prompt()` now uses browser automation (no more OAuth token extraction)
- `generate_image_new()` uses browser automation
- Text-to-video remains an **experimental** legacy GraphQL workflow; it currently returns 503 for the account used in live testing
- `upload_image()` uses Meta's rupload endpoint and requires `META_AI_ACCESS_TOKEN`
- Cookie-based auth replaces access token for primary methods

### Current verification status


- **Text chat (`/chat`, `ai.prompt`)** — Cold-start results remain mixed. After adding bounded UI-readiness waits, an initial cohort had HTTP 500 on all three first requests (`ConnectionError`: Meta's Send button stayed disabled); same-instance retries returned 500 twice, and one retry returned 200 but the test's local answer checker then failed. A later cohort of three independent server/browser starts returned HTTP 200 with correct random-arithmetic answers on all three first requests. This inconsistency means cold-start reliability is not yet proven.
- **Text-to-image (`/image`, `generate_image_new`)** — Live E2E tested.
- **Reference-image generation / image-to-image (`/image/reference` and reference inputs to `/image`)** — Live E2E tested, including `/upload` → returned `media_id` → `/image`. The generated image retained the synthetic reference's distinctive shapes and colors. Reuse only works for images uploaded through this API while their local image cache is present (24 hours).
- **Generic file upload (`POST /files`)** — A synthetic PDF upload to Meta was Live E2E tested. The API returns Meta's media ID and upload metadata, but does not retain a local copy or provide a download route. Other file types are not thereby verified.
- **Generated image download (`GET /media/{card_id}/download`)** — Live E2E tested for an image from Meta's media library. The route also has Mock tests; video download has not been live-verified here.
- **Generated document export (`/conversations/{id}/documents...`)** — Live E2E tested for PDF, DOCX and TXT artifacts. Downloads preserve Meta's original PDF/DOCX bytes or reconstruct the exact UTF-8 text stored in its TXT viewer payload.
- **Video generation** — Live-tested 2026-11-02; Meta declined for this account, so `/video` returns 503. Image-to-video and video extension are not implemented.
- **Image-only upload (`POST /upload`)** — Live E2E tested; Meta returned a media ID.
- **Health (`/healthz`) and conversation listing (`/conversations`)** — Live REST tested; both returned HTTP 200, and the conversation list grew from 41 to 42 after the test chat.
- **DGW frame replay and other listed routes** — Implemented; do not infer live verification from their presence in the API reference.

## Features

- 🖼️ **Image generation** — Create images from text prompts
- 💬 **Chat** — Text conversation with Meta AI (Instant + Thinking modes)
- 📋 **Conversation management** — List conversations
- 📎 **File upload** — Upload generic files to Meta's rupload handler; no local original copy is retained
- 🔗 **Media fetching/downloading** — Get URLs or stream generated images/videos by media-library item ID, CDN filename ID, or external conversation ID
- 📄 **Generated-document export** — List conversation artifacts and download supported PDF, DOCX and TXT outputs in their original format
- 🔓 **No API key** — Uses your browser cookies for authentication
- 🌐 **REST API server** — Deploy once, call from any language
- 📦 **Python SDK** — Use as a library in your Python projects

## Install

This personal copy is not published to PyPI as `metaai-sdk`; that PyPI project name is already used by another package. Install this copy directly from GitHub instead. The Playwright extra is recommended for Windows, macOS, and Linux, and does not require Node.js.

### Recommended: clone this repository

```bash
git clone https://github.com/paoqiyubeipan/meta_ai-api.git
cd meta_ai-api
python -m pip install -e ".[api,playwright]"
python -m playwright install chromium
```

Or install directly from GitHub without cloning:

```bash
python -m pip install "metaai-sdk[api,playwright] @ git+https://github.com/paoqiyubeipan/meta_ai-api.git"
python -m playwright install chromium
```

On Linux, if Chromium reports missing system libraries, run `python -m playwright install-deps chromium` (administrator privileges may be required).

## Quick Start

### As a Python SDK

```python
from metaai_api import MetaAI

ai = MetaAI(cookies={
    "datr": "your_datr_cookie",
    "ecto_1_sess": "your_ecto_1_sess_cookie",
})

# Generate an image
result = ai.generate_image_new("a watercolor painting of a red panda")
print(result["image_urls"])

# Chat
reply = ai.prompt("What is 2+2?")
print(reply["message"])

# List conversations
convs = ai.list_conversations()
for c in convs:
    print(c["title"])

ai.close()
```

### As an API server

```bash
export META_AI_DATR="your_datr_cookie"
export META_AI_ECTO_1_SESS="your_ecto_1_sess_cookie"
export META_AI_API_KEY="your_secret_key"  # optional

python -m metaai_api.api_server
```

Then call from any language:

```bash
curl -X POST http://localhost:8000/image \
  -H "Authorization: Bearer your_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a watercolor painting of a cat"}'
```

## Getting Your Cookies

1. Open [https://www.meta.ai/](https://www.meta.ai/) and log in
2. Press F12 → Application → Cookies → `https://www.meta.ai`
3. Copy the values of `datr` and `ecto_1_sess`

Or set them as environment variables:
```bash
export META_AI_DATR="..."
export META_AI_ECTO_1_SESS="..."
```

See [CHANGES_AND_COOKIES.md](CHANGES_AND_COOKIES.md) for details on cookie expiration and the optional access token.

## API Reference

### Python SDK

| Method | Description | Verification |
|--------|-------------|--------|
| `ai.generate_image_new(prompt)` | Generate image from text | Live E2E tested |
| `ai.prompt(message)` | Send a text chat message | REST route live-tested; cold-start results are mixed (one three-start cohort failed, a later three-start cohort passed); SDK method not separately tested live; reliability is unproven |
| `ai.list_conversations()` | List conversations | Live REST tested; 200 with 41 entries before, 42 after test chat |
| `ai.new_conversation()` | Start a new conversation | Implemented; live verification not claimed |
| `ai.fetch_card_media(media_id)` | Resolve media from Meta's current media-library feed | REST generated-image download flow Live E2E tested; SDK method itself not separately tested live; unit tests use Mocks |
| `ai.upload_file(file_bytes, filename, mime_type=None)` | Send a generic file to Meta's rupload handler | REST PDF upload Live E2E tested; SDK method itself not separately tested live; requires access token; other formats not verified |
| `ai.replay_frame(frame_b64)` | Replay captured DGW frame (advanced) | Implemented; live verification not claimed |
| `ai.warmup_conversation(conv_id)` | Register a new conversation | Implemented; live verification not claimed |
| `ai.generate_video_new(prompt)` | Legacy text-to-video flow | Live-tested refusal for this account; REST route returns 503 |
| `ai.extend_video(media_id)` | Extend a video | Not implemented |
| `ai.close()` | Close browser session | Implemented |

### REST API Endpoints

| Endpoint | Method | Description | Verification |
|----------|--------|-------------|--------|
| `/healthz` | GET | Health check | Live REST tested; HTTP 200 |
| `/chat` | POST | Send a text chat message | Live REST tested; results are inconsistent across independent cold starts; see current verification status |
| `/image` | POST | Generate image from text or reference inputs | Text-to-image and reference-image flows Live E2E tested |
| `/image/reference` | POST | Generate image from multipart reference file | Reference-image flow Live E2E tested; endpoint tests also use Mocks |
| `/video` | POST | Text-to-video | Live-tested refusal; returns 503 for this account |
| `/video/async` | POST | Async text-to-video job | Implemented; generation remains unavailable for this account |
| `/video/jobs/{job_id}` | GET | Persisted video job status | Implemented; live verification not claimed |
| `/video/extend` | POST | Extend a video | Not implemented (501) |
| `/upload` | POST | Upload an image to Meta rupload | Live E2E tested in the `/upload` → `/image` reference chain; requires access token; reusable `media_id` is resolved from this API's 24-hour local image cache |
| `/files` | POST | Upload a generic file to Meta rupload; returns Meta's media ID and metadata only | Synthetic PDF Live E2E tested; 10 MiB API cap; requires access token; no local copy or download route |
| `/conversations` | GET | List conversations | Live REST tested; HTTP 200 with 41 then 42 entries |
| `/conversations/{conversation_id}/documents` | GET | List supported PDF, DOCX and TXT artifacts Meta AI generated in a conversation | Live E2E tested for all three formats |
| `/conversations/{conversation_id}/documents/{index}/download` | GET | Export a generated PDF, DOCX or TXT in its original format | Live E2E tested; returns the matching MIME type and filename |
| `/media` | POST | Resolve a media-library item, CDN filename ID, or conversation ID | Used in Live E2E generated-image download flow |
| `/media/{card_id}/download` | GET | Stream generated media from Meta's CDN | Live E2E tested for generated image; not for video |
| `/reset` | POST | Reset browser session | Implemented; live verification not claimed |
| `/docs` | GET | Swagger UI | Implemented |

### Chat thinking mode

Both `ai.prompt()` and `POST /chat` use Thinking mode by default. To use Instant mode, pass `thinking_mode=False` in the SDK or include `"thinking_mode": false` in the REST request body. Omitting the field keeps Thinking enabled.

## How It Works

Meta AI uses a custom WebSocket protocol called **DGW (Datagram WebSocket)** for image generation and chat. The server validates message integrity, so you can't construct valid messages from scratch in Python.

This SDK prefers **Playwright** (pure Python, no Node.js) when installed and falls back to `agent-browser` otherwise. The Playwright backend drives a real headless Chromium browser:
1. Launches the browser with your cookies injected
2. Types the prompt into the meta.ai chat input
3. Waits for the image/response to appear
4. Extracts URLs and text from the page

For advanced users, the `replay_frame()` method allows replaying captured DGW frames via WebSocket (each frame works once).

See [GENERATION_API.md](GENERATION_API.md) for detailed API usage.

## Deployment

### Docker

```bash
export META_AI_DATR="..."
export META_AI_ECTO_1_SESS="..."
export META_AI_API_KEY="..."

docker-compose up -d
```

### Direct

```bash
python -m pip install "metaai-sdk[api,playwright] @ git+https://github.com/paoqiyubeipan/meta_ai-api.git"
python -m playwright install chromium
python -m metaai_api.api_server
```

See [QUICK_START.md](QUICK_START.md) for a step-by-step guide.

## Limitations

- **Video generation (live-tested 2026-11-02)**: A signed-in account asked Meta AI for a video in both English and Japanese; Meta AI answered "Video generation isn't available to you right now" / 「動画生成は現在ご利用いただけません」 and offered an image or a GIF made from images instead. `/video` now returns **503** with that reason instead of polling until a timeout. The current web build also posts JSON `{doc_id, variables}` to `/api/graphql`, while this SDK still sends the older multipart + `lsd`/`fb_dtsg` body, and the old numeric `doc_id` is rejected with HTTP 400. So the video path cannot be revived until Meta re-enables video for the account and a fresh request is captured. Image-to-video and video extension are therefore also unimplemented: there is no capturable request flow.
- **Cookie expiration**: `ecto_1_sess` expires frequently. Re-extract if requests fail.
- **Speed**: ~30-40s per image (browser startup + generation).
- **Region**: Meta AI isn't available in all regions.
- **Generic file upload**: `/files` sends files through the reverse-engineered `genai_document` rupload handler, requires `META_AI_ACCESS_TOKEN`, and limits requests to 10 MiB. The API returns Meta's media ID and upload metadata only; it retains no local original and has no `GET /files/{file_id}` route. A synthetic PDF upload to Meta was Live E2E tested; other file types are not covered by that test. Meta does not publish a `meta.ai` format allowlist, size limit or upload API contract. The separate [WhatsApp file-attachment help](https://faq.whatsapp.com/812625134612934) lists formats and a 40 MB cap for WhatsApp only; those limits do not establish what `meta.ai` supports. Uploading a file here does not attach it to a chat turn or guarantee that Meta can parse it.
- **Generated-document export (live-tested 2026-11-02)**: PDF, DOCX and TXT replies all produced downloadable artifacts in real Meta conversations. Signed `*.a.metaaiusercontent.com` viewer pages carry the PDF bytes as base64, the original DOCX ZIP bytes in a Next.js flight row, and TXT content in the `TextViewer` props. `/conversations/{id}/documents/{index}/download` returns the matching file bytes and MIME type, not a screenshot or PDF conversion. The live REST check returned HTTP 200 for all three (PDF 15,518 bytes; DOCX 36,607 bytes; TXT 35 bytes) and verified the DOCX package and each probe's text. Other artifact extensions remain unsupported until their payload format is verified.
- **Web chat attachment reading (separate UI test, live-tested 2026-11-02)**: Files were manually attached in a signed-in `meta.ai` web chat, not through this API. The test set included values hidden in a specific table row, sheet cell, slide or nested key. PDF (including content only on page 3), DOCX, TXT, CSV, JSON and XLSX were read and answered with the expected values. PPTX and ZIP never reached a chat: the composer's file input has no PowerPoint or archive types, and a PPTX attempt was rejected with 「サポートされていない画像フォーマットです」. This does not mean `POST /files` attaches files to `/chat`. See [GENERATION_API.md](GENERATION_API.md#web-chat-attachment-reading).
- **Image upload**: `/upload` is image-only and validates the file signature. Its local-cache and reference-input integration has Mock coverage; a separate live E2E test of the `/upload` → `/image` `media_ids` chain is not claimed. IDs uploaded elsewhere cannot be resolved by this local cache.
- **Generated-media download**: `/media/{card_id}/download` streams generated media from a known Meta CDN host. The generated-image download flow was Live E2E tested; video generation was unavailable during testing, so do not infer live video-download verification. Generated documents use the separate `/conversations/{id}/documents` routes above.
- **Async video jobs**: Job records survive process restarts. Jobs with a saved conversation ID resume polling; a request interrupted during submission is marked `interrupted` rather than automatically resubmitted, avoiding duplicate generation.

## Environment Variables
| Variable | Description |
|----------|-------------|
| `META_AI_DATR` | datr cookie (required) |
| `META_AI_ECTO_1_SESS` | ecto_1_sess cookie (required) |
| `META_AI_ABRA_SESS` | abra_sess cookie (optional, some regions) |
| `META_AI_ACCESS_TOKEN` | `ecto1:` access token; required by `/upload` and `/files`, and used by advanced WebSocket methods |
| `META_AI_API_KEY` | API key for the server (optional) |
| `META_AI_HEADED` | Show browser window: "true" or "false" (default) |
| `META_AI_STATE_DB` | SQLite state database path (default `~/.local/state/metaai-api/state.sqlite3`) |

## Project Structure

```
metaai-api/
├── src/metaai_api/
│   ├── __init__.py          # Package exports
│   ├── main.py              # MetaAI class (main entry point)
│   ├── api_server.py        # FastAPI server and REST endpoints
│   ├── browser.py           # Browser automation
│   ├── client.py            # MetaAIClient (HTTP/WebSocket)
│   ├── documents.py         # Generated-document export
│   ├── exceptions.py        # Error hierarchy
│   ├── generation.py        # Image and media operations
│   ├── image_inputs.py      # Reference-image validation
│   ├── image_upload.py      # Meta file upload
│   ├── parser.py            # DGW frame parser
│   ├── playwright_backend.py # Playwright browser backend
│   ├── state_store.py       # Persistent API state
│   ├── utils.py             # Constants and helpers
│   └── video_generation.py  # Experimental video workflow
├── test/                    # Current API and SDK tests
├── Dockerfile               # Docker deployment
├── docker-compose.yml       # Docker Compose
├── pyproject.toml           # Package config
└── requirements.txt         # Dependencies
```

## License

MIT

## Author

Ashiq Hussain Mir
