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

**Implemented** means the code path exists. **Mock tests pass** means automated tests used fake Meta/network responses; it is not proof that the live service accepted the request. **Live E2E tested** means the whole flow was exercised against a signed-in Meta AI account. Current live checks cover text-to-image, reference-image generation, text chat (with a first-request failure caveat below), health and conversation listing, one synthetic PDF upload followed by byte-for-byte download from the local cache, generated-image download from Meta's media library, and export of a Meta-generated PDF. Other routes and file formats must not be assumed live-tested.

## Text chat

`POST /chat` and `ai.prompt()` are implemented. A direct SDK call returned the correct answer (`17 × 23 = 391`) in a live account test. In a separate REST test after moving the synchronous Playwright call into a thread pool, the first `POST /chat` after server startup returned HTTP 500; a retry returned HTTP 200 with the correct answer (`391 × 22 = 8602`). `/conversations` returned 41 entries before and 42 after the successful chat. The first-request failure has not been explained, so cold-start reliability is not yet established; do not treat Mock tests as proof of live reliability.

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is 12 squared?"}'
```

## Reference-image generation

Reference-image generation (image-to-image) has been Live E2E tested. The browser composer can upload local reference images. The REST JSON endpoint accepts one or more base64 data URLs; use `/image/reference` for a normal multipart file upload. Install the `playwright` extra and Chromium. Meta AI may mount its hidden file input before the composer is ready, so the backend activates the hydrated **Add attachment** button, waits for a successful `rupload` response and an image preview, fills the editable composer itself, and clicks **Send**. It fails closed if attachment or prompt entry cannot be verified. Image-generation polling continues after the assistant's text appears, because the media URL can arrive a few seconds later. This live test does not separately certify every supported REST input form or the `/upload` → `media_ids` chain.

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

## Upload and download files

`POST /files` sends the bytes through the reverse-engineered Meta `rupload` service with the `genai_document` handler and a MIME type inferred from the filename (or supplied by the multipart client). The API code can submit files such as PDF, DOC/DOCX, TXT, CSV, JSON, XLS/XLSX, PPT/PPTX and ZIP, up to **10 MiB per request**. This list describes inputs the code can attempt to send; it is not a Meta-supported-format list. A synthetic PDF was uploaded to Meta in a Live E2E test; other formats have not been live-verified through this endpoint. The 10 MiB size is this API's safety cap, not a published Meta AI limit. Meta's acceptance and ability to read a given file remain account-, region- and upstream-dependent. This route does not attach the returned ID to a chat turn.

For the tested PDF flow, the API kept the exact original bytes in its local SQLite cache and `GET /files/{file_id}` returned them byte-for-byte. The cache lasts 24 hours; this endpoint downloads the local original, not a copy fetched back from Meta. IDs uploaded outside this API are not in the local cache. The route tests also cover these behaviors with Mocks, but those Mock tests alone do not establish live Meta acceptance.

## Download documents Meta AI generated

A Canvas / document reply is a real file: Meta builds an A4 PDF (ReportLab) and serves it from a signed page on `*.a.metaaiusercontent.com`. The viewer embeds those bytes as base64, so this API can return the original PDF instead of re-rendering a screenshot. The list-and-download REST flow was Live E2E tested on 2026-11-02 against a signed-in account. Only PDF artifacts with an embedded payload are downloadable.

```bash
curl 'http://localhost:8000/conversations/CONVERSATION_ID/documents'
# -> {"success": true, "documents": [{"index": 0, "file_name": "Generated.pdf",
#      "mime_type": "application/pdf", "artifact_uuid": "...", "asset_id": "...",
#      "downloadable": true,
#      "download_url": "/conversations/CONVERSATION_ID/documents/0/download"}]}

curl -L 'http://localhost:8000/conversations/CONVERSATION_ID/documents/0/download' -o generated.pdf
```

The conversation page is read with your `metaai_session` cookies, so no browser is involved. Only `PDF` artifacts carry an embedded payload; other artifact types return **404** with an explanation, and a conversation without documents returns **404** as well. `GET /files/{id}` still serves only the local copy of a file you uploaded here — the two routes are unrelated.

### Web chat attachment reading

This was tested end to end by manually attaching files in a signed-in `meta.ai` web chat, not by sending an ID from this API. Each fixture held a random code plus a value that could only be found by locating a specific table row, sheet cell or nested key. A reply counted as a pass only when both values were exact. These results show what the web chat read in that test; they do not mean `POST /files` attaches files to `/chat`.

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

The composer's file input lists PDF, DOC/DOCX, XLS/XLSX, images, MP4/MOV, plain text and code/config files (`.txt .md .json .csv .tsv .yaml .toml .html .xml .css .js .ts .py .go .rs .sql .sh .env` and similar), but no PowerPoint or archive types. Its MIME filter rejects other files before any upload happens. Although this API's generic upload code can attempt to send PPTX/ZIP bytes, that does not make them attachable or readable in chat, and those upload types were not included in the live API test. Tabular and nested data (CSV rows, XLSX cells, DOCX tables) survived into the web-chat answers; replies arrived roughly 10–20 seconds after sending. Legacy binary `.doc`/`.xls`, scanned image-only PDFs and password-protected files remain untested.

```bash
curl -X POST http://localhost:8000/files \
  -F 'file=@./report.pdf'
# response includes file_id, media_id, and download_url

curl -L 'http://localhost:8000/files/FILE_ID' -o report.pdf
```

The example demonstrates file transfer plus download of this API's cached original; it is not a method for attaching the returned `file_id` to a chat message.

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

The database path defaults to `~/.local/state/metaai-api/state.sqlite3`; set `META_AI_STATE_DB` to change it. Docker Compose uses a named volume for this database. Cached original uploads are no longer available from the API after 24 hours.

## Media fetching

Meta's current web app resolves generated media through its paginated `MediaLibraryFeed` query; the older `imagineCardMedia` GraphQL field is no longer available. `POST /media` and the download route now accept a media-library item ID, the numeric ID embedded in that item's CDN filename, or its external conversation ID. A media-library item ID is the most reliable choice. The lookup scans a bounded number of recent pages; an older item beyond that window may not be found.

To download an image or video directly, use the item's type and the zero-based index (defaults to `IMAGE_CARD` and `0`):

```bash
curl -L 'http://localhost:8000/media/MEDIA_ITEM_ID/download?card_type=IMAGE_CARD&index=0' \
  -o generated-image.jpg

curl -L 'http://localhost:8000/media/MEDIA_ITEM_ID/download?card_type=VIDEO_CARD&index=0' \
  -o generated-video.mp4
```

The REST media-library lookup and generated-image download flow was Live E2E tested; media lookup and download logic also have Mock tests. The route streams only from HTTPS Meta CDN hosts. Video streaming is present in code, but video generation was unavailable in the live check, so video download is not claimed as Live E2E tested. For generated Canvas PDFs, use the document endpoints above; only PDF artifacts with an embedded PDF payload can be exported, while other artifact types return 404. Meta's separate [WhatsApp help page for file attachments](https://faq.whatsapp.com/812625134612934) lists formats and a 40 MB per-file cap for WhatsApp, but that is a WhatsApp feature and should not be assumed to describe `meta.ai`.

Meta's official [Meta AI help](https://www.meta.com/help/artificial-intelligence/943942350800511/) documents asking about photos, and its [text-document help](https://www.meta.com/help/artificial-intelligence/1319682792536795/) documents creating and editing text documents. These pages do not publish a supported `meta.ai` file-format list, upload API contract, maximum size, or document-download API. This project's `rupload` integration is unofficial and may change or reject particular file types.

Python SDK access to fetched URLs remains available:

```python
data = ai.fetch_card_media("MEDIA_ITEM_ID", card_type="IMAGE_CARD")
urls = ai.generation_api.extract_media_urls(data)
print(urls)
```
