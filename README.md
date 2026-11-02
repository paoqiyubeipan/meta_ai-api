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

This version uses **browser automation** as the primary method, which works for ANY prompt. It prefers **Playwright** (pure Python; no Node.js required) when installed and falls back to `agent-browser`. The browser creates valid DGW messages natively.

### Breaking Changes from v2.x
- `prompt()` now uses browser automation (no more OAuth token extraction)
- `generate_image_new()` uses browser automation
- Text-to-video is exposed as an **experimental** legacy GraphQL workflow; Meta may reject it or have moved the feature
- `upload_image()` uses Meta's rupload endpoint and requires `META_AI_ACCESS_TOKEN`
- Cookie-based auth replaces access token for primary methods

### What Still Works
- ✅ Image generation (text → image)
- ✅ Reference-image generation through the Playwright composer (file input or base64 data URL)
- ❌ Text-to-video: live-tested 2026-11-02 — Meta AI declines video generation for the account, so `/video` returns 503 with the upstream reason (no image-to-video, no extension)
- ✅ Direct image upload (`/upload`, returns a media ID; this ID is not yet consumed by the composer)
- ✅ Generic file upload (`/files`) and 24-hour local original download (`GET /files/{file_id}`; Meta acceptance varies)
- ✅ Chat (text conversation, Instant + Thinking modes)
- ✅ Conversation management (list, new)
- ✅ Media fetching and direct image/video download by media-library item ID or CDN filename ID
- ✅ Document export: download a PDF Meta AI generated in a conversation (`/conversations/{id}/documents`)
- ✅ DGW frame replay (advanced WebSocket method)

## Features

- 🖼️ **Image generation** — Create images from text prompts
- 💬 **Chat** — Text conversation with Meta AI (Instant + Thinking modes)
- 📋 **Conversation management** — List conversations
- 📎 **File transfer** — Upload files to Meta's rupload handler and download the locally retained original for 24 hours
- 🔗 **Media fetching/downloading** — Get URLs or stream generated images/videos by media-library item ID, CDN filename ID, or external conversation ID
- 📄 **Generated-document export** — List the documents a conversation produced and download the original PDF bytes
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

| Method | Description | Status |
|--------|-------------|--------|
| `ai.generate_image_new(prompt)` | Generate image from text | ✅ Works |
| `ai.prompt(message)` | Send a chat message | ✅ Works |
| `ai.list_conversations()` | List all conversations | ✅ Works |
| `ai.new_conversation()` | Start a new conversation | ✅ Works |
| `ai.fetch_card_media(media_id)` | Resolve media from Meta's current media-library feed | ✅ Works |
| `ai.upload_file(file_bytes, filename, mime_type=None)` | Send a file to Meta's rupload handler | ⚠️ Upstream acceptance varies; requires access token |
| `ai.replay_frame(frame_b64)` | Replay captured DGW frame (advanced) | ✅ Works |
| `ai.warmup_conversation(conv_id)` | Register a new conversation | ✅ Works |
| `ai.generate_video_new(prompt)` | Generate text-to-video via captured GraphQL flow | ⚠️ Experimental |
| `ai.extend_video(media_id)` | Extend a video | ❌ Not supported |
| `ai.close()` | Close browser session | ✅ Works |

### REST API Endpoints

| Endpoint | Method | Description | Status |
|----------|--------|-------------|--------|
| `/healthz` | GET | Health check | ✅ |
| `/chat` | POST | Send a chat message | ✅ |
| `/image` | POST | Generate image from text | ✅ |
| `/image/reference` | POST | Generate image from multipart reference file | ✅ |
| `/video` | POST | Text-to-video | ❌ 503 (Meta declines for this account) |
| `/video/async` | POST | Async text-to-video job | ⚠️ Queues, then fails with the same reason |
| `/video/jobs/{job_id}` | GET | Persisted video job status | ✅ |
| `/video/extend` | POST | Extend a video | ❌ 501 |
| `/upload` | POST | Upload an image to Meta rupload | ✅ (access token required) |
| `/files` | POST | Upload a generic file to Meta rupload | ✅ (10 MiB API cap; access token required) |
| `/files/{file_id}` | GET | Download this API's locally cached upload original (24 hours) | ✅ |
| `/conversations` | GET | List conversations | ✅ |
| `/conversations/{conversation_id}/documents` | GET | List documents Meta AI generated in a conversation | ✅ (live-tested) |
| `/conversations/{conversation_id}/documents/{index}/download` | GET | Export a generated document as its original PDF | ✅ (live-tested) |
| `/media` | POST | Resolve a media-library item, CDN filename ID, or conversation ID | ✅ |
| `/media/{card_id}/download` | GET | Stream generated media from Meta's CDN | ✅ |
| `/reset` | POST | Reset browser session | ✅ |
| `/docs` | GET | Swagger UI | ✅ |

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
- **File upload/download**: `/files` sends files through the reverse-engineered `genai_document` rupload handler, requires `META_AI_ACCESS_TOKEN`, and limits requests to 10 MiB. `/files/{file_id}` downloads the original bytes cached locally for 24 hours; it does not retrieve the file from Meta. Meta does not publish a `meta.ai` format allowlist, size limit or upload API contract. The separate [WhatsApp file-attachment help](https://faq.whatsapp.com/812625134612934) lists PDF, JSON, TXT, DOC/DOCX, XLS/XLSX, PPT/PPTX and CSV up to 40 MB, but those limits do not establish what the `meta.ai` website supports. Uploading a file here does not attach it to a chat turn or guarantee that Meta can parse it.
- **Generated-document export (live-tested 2026-11-02)**: A document reply is a real file. Meta builds an A4 PDF with ReportLab and serves it from a signed `*.a.metaaiusercontent.com` viewer page that embeds the bytes as base64, so `/conversations/{id}/documents/{index}/download` returns the original PDF (15,518 bytes in the live run) rather than a re-rendered screenshot; text stays selectable. Only `PDF` artifacts carry that payload — other artifact types have no downloadable file and return 404.
- **Document reading in chat (live-tested 2026-11-02)**: Files were attached in a signed-in `meta.ai` chat and each carried a random code plus a value hidden in a specific table row, sheet cell, slide or nested key. PDF (including content only on page 3), DOCX, TXT, CSV, JSON and XLSX were all read and answered with exactly the right values. PPTX and ZIP never reached a chat: the composer's file input has no PowerPoint or archive types, and a PPTX attempt was rejected with 「サポートされていない画像フォーマットです」. See [GENERATION_API.md](GENERATION_API.md#verified-document-reading-chat-2026-11-02).
- **Image upload**: `/upload` remains image-only and validates the file signature. Images uploaded through this API are cached locally for 24 hours, so their `media_id` can be passed to `/image` as a reference; IDs uploaded elsewhere cannot be resolved by this local cache.
- **Generated-media download**: `/media/{card_id}/download` streams a generated image/video from a known Meta CDN host. Generated documents use the separate `/conversations/{id}/documents` routes above.
- **Async video jobs**: Job records survive process restarts. Jobs with a saved conversation ID resume polling; a request interrupted during submission is marked `interrupted` rather than automatically resubmitted, avoiding duplicate generation.

## Environment Variables
| Variable | Description |
|----------|-------------|
| `META_AI_DATR` | datr cookie (required) |
| `META_AI_ECTO_1_SESS` | ecto_1_sess cookie (required) |
| `META_AI_ABRA_SESS` | abra_sess cookie (optional, some regions) |
| `META_AI_ACCESS_TOKEN` | DGW access token (for WebSocket method, optional) |
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
