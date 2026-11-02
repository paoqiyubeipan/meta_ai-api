# Generation API

## Text-to-image

### Python SDK

```python
from metaai_api import MetaAI

ai = MetaAI(cookies={"datr": "...", "ecto_1_sess": "..."})
result = ai.generate_image_new(
    prompt="a watercolor painting of a red panda",
    orientation="VERTICAL",  # VERTICAL, HORIZONTAL, SQUARE
    num_images=1,
    timeout=120,
)
print(result["image_urls"])
```

### REST API

```bash
curl -X POST http://localhost:8000/image \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a watercolor painting of a red panda","num_images":1}'
```

### Verification labels

**Implemented** means the code path exists. **Mock tests pass** means automated tests used fake Meta/network responses; it is not proof that the live service accepted the request. **Live E2E tested** means the whole flow was exercised against a signed-in Meta AI account. Current live checks cover text-to-image, direct reference-image generation, the `/upload` → `media_id` → `/image` reference chain, text chat (with mixed cold-start results below), a synthetic PDF `/files` → `/chat` chain with an exact random-code answer, health and conversation listing, generated-image download from Meta's media library, and export of Meta-generated PDF, DOCX and TXT files. Other routes and file formats must not be assumed live-tested.

## Text chat

`POST /chat` and `ai.prompt()` are implemented. Repeated live cold-start checks produced inconsistent results. After adding bounded UI-readiness waits (up to about 2 seconds for a verifiable composer and 5 seconds for the Send button), one cohort of three separate server/browser processes still returned HTTP 500 on all three first requests. The sanitized category was `ConnectionError` from the browser UI: the Send button stayed disabled. Two same-instance retries returned 500; the third received HTTP 200, but the test harness's local answer validator then raised `TypeError`, so correctness for that retry was not recorded. A later, corrected cohort of three independent server/browser cold starts returned HTTP 200 with exact answers to random arithmetic on all three first requests. The changing outcome means cold-start reliability remains unproven; the UI wait is a mitigation, not a verified root-cause fix. The synchronous Playwright call is offloaded to a worker thread, but that change alone did not explain the UI state. Do not treat Mock tests as proof of live reliability.

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is 12 squared?"}'
```

### Attach a file uploaded through `/files`

`/chat` accepts legacy `media_ids`, an `attachments` list with `media_id`/`file_id`/`id`, or IDs inside `attachment_metadata`. The ID must come from `/files` or the image-only `/upload` endpoint on the **same running API process**. For generic files, the API keeps a bounded in-memory copy for up to 24 hours (16 entries and 50 MiB total; 10 MiB per file). It is not written to the database or disk and is lost when the process stops. At chat time the API materializes a short-lived, permission-restricted temporary file and attaches it through Meta AI's web composer; it does not guess a wire-format representation for the media ID.

```bash
curl -X POST http://localhost:8000/files -F 'file=@./report.pdf'
# Copy the response's media_id into the request below.
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the unique code in the attached PDF? Reply with only the code.","media_ids":["MEDIA_ID_FROM_FILES"],"attachment_metadata":{"file_name":"report.pdf","mime_type":"application/pdf"}}'
```

The browser composer uploads the retained bytes again when it submits the chat turn. The synthetic PDF `/files` → `/chat` flow has passed Live E2E with the exact code; other formats have not been verified through this API chain. Unknown, expired, or cross-process IDs return **400** without sending the prompt. A maximum of four files can be attached to one chat turn.

## Reference-image generation

Reference-image generation (image-to-image) has been Live E2E tested. The browser composer can upload local reference images. The REST JSON endpoint accepts one or more base64 data URLs; use `/image/reference` for a normal multipart file upload. Install the `playwright` extra and Chromium. Meta AI may mount its hidden file input before the composer is ready, so the backend activates the hydrated **Add attachment** button, waits for a successful `rupload` response and an image preview, fills the editable composer itself, and clicks **Send**. It fails closed if attachment or prompt entry cannot be verified. Image-generation polling continues after the assistant's text appears, because the media URL can arrive a few seconds later. The `/upload` → returned `media_id` → `/image` chain was also Live E2E tested: the image's distinctive colors and shapes appeared in the generated result. For this chain, the ID resolves to image bytes kept in this API's local cache for 24 hours; it is not a durable arbitrary Meta media ID. The live check does not separately certify every supported REST input form.

```bash
curl -X POST http://localhost:8000/image/reference \
  -F 'prompt=Keep the same subject, but make it a watercolor illustration' \
  -F 'reference_image=@./reference.png'
```

Alternatively send JSON to `/image` with `reference_image`, `reference_images`, or the Gemini-proxy-compatible `image` field containing `data:image/png;base64,...` values. Each image is limited to 10 MiB. For the SDK, pass server-local paths using `reference_images=["./reference.png"]`:

```python
result = ai.generate_image_new(
    "A watercolor portrait using the reference subject",
    reference_images=["./reference.png"],
)
```

## Upload files to Meta

`POST /files` sends the bytes through the reverse-engineered Meta `rupload` service with the `genai_document` handler and a MIME type inferred from the filename (or supplied by the multipart client). The API code can attempt to upload PDF, DOC/DOCX, TXT, CSV, JSON, XLS/XLSX, PPT/PPTX and ZIP, up to **10 MiB per request**. This is a list of inputs the code can attempt, not a Meta-supported-format list. A synthetic PDF has passed both upload and `/files` → `/chat` Live E2E tests; other formats have not been live-verified through this chain. The 10 MiB size is this API's safety cap, not a published Meta AI limit. Meta's acceptance and ability to read a given file remain account-, region- and upstream-dependent. Successful upload returns Meta's `file_id`/`media_id` and metadata, and stores a bounded in-memory copy for the chat flow (24-hour TTL, 16 files and 50 MiB total; 10 MiB per file). The bytes are not persisted to disk or the database, are lost on process restart, and cannot be downloaded from a `GET /files/{file_id}` route.

## Download documents Meta AI generated

A Canvas / document reply can be a real downloadable file. Live checks on 2026-11-02 found three supported artifact types in conversation data: PDF (`/pdf` viewer with the original PDF bytes as base64), DOCX (`/document` viewer with original DOCX ZIP bytes in a Next.js flight row), and TXT (`/document` viewer with the exact text in `TextViewer` props). The API returns these original bytes or UTF-8 text; it does not re-render a screenshot or convert the document to PDF. The REST list-and-download flow was tested against all three formats on a signed-in account. Other extensions remain unsupported unless their viewer payload is verified.

```bash
curl 'http://localhost:8000/conversations/CONVERSATION_ID/documents'
# -> {"success": true, "documents": [{"index": 0, "file_name": "Generated.pdf",
#      "file_extension": "pdf", "mime_type": "application/pdf",
#      "artifact_uuid": "...", "asset_id": "...",
#      "downloadable": true,
#      "download_url": "/conversations/CONVERSATION_ID/documents/0/download"}]}

curl -L 'http://localhost:8000/conversations/CONVERSATION_ID/documents/0/download' -o generated.pdf
```

Choose the output extension from the listed `file_name`/`file_extension` (for example, `generated.docx` or `generated.txt`); the download response includes the matching MIME type. The conversation page and signed viewer are fetched with the API session cookies, so no browser is involved. A conversation with no supported artifacts returns **404**. This generated-document export is separate from generic file upload: uploaded originals are only kept temporarily in process memory for `/chat`, not durably stored or downloadable through `/files`.

### Web chat attachment reading

The table below is from a separate test that manually attached files in a signed-in `meta.ai` web chat. Each fixture held a random code plus a value that could only be found by locating a specific table row, sheet cell or nested key. A reply counted as a pass only when both values were exact. Separately, the API-level `/files` → `/chat` flow is Live E2E tested for a synthetic PDF: the answer matched its unique random code exactly. Other formats have not been verified through the API chain.

| Format | Attach in composer | Read and answered correctly |
|--------|--------------------|-----------------------------|
| PDF (3 pages, table on page 1) | yes | yes |
| PDF (code only on page 3) | yes | yes — later pages are read, not just page 1 |
| DOCX (paragraph + table cell) | yes | yes |
| TXT | yes | yes |
| CSV (row lookup) | yes | yes |
| JSON (nested keys) | yes | yes |
| XLSX (cell lookup) | yes | yes |
| PPTX | **no** — rejected with "サポートされていない画像フォーマットです…" | not reached |
| ZIP | **no** — no attachment preview appears | not reached |

The composer's file input lists PDF, DOC/DOCX, XLS/XLSX, images, MP4/MOV, plain text and code/config files (`.txt .md .json .csv .tsv .yaml .toml .html .xml .css .js .ts .py .go .rs .sql .sh .env` and similar), but no PowerPoint or archive types. Its MIME filter rejects other files before any upload happens. A generic `/files` upload attempt does not establish that a format can attach or be read through `/chat`; PPTX/ZIP have not been tested through the API chain and are not accepted by the observed web composer. Tabular and nested data (CSV rows, XLSX cells, DOCX tables) survived into the separate manual web-chat answers; replies arrived roughly 10–20 seconds after sending. Legacy binary `.doc`/`.xls`, scanned image-only PDFs and password-protected files remain untested.

```bash
curl -X POST http://localhost:8000/files \
  -F 'file=@./report.pdf'
# copy media_id from the response, then use it in /chat
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the unique code in the attached PDF? Reply only with the code.","media_ids":["MEDIA_ID_FROM_FILES"],"attachment_metadata":{"file_name":"report.pdf","mime_type":"application/pdf"}}'
```

Use only an ID returned by `/files` in the same API process and before it expires; IDs from a separate process or an upload performed outside this API cannot be resolved. The `/chat` request attaches the API's in-memory file copy through the web composer, which sends those file bytes to Meta again before the prompt. Keep your own local original if you need durable storage. The older image-only `/upload` endpoint still accepts its own returned image `media_id` for reference-image generation.

The image-specific route remains available and still validates image signatures:

```bash
curl -X POST http://localhost:8000/upload \
  -F 'file=@./reference.png'
```

The `/upload` route and its local-cache integration have Mock tests. The returned image `media_id` can be passed to `/image` as a `media_ids` entry when the image was uploaded through this API; that specific upload-then-generate chain is not separately claimed as Live E2E tested:

```bash
curl -X POST http://localhost:8000/image \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Make a watercolor illustration based on this reference","media_ids":["MEDIA_ID_FROM_UPLOAD"]}'
```

## Video generation
Live-tested on 2026-11-02 with a signed-in Meta AI account. Asked for a five-second clip in English and in Japanese, Meta AI replied "Video generation isn't available to you right now" / 「動画生成は現在ご利用いただけません」 and offered to make a still image, or a GIF stitched from images, instead. The API now detects that refusal and returns **503** with the reason, instead of polling until the attempt limit.

The current Meta AI web build posts JSON `{doc_id, variables}` to `/api/graphql`, whereas this SDK sends the older multipart body with `lsd`/`fb_dtsg` fields. The older numeric `doc_id` is rejected by the current endpoint with HTTP 400, so the video path needs a fresh capture once Meta enables video generation for the account. Image-to-video and video extension have no capturable request flow in this state, and `/video/extend` stays `501`.

```bash
curl -X POST http://localhost:8000/video \
  -H "Content-Type: application/json" \
  -d '{"prompt":"A red panda walking through a bamboo forest","auto_poll":true}'
```

For longer jobs, use `POST /video/async` and poll `GET /video/jobs/{job_id}`. Job state is stored in SQLite and survives restarts. Jobs with a saved Meta conversation ID resume polling after restart. If the process stops during submission before that ID is saved, the job is marked `interrupted` rather than resubmitted automatically, to avoid accidentally generating a duplicate.

The database path defaults to `~/.local/state/metaai-api/state.sqlite3`; set `META_AI_STATE_DB` to change it. Docker Compose uses a named volume for this database. The database stores video-job state and image-reference uploads, not generic `/files` upload copies.

## Media fetching

Meta's current web app resolves generated media through its paginated `MediaLibraryFeed` query; the older `imagineCardMedia` GraphQL field is no longer available. `POST /media` and the download route now accept a media-library item ID, the numeric ID embedded in that item's CDN filename, or its external conversation ID. A media-library item ID is the most reliable choice. The lookup scans a bounded number of recent pages; an older item beyond that window may not be found.

To download an image or video directly, use the item's type and the zero-based index (defaults to `IMAGE_CARD` and `0`):

```bash
curl -L 'http://localhost:8000/media/MEDIA_ITEM_ID/download?card_type=IMAGE_CARD&index=0' \
  -o generated-image.jpg

curl -L 'http://localhost:8000/media/MEDIA_ITEM_ID/download?card_type=VIDEO_CARD&index=0' \
  -o generated-video.mp4
```

The REST media-library lookup and generated-image download flow was Live E2E tested; media lookup and download logic also have Mock tests. The route streams only from HTTPS Meta CDN hosts. Video streaming is present in code, but video generation was unavailable in the live check, so video download is not claimed as Live E2E tested. For generated Canvas files, use the document endpoints above; PDF, DOCX and TXT are live-verified, while other artifact extensions are not yet supported. Meta's separate [WhatsApp help page for file attachments](https://faq.whatsapp.com/812625134612934) lists formats and a 40 MB per-file cap for WhatsApp, but that is a WhatsApp feature and should not be assumed to describe `meta.ai`.

Meta's official [Meta AI help](https://www.meta.com/help/artificial-intelligence/943942350800511/) documents asking about photos, and its [text-document help](https://www.meta.com/help/artificial-intelligence/1319682792536795/) documents creating and editing text documents. These pages do not publish a supported `meta.ai` file-format list, upload API contract, maximum size, or document-download API. This project's `rupload` integration is unofficial and may change or reject particular file types.

Python SDK access to fetched URLs remains available:

```python
data = ai.fetch_card_media("MEDIA_ITEM_ID", card_type="IMAGE_CARD")
urls = ai.generation_api.extract_media_urls(data)
print(urls)
```
