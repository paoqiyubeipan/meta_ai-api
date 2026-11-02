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

**Implemented** means the code path exists. **Mock tests pass** means automated tests used fake Meta/network responses; it is not proof that the live service accepted the request. **Live E2E tested** means the whole flow was exercised against a signed-in Meta AI account. Current live checks cover text-to-image, direct reference-image generation, the `/upload` → `media_id` → `/image` reference chain, text chat (with mixed cold-start results below), health and conversation listing, one synthetic PDF upload to Meta, generated-image download from Meta's media library, and export of Meta-generated PDF, DOCX and TXT files. Other routes and file formats must not be assumed live-tested.

## Text chat

`POST /chat` and `ai.prompt()` are implemented. Repeated live cold-start checks produced inconsistent results. After adding bounded UI-readiness waits (up to about 2 seconds for a verifiable composer and 5 seconds for the Send button), one cohort of three separate server/browser processes still returned HTTP 500 on all three first requests. The sanitized category was `ConnectionError` from the browser UI: the Send button stayed disabled. Two same-instance retries returned 500; the third received HTTP 200, but the test harness's local answer validator then raised `TypeError`, so correctness for that retry was not recorded. A later, corrected cohort of three independent server/browser cold starts returned HTTP 200 with exact answers to random arithmetic on all three first requests. The changing outcome means cold-start reliability remains unproven; the UI wait is a mitigation, not a verified root-cause fix. The synchronous Playwright call is offloaded to a worker thread, but that change alone did not explain the UI state. Do not treat Mock tests as proof of live reliability.

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is 12 squared?"}'
```

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

`POST /files` sends the bytes through the reverse-engineered Meta `rupload` service with the `genai_document` handler and a MIME type inferred from the filename (or supplied by the multipart client). The API code can submit files such as PDF, DOC/DOCX, TXT, CSV, JSON, XLS/XLSX, PPT/PPTX and ZIP, up to **10 MiB per request**. This list describes inputs the code can attempt to send; it is not a Meta-supported-format list. A synthetic PDF was uploaded to Meta in a Live E2E test; other formats have not been live-verified through this endpoint. The 10 MiB size is this API's safety cap, not a published Meta AI limit. Meta's acceptance and ability to read a given file remain account-, region- and upstream-dependent. This route does not attach the returned ID to a chat turn. It returns Meta's `file_id`/`media_id` and upload metadata only; it does not retain a local copy and there is no `GET /files/{file_id}` route.

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

Choose the output extension from the listed `file_name`/`file_extension` (for example, `generated.docx` or `generated.txt`); the download response includes the matching MIME type. The conversation page and signed viewer are fetched with the API session cookies, so no browser is involved. A conversation with no supported artifacts returns **404**. This generated-document export is separate from generic file upload: uploaded files are not retained locally or downloadable through `/files`.

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
# response includes Meta's file_id/media_id and upload metadata; no local copy is kept
```

This example uploads to Meta; it is not a method for attaching the returned `file_id` to a chat message. Keep your own local original if you need it later.

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
