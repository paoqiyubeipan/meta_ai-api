# Quick Start

## 1. Install

Clone this repository and install the API server with the Playwright backend:

```bash
git clone https://github.com/paoqiyubeipan/meta_ai-api.git
cd meta_ai-api
python -m pip install -e ".[api,playwright]"
python -m playwright install chromium
```

On Linux, if Chromium reports missing system libraries, run `python -m playwright install-deps chromium` (administrator privileges may be required).

## 2. Configure your Meta AI cookies

1. Sign in at <https://www.meta.ai/>.
2. Open browser developer tools → Application → Cookies → `https://www.meta.ai`.
3. Copy the `datr` and `ecto_1_sess` cookie values.

Set them as environment variables:

```bash
export META_AI_DATR="YOUR_DATR"
export META_AI_ECTO_1_SESS="YOUR_ECTO_1_SESS"
```

File uploads also require an `ecto1:` access token:

```bash
export META_AI_ACCESS_TOKEN="YOUR_ECTO1_ACCESS_TOKEN"
```

## 3. Start the API server

```bash
python -m metaai_api.api_server
```

The server listens on `http://localhost:8000` by default. `META_AI_API_KEY` is optional; set it if you want bearer-token protection on the REST API.

## 4. Send a text chat message

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"What is the capital of France?"}'
```

## 5. Generate images

Text-to-image:

```bash
curl -X POST http://localhost:8000/image \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a watercolor painting of a cat"}'
```

Image-to-image with a reference image:

```bash
curl -X POST http://localhost:8000/image/reference \
  -F 'prompt=Turn this into a watercolor illustration' \
  -F 'reference_image=@./reference.png'
```

## 6. Upload a file and download its cached original

```bash
curl -X POST http://localhost:8000/files \
  -F 'file=@./report.pdf'
```

The response contains a `file_id` and `download_url`. Download the exact original bytes retained by this API:

```bash
curl -L 'http://localhost:8000/files/FILE_ID' -o report-copy.pdf
```

The upload sends the file to Meta; the download route reads this API's local cache, which expires after 24 hours. This upload does **not** attach the file to a `/chat` message. The live end-to-end test covered a synthetic PDF; do not infer that every file type is accepted or readable by Meta.

## Verification notes

The examples show implemented routes, not a claim that every path has been live-tested. The current evidence distinguishes code implementation, Mock tests, and signed-in-account end-to-end tests in [README.md](README.md#current-verification-status) and [GENERATION_API.md](GENERATION_API.md#verification-labels).
