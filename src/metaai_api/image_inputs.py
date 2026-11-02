"""Helpers for materializing reference images supplied as data URLs."""
from __future__ import annotations

import base64
import binascii
import re
import tempfile
from pathlib import Path
from typing import Iterable

DATA_URL_RE = re.compile(r"^data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)$", re.I)
MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


def _sniff_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def materialize_reference_images(references: Iterable[str]) -> list[Path]:
    """Decode image data URLs into temporary files for browser upload.

    Remote URLs and server-local paths are intentionally rejected: the REST
    endpoint accepts inline data only, avoiding SSRF and local-file disclosure.
    """
    refs = [ref.strip() for ref in references if isinstance(ref, str) and ref.strip()]
    if not refs:
        return []
    tmpdir = Path(tempfile.mkdtemp(prefix="metaai-ref-"))
    paths: list[Path] = []
    try:
        for index, ref in enumerate(refs):
            match = DATA_URL_RE.fullmatch(ref)
            if not match:
                raise ValueError("Reference images must be data:image/{png,jpeg,webp,gif};base64,... URLs")
            mime = match.group(1).lower()
            try:
                encoded = "".join(match.group(2).split())
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Reference image has invalid base64 data") from exc
            if not data:
                raise ValueError("Reference image is empty")
            if len(data) > MAX_REFERENCE_IMAGE_BYTES:
                raise ValueError("Each reference image must be 10 MiB or smaller")
            if _sniff_mime(data) != mime:
                raise ValueError("Reference image content does not match its data URL MIME type")
            path = tmpdir / f"reference_{index}{_EXTENSIONS[mime]}"
            path.write_bytes(data)
            paths.append(path)
        return paths
    except Exception:
        cleanup_reference_images(paths, tmpdir=tmpdir)
        raise


def materialize_uploaded_media_ids(media_ids: Iterable[str], store) -> list[Path]:
    """Materialize images uploaded through this API for browser-composer use."""
    ids = [media_id.strip() for media_id in media_ids if isinstance(media_id, str) and media_id.strip()]
    if not ids:
        return []
    if len(ids) > 4:
        raise ValueError("At most four reference images are supported")
    tmpdir = Path(tempfile.mkdtemp(prefix="metaai-ref-"))
    paths: list[Path] = []
    try:
        for index, media_id in enumerate(ids):
            uploaded = store.get_uploaded_image(media_id)
            if not uploaded:
                raise ValueError(
                    "No locally cached upload matches a supplied media_id; upload the image through this API first"
                )
            data = uploaded["image_bytes"]
            mime = uploaded["mime_type"]
            if not data or len(data) > MAX_REFERENCE_IMAGE_BYTES:
                raise ValueError("Uploaded reference image is empty or exceeds 10 MiB")
            if _sniff_mime(data) != mime:
                raise ValueError("Cached upload image content does not match its MIME type")
            path = tmpdir / f"uploaded_{index}{_EXTENSIONS[mime]}"
            path.write_bytes(data)
            paths.append(path)
        return paths
    except Exception:
        cleanup_reference_images(paths, tmpdir=tmpdir)
        raise


def cleanup_reference_images(paths: Iterable[Path], *, tmpdir: Path | None = None) -> None:
    """Remove temporary reference files created by this module."""
    paths = list(paths)
    root = tmpdir or (paths[0].parent if paths else None)
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if root and root.name.startswith("metaai-ref-"):
        try:
            root.rmdir()
        except OSError:
            pass
