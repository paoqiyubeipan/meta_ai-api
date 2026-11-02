"""FastAPI server for Meta AI — exposes the SDK as a REST API.

Endpoints match the original metaai-api repo:
    GET  /healthz              — health check
    POST /chat                 — send a chat message
    POST /image                — generate image from text/reference
    POST /image/reference      — multipart reference-image generation
    POST /video                — text-to-video generation
    POST /video/async          — start asynchronous video generation
    GET  /video/jobs/{job_id}  — retrieve asynchronous job status
    POST /video/extend         — extend video (returns 501)
    POST /upload               — upload image to Meta rupload
    POST /files                — upload a generic file to Meta rupload
    GET  /conversations/{conversation_id}/documents — list generated documents
    GET  /conversations/{conversation_id}/documents/{index}/download — export a document
    GET  /conversations        — list conversations
    POST /media                — fetch media by card ID
    GET  /media/{card_id}/download — download generated media from Meta CDN
    POST /reset                — reset browser session

Run:
    META_AI_DATR=... META_AI_ECTO_1_SESS=... python -m metaai_api.api_server
"""
from __future__ import annotations

import logging
import mimetypes
import os
import re
import threading
import time
import uuid
from email.message import Message
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional
from urllib.parse import quote, unquote, urljoin, urlsplit
import requests
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from starlette.concurrency import run_in_threadpool

from . import __version__
from .main import MetaAI
from .exceptions import (
    AuthenticationError, BrowserNotInstalledError, GenerationError,
    DocumentExportError, MetaAIError,
    TimeoutError as MetaAITimeoutError, VideoUnavailableError,
)
from .documents import DocumentExporter
from .utils import DEFAULT_UA
from .image_inputs import (
    cleanup_reference_images,
    materialize_reference_images,
    materialize_uploaded_media_ids,
)
from .state_store import SQLiteStateStore

logger = logging.getLogger(__name__)

# ---- Pydantic models (matching original repo) ----

class ChatRequest(BaseModel):
    message: str
    stream: bool = False
    new_conversation: bool = False
    thinking_mode: bool = True
    media_ids: Optional[list] = None
    attachment_metadata: Optional[dict] = None

class ImageRequest(BaseModel):
    prompt: str
    new_conversation: bool = False
    media_ids: Optional[list[str]] = None
    attachment_metadata: Optional[dict] = None
    orientation: Optional[str] = None
    num_images: int = Field(1, ge=1, le=4)
    reference_image: Optional[str] = None
    reference_images: Optional[list[str]] = None
    image: Optional[str | list[str]] = None

class VideoRequest(BaseModel):
    prompt: str
    media_ids: Optional[list[str]] = None
    attachment_metadata: Optional[dict] = None
    auto_poll: bool = True
    max_poll_attempts: int = Field(15, ge=1, le=60)
    poll_wait_seconds: int = Field(3, ge=1, le=30)
    orientation: Optional[str] = None
    wait_before_poll: int = Field(10, ge=0, le=60)
    max_attempts: int = Field(30, ge=1, le=60)

class VideoExtendRequest(BaseModel):
    media_id: str
    source_media_url: Optional[str] = None
    conversation_id: Optional[str] = None
    auto_poll: bool = True
    max_poll_attempts: int = Field(15, ge=1, le=60)
    poll_wait_seconds: int = Field(3, ge=1, le=30)

class FetchMediaRequest(BaseModel):
    card_id: str
    card_type: str = Field("IMAGE_CARD", pattern="^(IMAGE_CARD|VIDEO_CARD)$")

class HealthResponse(BaseModel):
    status: str; version: str; browser_ready: bool


# ---- Client manager ----

class ClientManager:
    def __init__(self):
        self._client: Optional[MetaAI] = None
        self._lock = threading.Lock()

    def get_client(self) -> MetaAI:
        with self._lock:
            if self._client is None:
                logger.info("Initializing MetaAI client...")
                self._client = MetaAI()
                logger.info("MetaAI client ready")
            return self._client

    def reset(self):
        with self._lock:
            if self._client:
                try: self._client.close()
                except: pass
                self._client = None

client_manager = ClientManager()
video_jobs_lock = threading.Lock()
video_job_store = SQLiteStateStore()
active_video_jobs: set[str] = set()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_FILE_UPLOAD_BYTES = 10 * 1024 * 1024
MEDIA_DOWNLOAD_HOST_SUFFIXES = (".fbcdn.net", ".fbsbx.com", ".metaaiusercontent.com")


def _is_allowed_media_download_url(url: str) -> bool:
    """Only fetch HTTPS media from Meta's known CDN host families."""
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and any(hostname.endswith(suffix) for suffix in MEDIA_DOWNLOAD_HOST_SUFFIXES)
        )
    except ValueError:
        return False


def _open_meta_media_download(url: str):
    """Follow only validated HTTPS redirects, preventing the media proxy becoming SSRF."""
    current_url = url
    for _ in range(6):
        if not _is_allowed_media_download_url(current_url):
            raise HTTPException(status_code=502, detail="Meta returned an unsupported media host")
        try:
            response = requests.get(
                current_url, stream=True, timeout=(10, 60), allow_redirects=False
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Could not fetch Meta media: {exc}") from exc
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise HTTPException(status_code=502, detail="Meta media redirect had no location")
            current_url = urljoin(current_url, location)
            continue
        if response.status_code != 200:
            status = response.status_code
            response.close()
            raise HTTPException(status_code=502, detail=f"Meta media download returned HTTP {status}")
        return response
    raise HTTPException(status_code=502, detail="Too many redirects while fetching Meta media")


def _download_filename(response, source_url: str) -> str:
    disposition = Message()
    disposition["content-disposition"] = response.headers.get("Content-Disposition", "")
    filename = disposition.get_filename()
    if not filename:
        filename = Path(unquote(urlsplit(source_url).path)).name
    filename = Path(filename or "media").name.replace("\r", "").replace("\n", "")
    if not Path(filename).suffix:
        extension = mimetypes.guess_extension(response.headers.get("Content-Type", "").split(";")[0])
        if extension:
            filename += extension
    return filename or "media.bin"


def detect_image_mime(data: bytes) -> Optional[str]:
    """Recognize supported image signatures instead of trusting the file name."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def detect_upload_mime(filename: str, reported_mime_type: Optional[str]) -> str:
    """Prefer the filename's known MIME type and reject malformed client headers."""
    guessed = mimetypes.guess_type(filename)[0]
    if guessed:
        return guessed
    reported = (reported_mime_type or "").split(";", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", reported):
        return reported
    return "application/octet-stream"


def _video_job_worker(job_id: str) -> None:
    """Submit a video request once, then poll its durable conversation ID."""
    try:
        job = video_job_store.get_video_job(job_id)
        if not job:
            return
        request_data = job["request"]
        conversation_id = job.get("conversation_id")
        client = client_manager.get_client()
        if not conversation_id:
            video_job_store.update_video_job(job_id, status="submitting")
            started = client.generate_video_new(
                request_data["prompt"],
                auto_poll=False,
                orientation=request_data.get("orientation"),
                wait_before_poll=request_data.get("wait_before_poll", 10),
                max_attempts=request_data.get("max_attempts", 30),
                poll_wait_seconds=request_data.get("poll_wait_seconds", 3),
            )
            conversation_id = started.get("conversation_id")
            if not conversation_id:
                raise GenerationError("Meta AI did not return a video conversation ID")
            video_job_store.update_video_job(
                job_id, status="processing", conversation_id=conversation_id
            )
            initial_wait = int(request_data.get("wait_before_poll", 10))
            if initial_wait:
                time.sleep(initial_wait)
        else:
            video_job_store.update_video_job(job_id, status="processing")

        from .video_generation import VideoGenerator

        generator = VideoGenerator(cookies_dict=client.cookies)
        video_urls = generator.fetch_video_urls(
            conversation_id=conversation_id,
            max_attempts=request_data.get("max_attempts", 30),
            wait_seconds=request_data.get("poll_wait_seconds", 3),
            verbose=False,
        )
        if not video_urls:
            video_job_store.update_video_job(
                job_id,
                status="failed",
                error="Meta AI did not return a video URL within the polling limit.",
            )
            return
        result = {
            "success": True,
            "conversation_id": conversation_id,
            "prompt": request_data["prompt"],
            "video_urls": video_urls,
        }
        video_job_store.update_video_job(job_id, status="completed", result=result)
    except VideoUnavailableError as exc:
        video_job_store.update_video_job(
            job_id,
            status="failed",
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("Video job %s failed", job_id)
        try:
            video_job_store.update_video_job(job_id, status="failed", error=str(exc))
        except Exception:
            logger.exception("Could not persist failure state for video job %s", job_id)


def _launch_video_job(job_id: str) -> None:
    with video_jobs_lock:
        if job_id in active_video_jobs:
            return
        active_video_jobs.add(job_id)

    def run():
        try:
            _video_job_worker(job_id)
        finally:
            with video_jobs_lock:
                active_video_jobs.discard(job_id)

    threading.Thread(target=run, daemon=True).start()


def _resume_video_jobs() -> None:
    for job in video_job_store.get_recoverable_video_jobs():
        _launch_video_job(job["job_id"])


# ---- Auth ----

def verify_api_key(authorization: Optional[str] = Header(None)):
    expected_key = os.getenv("META_AI_API_KEY")
    if not expected_key:
        return True
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    token = authorization[7:] if authorization.startswith("Bearer ") else authorization
    if token != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key.")
    return True


def handle_error(e: Exception, timeout: int = 60):
    if isinstance(e, AuthenticationError):
        client_manager.reset()
        raise HTTPException(status_code=401, detail="Meta AI cookies expired.")
    if isinstance(e, MetaAITimeoutError):
        raise HTTPException(status_code=504, detail=f"Timed out after {timeout}s")
    if isinstance(e, VideoUnavailableError):
        raise HTTPException(
            status_code=503,
            detail=(
                "Meta AI reports that video generation is not available for this account. "
                "The account can still generate images (including reference-image generation)."
            ),
        )
    if isinstance(e, DocumentExportError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, GenerationError):
        raise HTTPException(status_code=502, detail=str(e))
    if isinstance(e, BrowserNotInstalledError):
        raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


# ---- App ----

@asynccontextmanager
async def api_lifespan(_app):
    """Resume durable jobs on startup and close the browser client on shutdown."""
    _resume_video_jobs()
    try:
        yield
    finally:
        client_manager.reset()


app = FastAPI(
    title="MetaAI API",
    description="Unofficial API server for Meta AI — image generation, chat, and more.",
    version=__version__,
    docs_url="/docs",
    lifespan=api_lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/healthz", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(status="ok", version=__version__,
                          browser_ready=client_manager._client is not None)


@app.post("/chat")
async def chat(req: ChatRequest, _=Depends(verify_api_key)):
    """Send a chat message to Meta AI."""
    start = time.time()
    try:
        client = client_manager.get_client()
        result = await run_in_threadpool(
            lambda: client.prompt(
                req.message,
                stream=req.stream,
                new_conversation=req.new_conversation,
                thinking_mode=req.thinking_mode,
                media_ids=req.media_ids,
                attachment_metadata=req.attachment_metadata,
            )
        )
        return {
            "success": True,
            "message": result.get("message", ""),
            "conversation_id": result.get("conversation_id"),
            "elapsed_seconds": round(time.time()-start, 1),
        }
    except Exception as e:
        handle_error(e, 60)


@app.post("/image")
async def generate_image(req: ImageRequest, _=Depends(verify_api_key)):
    """Generate an image using inline or locally uploaded reference images."""
    start = time.time()
    refs = list(req.reference_images or [])
    if req.reference_image:
        refs.append(req.reference_image)
    if isinstance(req.image, str):
        refs.append(req.image)
    elif req.image:
        refs.extend(req.image)
    if len(refs) + len(req.media_ids or []) > 4:
        raise HTTPException(status_code=400, detail="At most four reference images are supported")
    ref_paths = []
    uploaded_paths = []
    try:
        ref_paths = materialize_reference_images(refs)
        uploaded_paths = materialize_uploaded_media_ids(req.media_ids or [], video_job_store)
    except ValueError as e:
        cleanup_reference_images(ref_paths)
        raise HTTPException(status_code=400, detail=str(e)) from e
    all_ref_paths = ref_paths + uploaded_paths
    try:
        client = client_manager.get_client()
        result = await run_in_threadpool(
            client.generate_image_new,
            req.prompt,
            orientation=req.orientation or "VERTICAL",
            num_images=req.num_images,
            reference_images=[str(path) for path in all_ref_paths],
            timeout=120,
        )
        return {
            "success": result["success"],
            "image_urls": result.get("image_urls", []),
            "conversation_id": result.get("conversation_id"),
            "prompt": result.get("prompt", ""),
            "elapsed_seconds": round(time.time()-start, 1),
        }
    except Exception as e:
        handle_error(e, 120)
    finally:
        cleanup_reference_images(ref_paths)
        cleanup_reference_images(uploaded_paths)


@app.post("/image/reference")
async def generate_image_with_reference(
    prompt: str = Form(...),
    reference_image: UploadFile = File(...),
    orientation: str = Form("VERTICAL"),
    _=Depends(verify_api_key),
):
    """Generate from one multipart reference image using the browser composer."""
    start = time.time()
    data = await reference_image.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Reference image must be 10 MiB or smaller")
    mime_type = detect_image_mime(data)
    if not mime_type:
        raise HTTPException(status_code=415, detail="Unsupported or invalid image file")
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[mime_type]
    temp_path = None
    try:
        with NamedTemporaryFile(prefix="metaai-ref-", suffix=suffix, delete=False) as temp:
            temp.write(data)
            temp_path = Path(temp.name)
        result = await run_in_threadpool(
            client_manager.get_client().generate_image_new,
            prompt,
            orientation=orientation,
            reference_images=[str(temp_path)],
            timeout=120,
        )
        return {
            "success": result["success"],
            "image_urls": result.get("image_urls", []),
            "conversation_id": result.get("conversation_id"),
            "prompt": result.get("prompt", prompt),
            "elapsed_seconds": round(time.time() - start, 1),
        }
    except Exception as e:
        handle_error(e, 120)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


@app.post("/video")
async def generate_video(req: VideoRequest, _=Depends(verify_api_key)):
    """Generate a text-to-video clip; returns 503 when Meta AI declines video generation."""
    if req.media_ids or req.attachment_metadata:
        raise HTTPException(
            status_code=501,
            detail="Image-to-video is not implemented: Meta AI exposes no capturable image-to-video request.",
        )
    start = time.time()
    try:
        result = client_manager.get_client().generate_video_new(
            req.prompt,
            auto_poll=req.auto_poll,
            max_poll_attempts=req.max_poll_attempts,
            poll_wait_seconds=req.poll_wait_seconds,
            wait_before_poll=req.wait_before_poll,
            max_attempts=req.max_attempts,
            orientation=req.orientation,
        )
        return {**result, "elapsed_seconds": round(time.time() - start, 1)}
    except Exception as e:
        handle_error(e, 180)


@app.post("/video/async")
async def generate_video_async(req: VideoRequest, _=Depends(verify_api_key)):
    """Start a durable text-to-video job and return its job ID."""
    if req.media_ids or req.attachment_metadata:
        raise HTTPException(
            status_code=501,
            detail="Image-to-video is not implemented: Meta AI exposes no capturable image-to-video request.",
        )
    job_id = str(uuid.uuid4())
    request_data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    video_job_store.create_video_job(job_id, request_data)
    _launch_video_job(job_id)
    return {"success": True, "job_id": job_id, "status": "queued"}


@app.get("/video/jobs/{job_id}")
async def video_job_status(job_id: str, _=Depends(verify_api_key)):
    """Return persisted status and result for an asynchronous video job."""
    job = video_job_store.get_video_job(job_id)
    if job:
        return {
            key: job[key]
            for key in ("job_id", "status", "conversation_id", "result", "error", "created_at", "updated_at")
        }
    raise HTTPException(status_code=404, detail="Video job not found")


@app.post("/video/extend")
async def video_extend(req: VideoExtendRequest, _=Depends(verify_api_key)):
    """Return a clear error until the Meta AI extension request can be captured."""
    raise HTTPException(status_code=501,
        detail="Video extension exists in Meta AI's app, but this API has no verified request flow yet.")


@app.post("/upload")
async def upload_image(file: UploadFile = File(...), _=Depends(verify_api_key)):
    """Upload an image directly to Meta AI's rupload service."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image must be 10 MiB or smaller")
    mime_type = detect_image_mime(data)
    if not mime_type:
        raise HTTPException(status_code=415, detail="Unsupported or invalid image file")
    client = client_manager.get_client()
    if not client.access_token:
        raise HTTPException(
            status_code=503,
            detail="Image upload needs META_AI_ACCESS_TOKEN (an ecto1: token).",
    )
    try:
        result = client.upload_image(data, Path(file.filename or "image").name, mime_type)
        media_id = result.get("media_id")
        if not media_id:
            raise GenerationError("Meta AI upload completed without returning a media_id")
        video_job_store.save_uploaded_image(
            media_id, data, mime_type, Path(file.filename or "image").name
        )
        return {
            "success": True,
            "media_id": media_id,
            "upload_session_id": result.get("upload_session_id"),
            "file_name": result.get("file_name"),
            "file_size": result.get("file_size"),
            "mime_type": result.get("mime_type"),
        }
    except Exception as e:
        handle_error(e, 60)


@app.post("/files")
async def upload_file(file: UploadFile = File(...), _=Depends(verify_api_key)):
    """Upload a generic file to Meta AI without retaining a local copy."""
    data = await file.read(MAX_FILE_UPLOAD_BYTES + 1)
    if len(data) > MAX_FILE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File must be 10 MiB or smaller")
    if not data:
        raise HTTPException(status_code=400, detail="File is empty")
    filename = (file.filename or "upload.bin").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename:
        filename = "upload.bin"
    mime_type = detect_upload_mime(filename, file.content_type)
    client = client_manager.get_client()
    if not client.access_token:
        raise HTTPException(
            status_code=503,
            detail="File upload needs META_AI_ACCESS_TOKEN (an ecto1: token).",
        )
    try:
        result = await run_in_threadpool(client.upload_file, data, filename, mime_type)
        file_id = result.get("media_id")
        if not file_id:
            raise GenerationError("Meta AI upload completed without returning a media_id")
        return {
            "success": True,
            "file_id": file_id,
            "media_id": file_id,
            "upload_session_id": result.get("upload_session_id"),
            "file_name": filename,
            "file_size": len(data),
            "mime_type": mime_type,
        }
    except Exception as e:
        handle_error(e, 60)


@app.get("/conversations")
async def list_conversations(_=Depends(verify_api_key)):
    """List all conversations."""
    try:
        client = client_manager.get_client()
        convs = await run_in_threadpool(client.list_conversations)
        return {"success": True, "conversations": convs}
    except Exception as e:
        handle_error(e, 10)


def _document_exporter(client) -> DocumentExporter:
    """Build a document exporter that reuses the signed-in API session."""
    return DocumentExporter(
        session=client.client.session,
        cookies=client.cookies,
        access_token=getattr(client, "access_token", None),
        user_agent=getattr(client, "user_agent", None) or DEFAULT_UA,
    )


@app.get("/conversations/{conversation_id}/documents")
async def list_generated_documents(conversation_id: str, _=Depends(verify_api_key)):
    """List PDF documents Meta AI generated inside one conversation."""
    try:
        client = client_manager.get_client()
        documents = await run_in_threadpool(
            _document_exporter(client).list_documents, conversation_id
        )
        exportable = [doc for doc in documents if doc.get("file_extension") == "pdf"]
        return {
            "success": True,
            "conversation_id": conversation_id,
            "documents": [
                {
                    "index": position,
                    "file_name": doc["file_name"],
                    "mime_type": doc["mime_type"],
                    "file_extension": doc["file_extension"],
                    "artifact_uuid": doc["artifact_uuid"],
                    "asset_id": doc["asset_id"],
                    "artifact_type": doc["artifact_type"],
                    "downloadable": "CAN_DOWNLOAD" in doc["viewer_actions"],
                    "download_url": (
                        f"/conversations/{quote(conversation_id, safe='')}"
                        f"/documents/{position}/download"
                    ),
                }
                for position, doc in enumerate(exportable)
            ],
        }
    except Exception as e:
        handle_error(e, 30)


@app.get("/conversations/{conversation_id}/documents/{index}/download")
async def download_generated_document(
    conversation_id: str, index: int, _=Depends(verify_api_key)
):
    """Export a Meta AI generated document as its original PDF bytes."""
    if index < 0:
        raise HTTPException(status_code=404, detail="Document index must be zero or higher")
    try:
        client = client_manager.get_client()
        result = await run_in_threadpool(
            _document_exporter(client).download, conversation_id, index
        )
    except Exception as e:
        handle_error(e, 60)
    filename = Path(result["file_name"] or "generated.pdf").name
    filename = filename.replace("\r", "").replace("\n", "")
    if not Path(filename).suffix:
        filename += ".pdf"
    return Response(
        content=result["file_bytes"],
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@app.post("/media")
async def fetch_media(req: FetchMediaRequest, _=Depends(verify_api_key)):
    """Fetch media URLs by card ID."""
    try:
        client = client_manager.get_client()
        data = client.fetch_card_media(req.card_id, req.card_type)
        urls = client.generation_api.extract_media_urls(data)
        return {
            "success": True,
            "urls": urls,
            "card_id": req.card_id,
            "card_type": req.card_type,
        }
    except Exception as e:
        handle_error(e, 30)


@app.get("/media/{card_id}/download")
def download_generated_media(
    card_id: str,
    card_type: str = Query("IMAGE_CARD", pattern="^(IMAGE_CARD|VIDEO_CARD)$"),
    index: int = Query(0, ge=0, le=20),
    _=Depends(verify_api_key),
):
    """Resolve generated media by card ID and stream one item from Meta's CDN."""
    try:
        client = client_manager.get_client()
        data = client.fetch_card_media(card_id, card_type)
        urls = client.generation_api.extract_media_urls(data)
    except Exception as e:
        handle_error(e, 30)
    if index >= len(urls):
        raise HTTPException(status_code=404, detail="Media item not found for this card")
    source_url = urls[index]
    remote = _open_meta_media_download(source_url)
    filename = _download_filename(remote, source_url)
    content_type = remote.headers.get("Content-Type", "application/octet-stream").split(";")[0]
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    if remote.headers.get("Content-Length"):
        headers["Content-Length"] = remote.headers["Content-Length"]

    def chunks():
        try:
            yield from remote.iter_content(chunk_size=64 * 1024)
        finally:
            remote.close()

    return StreamingResponse(chunks(), media_type=content_type, headers=headers)


@app.post("/reset")
async def reset_client(_=Depends(verify_api_key)):
    """Reset the browser session."""
    client_manager.reset()
    return {"status": "reset", "message": "Browser will restart on next request"}


def main():
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    if not os.getenv("META_AI_DATR") or not os.getenv("META_AI_ECTO_1_SESS"):
        print("ERROR: Set META_AI_DATR and META_AI_ECTO_1_SESS environment variables.")
        print("\nGet them from:")
        print("  1. Open https://www.meta.ai/ and log in")
        print("  2. DevTools → Application → Cookies")
        print("  3. Copy 'datr' and 'ecto_1_sess' values")
        raise SystemExit(1)
    print(f"Starting MetaAI API server on {host}:{port}")
    print(f"  Docs: http://localhost:{port}/docs")
    print(f"  API key: {'enabled' if os.getenv('META_AI_API_KEY') else 'disabled'}")
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
