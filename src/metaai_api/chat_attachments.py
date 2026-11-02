"""Short-lived uploaded-file references used by the browser-backed chat flow.

Meta's rupload ID cannot be turned back into file bytes by this API. Keep a
bounded, in-memory copy for the lifetime of the API process so a later /chat
request can attach the same bytes through Meta AI's supported web composer.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MAX_CHAT_ATTACHMENTS = 4
MAX_CHAT_ATTACHMENT_BYTES = 10 * 1024 * 1024


class UploadedFileCache:
    """Thread-safe, process-local cache with TTL and aggregate size limits."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 24 * 60 * 60,
        max_entries: int = 16,
        max_total_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes
        self._entries: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.RLock()

    def _remove_expired(self, now: float) -> None:
        expired = [
            media_id for media_id, entry in self._entries.items()
            if now - entry["created_at"] >= self.ttl_seconds
        ]
        for media_id in expired:
            self._entries.pop(media_id, None)

    def _total_bytes(self) -> int:
        return sum(entry["file_size"] for entry in self._entries.values())

    def save(self, media_id: str, file_bytes: bytes, mime_type: str, file_name: str) -> None:
        target = str(media_id).strip()
        if not target:
            raise ValueError("media_id must not be empty")
        data = bytes(file_bytes)
        if not data or len(data) > MAX_CHAT_ATTACHMENT_BYTES:
            raise ValueError("Uploaded file is empty or exceeds 10 MiB")
        name = Path(str(file_name).replace("\\", "/")).name or "upload.bin"
        with self._lock:
            now = time.monotonic()
            self._remove_expired(now)
            self._entries.pop(target, None)
            while self._entries and (
                len(self._entries) >= self.max_entries
                or self._total_bytes() + len(data) > self.max_total_bytes
            ):
                self._entries.popitem(last=False)
            if len(data) > self.max_total_bytes:
                raise ValueError("Uploaded file exceeds the chat attachment cache limit")
            self._entries[target] = {
                "file_bytes": data,
                "mime_type": str(mime_type),
                "file_name": name,
                "file_size": len(data),
                "created_at": now,
            }

    def get(self, media_id: str) -> Optional[Dict[str, Any]]:
        target = str(media_id).strip()
        with self._lock:
            now = time.monotonic()
            self._remove_expired(now)
            entry = self._entries.get(target)
            if entry is None:
                return None
            self._entries.move_to_end(target)
            return {
                "file_bytes": entry["file_bytes"],
                "mime_type": entry["mime_type"],
                "file_name": entry["file_name"],
                "file_size": entry["file_size"],
            }


def _safe_local_name(value: Any, index: int) -> str:
    name = Path(str(value or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name).strip(" .")
    name = name[:160]
    return name or "attachment-{}.bin".format(index + 1)


def _lookup_upload(media_id: str, file_cache: UploadedFileCache, image_store) -> Optional[Dict[str, Any]]:
    uploaded = file_cache.get(media_id)
    if uploaded:
        return uploaded
    if image_store is not None:
        image = image_store.get_uploaded_image(media_id)
        if image:
            return {
                "file_bytes": image["image_bytes"],
                "mime_type": image["mime_type"],
                "file_name": image["file_name"],
                "file_size": len(image["image_bytes"]),
            }
    return None


def materialize_chat_attachments(
    media_ids: Iterable[str],
    file_cache: UploadedFileCache,
    image_store=None,
) -> List[Path]:
    """Resolve locally uploaded IDs into secure temporary files for the UI."""
    ids = [item.strip() for item in media_ids if isinstance(item, str) and item.strip()]
    if len(ids) > MAX_CHAT_ATTACHMENTS:
        raise ValueError("At most four chat attachments are supported")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate chat attachment IDs are not allowed")
    if not ids:
        return []

    root = Path(tempfile.mkdtemp(prefix="metaai-chat-attachments-"))
    paths: List[Path] = []
    try:
        os.chmod(root, 0o700)
        for index, media_id in enumerate(ids):
            uploaded = _lookup_upload(media_id, file_cache, image_store)
            if not uploaded:
                raise ValueError(
                    "No active local upload matches media_id {!r}; upload the file through this API instance first".format(media_id)
                )
            data = uploaded["file_bytes"]
            if not data or len(data) > MAX_CHAT_ATTACHMENT_BYTES:
                raise ValueError("Chat attachment is empty or exceeds 10 MiB")
            filename = _safe_local_name(uploaded.get("file_name"), index)
            path = root / ("{}-{}".format(index + 1, filename))
            with path.open("xb") as output:
                os.chmod(path, 0o600)
                output.write(data)
            paths.append(path)
        return paths
    except Exception:
        cleanup_chat_attachments(paths, tmpdir=root)
        raise


def cleanup_chat_attachments(paths: Iterable[Path], *, tmpdir: Optional[Path] = None) -> None:
    """Remove temporary copies created for one chat request."""
    path_list = list(paths)
    root = tmpdir or (path_list[0].parent if path_list else None)
    for path in path_list:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if root and root.name.startswith("metaai-chat-attachments-"):
        try:
            root.rmdir()
        except OSError:
            pass
