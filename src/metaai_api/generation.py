"""Image and video generation API for Meta AI.

Based on captured network requests from meta.ai. Uses both browser
automation (primary) and DGW WebSocket (advanced) methods.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .browser import BrowserBackend
from .client import MetaAIClient
from .exceptions import GenerationError
from .utils import DEFAULT_UA, is_media_url, logger

# Persisted-query doc_ids (discovered in Meta's web JS)
FETCH_IMAGINE_CARD_MEDIA_DOC_ID = "344570a4b8110dd9848829731d35c74a"
MEDIA_LIBRARY_FEED_DOC_ID = "f9479f94c56a5c5ef75159a4efc2914e"
MEDIA_LIBRARY_PAGE_SIZE = 20
MEDIA_LIBRARY_MAX_PAGES = 20
VIDEO_POLLING_SUBSCRIPTION_DOC_ID = "9928a9b87ec492a16326f18925191c0f"


class GenerationAPI:
    """
    Image and Video Generation API based on Meta AI GraphQL patterns.

    This class provides both browser-based (primary) and WebSocket-based
    (advanced) methods for generating images and fetching media.
    """

    ENDPOINT = "https://www.meta.ai/api/graphql"

    def __init__(self, session=None, cookies: Optional[Dict] = None,
                 access_token: Optional[str] = None,
                 browser: Optional[BrowserBackend] = None,
                 user_agent: str = DEFAULT_UA):
        """Initialize Generation API.

        Args:
            session: Optional requests.Session (legacy compatibility).
            cookies: Optional cookies dictionary.
            access_token: Optional DGW access token for WebSocket method.
            browser: Optional BrowserBackend instance for browser method.
        """
        self.session = session
        self.cookies = cookies or {}
        self.access_token = access_token
        self.browser = browser
        self.user_agent = user_agent

        if session is None and cookies:
            import requests
            self.session = requests.Session()
            self.session.cookies.update(cookies)

        self.logger = logging.getLogger(__name__)

    def generate_image(self, prompt: str, timeout: int = 120,
                       reference_images: Optional[List[str]] = None) -> Dict[str, Any]:
        """Generate an image using the browser method.

        Args:
            prompt: Text description of the image.
            timeout: Max seconds to wait.

        Returns:
            Response dict with 'images', 'success', 'prompt', etc.
        """
        if not self.browser:
            raise GenerationError("Browser backend is required for image generation")

        if reference_images:
            send_with_images = getattr(self.browser, "send_message_with_images", None)
            if not send_with_images:
                raise GenerationError(
                    "Reference images require the Playwright browser backend."
                )
            result = send_with_images(prompt, reference_images, timeout=timeout)
        else:
            send_for_media = getattr(self.browser, "send_message_for_media", None)
            if callable(send_for_media):
                result = send_for_media(prompt, timeout=timeout)
            else:
                result = self.browser.send_message(prompt, timeout=timeout)
        urls = result.get("urls", [])
        text = result.get("text", "")

        if not urls:
            raise GenerationError(
                "No image URLs returned.",
                detail=f"Prompt: {prompt!r}, text: {text[:200]}",
            )

        return {
            "success": True,
            "prompt": prompt,
            "image_urls": urls,
            "images": urls,
            "conversation_id": result.get("conversation_id"),
            "raw": result,
        }

    def generate_image_new(self, prompt: str, orientation: str = "VERTICAL",
                           num_images: int = 1, **kwargs) -> Dict[str, Any]:
        """Generate images using the browser method (new API).

        Args:
            prompt: Text description of the image.
            orientation: Image orientation (VERTICAL, HORIZONTAL, SQUARE).
            num_images: Number of images (browser typically generates 1-4).
            **kwargs: Additional parameters.

        Returns:
            Dictionary with response data and extracted image URLs.
        """
        return self.generate_image(
            prompt,
            timeout=kwargs.get("timeout", 120),
            reference_images=kwargs.get("reference_images"),
        )

    def generate_video(self, prompt: str, timeout: int = 180,
                       **kwargs) -> Dict[str, Any]:
        """Generate text-to-video through the captured Meta GraphQL flow.

        The current request format does not support image-to-video attachments.
        """
        if kwargs.get("media_ids") or kwargs.get("attachment_metadata"):
            raise GenerationError("Image-to-video attachments are not supported")
        from .video_generation import VideoGenerator

        generator = VideoGenerator(cookies_dict=self.cookies)
        if not kwargs.get("auto_poll", True):
            conversation_id = generator.create_video_generation_request(
                prompt_text=prompt,
                orientation=kwargs.get("orientation"),
                verbose=False,
            )
            if not conversation_id:
                raise GenerationError("Meta AI did not accept the video request")
            return {
                "success": True,
                "processing": True,
                "conversation_id": conversation_id,
                "prompt": prompt,
                "video_urls": [],
            }
        poll_wait = max(1, int(kwargs.get("poll_wait_seconds", 3)))
        result = generator.generate_video(
            prompt=prompt,
            orientation=kwargs.get("orientation"),
            wait_before_poll=kwargs.get("wait_before_poll", 10),
            max_attempts=kwargs.get("max_attempts", max(1, timeout // poll_wait)),
            wait_seconds=poll_wait,
            verbose=False,
        )
        if not result.get("success"):
            raise GenerationError(result.get("error", "Video generation failed"))
        return result

    def fetch_media_library_page(
        self,
        *,
        after: Optional[str] = None,
        first: int = MEDIA_LIBRARY_PAGE_SIZE,
        filter_value: Optional[str] = None,
        search_query: Optional[str] = None,
        shape: str = "v2",
    ) -> Dict[str, Any]:
        """Read one page using the persisted query used by Meta's media library."""
        if not self.session:
            raise GenerationError("No session available for HTTP requests")
        page_size = max(1, min(int(first), 50))
        try:
            response = self.session.post(
                self.ENDPOINT,
                json={
                    "doc_id": MEDIA_LIBRARY_FEED_DOC_ID,
                    "variables": {
                        "after": after,
                        "filter": filter_value,
                        "first": page_size,
                        "searchQuery": search_query,
                        "shape": shape,
                    },
                },
                headers={
                    "User-Agent": self.user_agent,
                    "Content-Type": "application/json",
                    "Origin": "https://www.meta.ai",
                    "Referer": "https://www.meta.ai/media/",
                    **({"Authorization": f"ecto1:{self.access_token}"} if self.access_token else {}),
                },
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise GenerationError(f"Meta media library request failed: {exc}") from exc

        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            message = errors[0].get("message", "GraphQL query failed") if isinstance(errors[0], dict) else str(errors[0])
            raise GenerationError(f"Meta media library query failed: {message}")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("mediaLibraryFeed"), dict):
            raise GenerationError("Meta media library returned no media feed")
        return body

    @staticmethod
    def _feed_items(node: Dict[str, Any], card_type: str) -> List[Dict[str, Any]]:
        field = "images" if card_type == "IMAGE_CARD" else "videos"
        items = node.get(field)
        if isinstance(items, list) and items:
            return [item for item in items if isinstance(item, dict)]
        renderer = node.get("contentRenderer") or {}
        content = renderer.get("mediaContent") or {}
        rendered = content.get("media") or []
        expected = "GeneratedImage" if card_type == "IMAGE_CARD" else "GeneratedVideo"
        return [
            item for item in rendered
            if isinstance(item, dict) and item.get("__typename") == expected
        ]

    def find_media_library_items(
        self,
        media_id: str,
        card_type: str = "IMAGE_CARD",
        *,
        max_pages: int = MEDIA_LIBRARY_MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Resolve a current media item, CDN filename ID, or conversation ID.

        Meta's old imagineCardMedia GraphQL field no longer exists. The web app
        now resolves downloadable assets from MediaLibraryFeed, so scan a bounded
        number of its newest pages and match supported IDs from those records.
        """
        if card_type not in {"IMAGE_CARD", "VIDEO_CARD"}:
            raise GenerationError("card_type must be IMAGE_CARD or VIDEO_CARD")
        target = str(media_id).strip()
        if not target:
            return []
        normalized_target = target.rsplit(":", 1)[-1]
        direct_matches: List[Dict[str, Any]] = []
        conversation_matches: List[Dict[str, Any]] = []
        cursor = None
        seen_cursors = set()

        for _ in range(max(1, min(int(max_pages), MEDIA_LIBRARY_MAX_PAGES))):
            page = self.fetch_media_library_page(after=cursor, first=MEDIA_LIBRARY_PAGE_SIZE)
            feed = page["data"]["mediaLibraryFeed"]
            for edge in feed.get("edges") or []:
                node = (edge or {}).get("node") or {}
                if not isinstance(node, dict):
                    continue
                node_ids = {str(node.get("id") or ""), str(node.get("conversationId") or "")}
                for item in self._feed_items(node, card_type):
                    url = item.get("url") or item.get("fallbackUrl") or ""
                    path = urlsplit(url).path if isinstance(url, str) else ""
                    path_ids = set(re.findall(r"(?<!\d)\d{9,22}(?!\d)", path))
                    item_ids = {
                        str(item.get("id") or ""),
                        str(item.get("response_id") or ""),
                    }
                    is_direct_match = (
                        target in item_ids
                        or normalized_target in item_ids
                        or target in node_ids
                        or normalized_target in node_ids
                        or (target.isdigit() and target in path_ids)
                    )
                    if is_direct_match:
                        direct_matches.append(item)
                    elif target == str(item.get("externalConversationId") or ""):
                        conversation_matches.append(item)

            # An image/item ID or CDN filename identifies one asset; stop as soon
            # as it is found. Conversation IDs may have several assets, so keep
            # following the feed to collect their siblings.
            if direct_matches:
                break
            page_info = feed.get("pageInfo") or {}
            next_cursor = page_info.get("endCursor")
            if not page_info.get("hasNextPage") or not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        return direct_matches or conversation_matches

    def fetch_imagine_card_media(self, card_id: str, card_type: str = "IMAGE_CARD",
                                  **kwargs) -> Dict[str, Any]:
        """Resolve media using the current web media-library query.

        ``card_id`` accepts the media-library item ID, its CDN filename's numeric
        ID, or the media's external conversation ID. The returned shape keeps
        the historical ``imagineCardMedia`` wrapper for SDK compatibility.
        """
        items = self.find_media_library_items(
            card_id, card_type, max_pages=kwargs.get("max_pages", MEDIA_LIBRARY_MAX_PAGES)
        )
        media = {
            "images": items if card_type == "IMAGE_CARD" else [],
            "videos": items if card_type == "VIDEO_CARD" else [],
        }
        return {"data": {"imagineCardMedia": media}}

    def extract_media_urls(self, response_data: Dict[str, Any]) -> List[str]:
        """Extract image/video URLs from legacy cards or current media-feed data."""
        urls = []
        try:
            data = response_data.get("data", {})
            media = data.get("imagineCardMedia", {})
            for field in ("images", "videos"):
                for item in media.get(field, []) or []:
                    url = item.get("url") or item.get("fallbackUrl")
                    if url and url not in urls:
                        urls.append(url)
            if not urls:
                feed = data.get("mediaLibraryFeed", {})
                for edge in feed.get("edges", []) or []:
                    node = (edge or {}).get("node", {}) or {}
                    for field in ("images", "videos"):
                        for item in node.get(field, []) or []:
                            url = item.get("url") or item.get("fallbackUrl")
                            if url and url not in urls:
                                urls.append(url)
        except (KeyError, TypeError):
            pass
        return urls
