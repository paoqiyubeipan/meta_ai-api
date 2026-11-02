"""Playwright browser backend — pure Python, no Node.js needed.

This is an alternative to BrowserBackend that uses Playwright instead of
agent-browser. It's ideal for Google Colab and other environments where
Node.js isn't available or is too old.

Install:
    pip install metaai-sdk[playwright]
    playwright install chromium
    playwright install-deps  # Linux/Colab only
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .exceptions import BrowserNotInstalledError, ConnectionError
from .utils import DEFAULT_UA, is_media_url, logger

try:
    from playwright.sync_api import sync_playwright, Browser, Page
except ImportError:
    sync_playwright = None
    Browser = None
    Page = None

CHAT_ATTACHMENT_PREVIEW_TIMEOUT = 15


class PlaywrightBackend:
    """Browser automation backend using Playwright (pure Python).

    This is the recommended backend for Google Colab and environments
    where Node.js/agent-browser isn't available.
    """

    def __init__(self, cookies: dict, headed: bool = False,
                 session_name: str = "metaai", user_agent: str = DEFAULT_UA):
        self.cookies = cookies
        self.headed = headed
        self.session_name = session_name
        self.user_agent = user_agent
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._ready = False

    def _check_installed(self):
        if sync_playwright is None:
            raise BrowserNotInstalledError(
                "Playwright is not installed. Install it with:\n"
                "  pip install metaai-sdk[playwright]\n"
                "  playwright install chromium\n"
                "  playwright install-deps  # Linux/Colab only"
            )

    def setup(self) -> None:
        """Start browser and inject cookies."""
        self._check_installed()

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=not self.headed,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 720},
        )

        # Set cookies
        cookie_list = []
        for name, value in self.cookies.items():
            cookie_list.append({
                "name": name,
                "value": value,
                "domain": ".meta.ai",
                "path": "/",
            })
        context.add_cookies(cookie_list)

        self._page = context.new_page()
        self._page.goto("https://www.meta.ai/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Dismiss any cookie consent / overlay dialogs
        self._dismiss_overlays()

        # Reload to apply cookies
        self._page.reload(wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        self._dismiss_overlays()

        self._ready = True
        logger.info("Playwright backend ready")

    def _dismiss_overlays(self):
        """Dismiss cookie consent, dialogs, and overlays that might block the input."""
        try:
            # Try clicking "Connect" / "Dismiss" / "Accept" buttons
            for text in ["Dismiss", "Connect", "Accept all", "Accept", "Got it", "Close", "OK"]:
                try:
                    btn = self._page.query_selector(f'button:has-text("{text}")')
                    if btn and btn.is_visible():
                        btn.click()
                        time.sleep(1)
                        logger.info(f"Dismissed overlay: {text}")
                except Exception:
                    pass
        except Exception:
            pass

    def send_message_with_images(
        self, prompt: str, image_paths: List[str], timeout: int = 120,
        thinking_mode: bool = False,
    ) -> Dict[str, Any]:
        """Attach local reference images in Meta AI's web composer, then prompt."""
        if not self._ready:
            self.setup()
        self._dismiss_overlays()
        if not image_paths:
            return self.send_message(prompt, timeout=timeout, thinking_mode=thinking_mode)

        input_selector = 'input[type="file"]'
        inputs = self._page.query_selector_all(input_selector)
        # The current composer may mount its hidden input before it is usable.
        # Always activate the hydrated attachment button when present; otherwise
        # the UI can show a local thumbnail without associating it with a sent turn.
        button_selector = (
            '[data-testid="composer-add-attachment-button"], '
            'button[aria-label="Add attachment"], '
            'button[aria-label*="attach" i]'
        )
        query_selector = getattr(self._page, "query_selector", None)
        button = query_selector(button_selector) if callable(query_selector) else None
        if not button and not inputs:
            try:
                self._page.wait_for_selector(
                    button_selector, state="visible", timeout=30000
                )
            except Exception:
                pass
            button = query_selector(button_selector) if callable(query_selector) else None
        if button:
            try:
                button.click(timeout=5000)
            except Exception as exc:
                raise ConnectionError(f"Could not open Meta AI's attachment menu: {exc}") from exc
            try:
                self._page.wait_for_selector(
                    input_selector, state="attached", timeout=10000
                )
            except Exception:
                # Keep compatibility with lightweight browser mocks and older
                # Playwright versions while still checking the resulting DOM.
                pass
            inputs = self._page.query_selector_all(input_selector)
        if not inputs:
            raise ConnectionError(
                "Meta AI's Add attachment control did not provide a file input."
            )

        try:
            baseline_previews = len(
                self._page.query_selector_all('img[src^="blob:"], img[src^="data:"]')
            )
        except Exception:
            baseline_previews = None
        upload_responses = []
        response_listener = None
        add_listener = getattr(self._page, "on", None)
        remove_listener = getattr(self._page, "remove_listener", None)
        if callable(add_listener):
            def capture_upload_response(response):
                if "rupload.meta.ai" in getattr(response, "url", ""):
                    upload_responses.append(response)

            response_listener = capture_upload_response
            add_listener("response", response_listener)
        last_error = None
        try:
            for file_input in inputs:
                try:
                    file_input.set_input_files(image_paths, timeout=5000)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
            if last_error:
                raise ConnectionError(f"Could not attach reference image: {last_error}")
            if response_listener:
                deadline = time.monotonic() + 30
                wait_for_timeout = getattr(self._page, "wait_for_timeout", None)
                while len(upload_responses) < len(image_paths) and time.monotonic() < deadline:
                    if not callable(wait_for_timeout):
                        break
                    wait_for_timeout(100)
                if len(upload_responses) < len(image_paths):
                    raise ConnectionError(
                        "Meta AI did not confirm the reference-image upload; the prompt was not sent."
                    )
                failed_uploads = [response.status for response in upload_responses if response.status >= 400]
                if failed_uploads:
                    raise ConnectionError(
                        f"Meta AI rejected the reference-image upload (HTTP {failed_uploads[0]})."
                    )
        finally:
            if response_listener and callable(remove_listener):
                remove_listener("response", response_listener)
        # Do not send the prompt if the current UI did not render an image
        # preview; otherwise Meta AI may answer that no reference was attached.
        wait_for_function = getattr(self._page, "wait_for_function", None)
        if baseline_previews is not None and callable(wait_for_function):
            try:
                wait_for_function(
                    "count => document.querySelectorAll('img[src^=\\\"blob:\\\"], "
                    "img[src^=\\\"data:\\\"]').length >= count",
                    arg=baseline_previews + len(image_paths),
                    timeout=15000,
                )
            except Exception as exc:
                raise ConnectionError(
                    "Meta AI did not display the reference-image preview; the prompt was not sent."
                ) from exc
        else:
            time.sleep(2)
        return self.send_message(
            prompt, timeout=timeout, thinking_mode=thinking_mode, expected_media=True
        )

    def send_message_with_files(
        self, prompt: str, file_paths: List[str], timeout: int = 120,
        thinking_mode: bool = False,
    ) -> Dict[str, Any]:
        """Attach local files through Meta AI's web composer, then ask a question.

        This uses the browser's native upload flow rather than guessing how an
        uploaded rupload media ID should be serialized into a chat frame.
        """
        if not self._ready:
            self.setup()
        self._dismiss_overlays()
        if not file_paths:
            return self.send_message(prompt, timeout=timeout, thinking_mode=thinking_mode)

        input_selector = 'input[type="file"]'
        button_selector = (
            '[data-testid="composer-add-attachment-button"], '
            'button[aria-label="Add attachment"], '
            'button[aria-label*="attach" i]'
        )
        query_selector = getattr(self._page, "query_selector", None)
        inputs = self._page.query_selector_all(input_selector)
        button = query_selector(button_selector) if callable(query_selector) else None
        if button:
            try:
                button.click(timeout=5000)
            except Exception as exc:
                raise ConnectionError("Could not open Meta AI's attachment menu: {}".format(exc)) from exc
            try:
                self._page.wait_for_selector(input_selector, state="attached", timeout=10000)
            except Exception:
                pass
            inputs = self._page.query_selector_all(input_selector)
        elif not inputs:
            try:
                self._page.wait_for_selector(button_selector, state="visible", timeout=30000)
            except Exception:
                pass
            button = query_selector(button_selector) if callable(query_selector) else None
            if button:
                button.click(timeout=5000)
                try:
                    self._page.wait_for_selector(input_selector, state="attached", timeout=10000)
                except Exception:
                    pass
                inputs = self._page.query_selector_all(input_selector)
        if not inputs:
            raise ConnectionError(
                "Meta AI's Add attachment control did not provide a file input; the prompt was not sent."
            )

        upload_responses = []
        response_listener = None
        add_listener = getattr(self._page, "on", None)
        remove_listener = getattr(self._page, "remove_listener", None)
        if callable(add_listener):
            def capture_upload_response(response):
                if "rupload.meta.ai" in getattr(response, "url", ""):
                    upload_responses.append(response)

            response_listener = capture_upload_response
            add_listener("response", response_listener)
        last_error = None
        try:
            for file_input in inputs:
                try:
                    file_input.set_input_files(file_paths, timeout=5000)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
            if last_error:
                raise ConnectionError(
                    "Could not attach chat files: {}; the prompt was not sent.".format(last_error)
                )
            if response_listener:
                deadline = time.monotonic() + 30
                wait_for_timeout = getattr(self._page, "wait_for_timeout", None)
                while len(upload_responses) < len(file_paths) and time.monotonic() < deadline:
                    if not callable(wait_for_timeout):
                        break
                    wait_for_timeout(100)
                if len(upload_responses) < len(file_paths):
                    raise ConnectionError(
                        "Meta AI did not confirm every file upload; the prompt was not sent."
                    )
                failed_uploads = [
                    response.status for response in upload_responses
                    if response.status >= 400
                ]
                if failed_uploads:
                    raise ConnectionError(
                        "Meta AI rejected a chat attachment (HTTP {}); the prompt was not sent.".format(
                            failed_uploads[0]
                        )
                    )
        finally:
            if response_listener and callable(remove_listener):
                remove_listener("response", response_listener)

        # Do not submit a bare text prompt when the browser cannot show that the
        # selected files are still associated with the composer.
        visible_names = False
        deadline = time.monotonic() + CHAT_ATTACHMENT_PREVIEW_TIMEOUT
        wait_for_timeout = getattr(self._page, "wait_for_timeout", None)
        while time.monotonic() < deadline and not visible_names:
            try:
                visible_names = self._page.evaluate(
                    """names => {
                        const body = (document.body?.innerText || '').toLowerCase();
                        const chips = document.querySelectorAll(
                            '[data-testid*="attachment" i], [data-testid*="file-chip" i], [aria-label*="remove attachment" i], [aria-label*="remove file" i]'
                        ).length;
                        return names.every(name => body.includes(name.toLowerCase())) || chips >= names.length;
                    }""",
                    [Path(path).name for path in file_paths],
                )
            except Exception:
                # Selecting a document can navigate/re-hydrate the composer;
                # retry after the new DOM execution context becomes available.
                visible_names = False
            if not visible_names:
                if callable(wait_for_timeout):
                    wait_for_timeout(250)
                else:
                    time.sleep(0.25)
        if not visible_names:
            raise ConnectionError(
                "Meta AI did not show the selected files in the composer; the prompt was not sent."
            )
        return self.send_message(
            prompt, timeout=timeout, thinking_mode=thinking_mode, expected_media=False
        )

    def send_message(self, prompt: str, timeout: int = 120,
                     thinking_mode: bool = False,
                     expected_media: bool = False) -> Dict[str, Any]:
        """Send a prompt and return media URLs + text response."""
        if not self._ready:
            self.setup()

        if thinking_mode:
            self._switch_mode("Thinking")
        else:
            self._switch_mode("Instant")

        # Dismiss any overlays that appeared
        self._dismiss_overlays()

        # Snapshot existing media before submitting. A fast image result may
        # render before the response poll starts and must still count as new.
        existing_urls: Set[str] = set()
        try:
            elements = self._page.query_selector_all('img[src], video[src], source[src], a[href]')
            for el in elements:
                src = el.get_attribute("src") or el.get_attribute("href") or ""
                if src and "fbcdn" in src:
                    existing_urls.add(src)
        except Exception:
            pass

        # Prefer the editable composer itself. Descendant contenteditable nodes
        # can be non-editable attachment thumbnails and must never receive text.
        typed = False
        selectors = [
            '[data-testid="composer-input"][contenteditable="true"]',
            'div[data-testid="composer-input"][role="textbox"]',
            'textarea[data-testid="composer-input"]',
            'textarea[placeholder*="Ask Meta"]',
            '[role="textbox"]',
            'textarea',
        ]

        for attempt in range(20):
            for selector in selectors:
                try:
                    el = self._page.query_selector(selector)
                    if not el or not el.is_visible():
                        continue
                    if el.get_attribute("contenteditable") == "false":
                        continue
                    el.scroll_into_view_if_needed(timeout=5000)
                    try:
                        el.fill(prompt, timeout=5000)
                    except Exception:
                        el.click(timeout=5000)
                        self._page.keyboard.type(prompt, delay=30)
                    actual_text = el.evaluate(
                        "e => e.tagName === 'TEXTAREA' ? e.value : (e.innerText || e.textContent || '')"
                    )
                    if prompt not in str(actual_text):
                        continue
                    typed = True
                    logger.info(f"Filled prompt using selector: {selector}")
                    break
                except Exception as e:
                    logger.debug(f"Selector {selector} failed: {e}")
                    continue
            if typed:
                break
            if attempt < 19:
                time.sleep(0.1)

        if not typed:
            raise ConnectionError(
                "Could not verify that the prompt was entered in Meta AI's composer; the prompt was not sent."
            )

        send_button = self._page.query_selector(
            '[data-testid="composer-send-button"], button[aria-label="Send"]'
        )
        if send_button and send_button.is_visible():
            enabled = False
            for attempt in range(20):
                if send_button.is_enabled():
                    enabled = True
                    break
                if attempt < 19:
                    time.sleep(0.25)
                    send_button = self._page.query_selector(
                        '[data-testid="composer-send-button"], button[aria-label="Send"]'
                    )
                    if not send_button or not send_button.is_visible():
                        break
            if not enabled:
                raise ConnectionError(
                    "Meta AI's Send button stayed disabled after the prompt was entered."
                )
            send_button.click(timeout=5000)
        else:
            self._page.keyboard.press("Enter")
        time.sleep(0.5)

        # Wait for response — only collect NEW URLs
        urls, text = self._wait_for_response(
            timeout, existing_urls, expected_media=expected_media
        )
        conv_id = self._get_conversation_id()

        return {"urls": urls, "text": text, "conversation_id": conv_id}

    def send_message_for_media(self, prompt: str, timeout: int = 120) -> Dict[str, Any]:
        """Send an image-generation prompt and wait for the media URL, not just text."""
        return self.send_message(prompt, timeout=timeout, expected_media=True)

    def _switch_mode(self, mode_name: str) -> None:
        """Switch between Instant and Thinking modes."""
        try:
            mode_button = self._page.query_selector('button:has-text("Instant"), button:has-text("Thinking")')
            if mode_button and mode_button.is_visible():
                mode_button.click()
                time.sleep(1)
                mode_item = self._page.query_selector(f'[role="menuitemcheckbox"]:has-text("{mode_name}")')
                if mode_item:
                    mode_item.click()
                    time.sleep(1)
        except Exception:
            pass

    def _wait_for_response(self, timeout: int, existing_urls: Optional[Set[str]] = None,
                           expected_media: bool = False) -> Tuple[List[str], str]:
        """Wait for either media URLs or text response. Only collect NEW URLs."""
        urls: Set[str] = set()
        last_text = ""
        text_stable_count = 0
        start = time.time()
        existing = existing_urls or set()

        while time.time() - start < timeout:
            time.sleep(2)

            # Check for NEW media URLs only
            try:
                elements = self._page.query_selector_all('img[src], video[src], source[src], a[href]')
                for el in elements:
                    src = el.get_attribute("src") or el.get_attribute("href") or ""
                    if src and "fbcdn" in src and is_media_url(src) and src not in existing:
                        urls.add(src)
            except Exception:
                pass

            if urls:
                time.sleep(3)
                return sorted(urls), self._extract_text()

            # Check for text response
            try:
                text = self._extract_text()
                if text:
                    if text == last_text:
                        text_stable_count += 1
                        if text_stable_count >= 2 and not expected_media:
                            return [], text
                    else:
                        text_stable_count = 0
                        last_text = text
            except Exception:
                pass

        return sorted(urls), last_text

    def _extract_text(self) -> str:
        try:
            return self._page.evaluate("""
                () => {
                    // Older Meta AI pages use assistant-message classes;
                    // the current UI renders replies in `.prose` blocks.
                    const selectors = ['[class*="assistant-message"]', '.prose'];
                    for (const selector of selectors) {
                        const messages = Array.from(document.querySelectorAll(selector))
                            .map((node) => (node.innerText || node.textContent || '').trim())
                            .filter(Boolean);
                        if (messages.length) return messages[messages.length - 1];
                    }
                    return '';
                }
            """)
        except Exception:
            return ""

    def _get_conversation_id(self) -> str:
        try:
            url = self._page.url
            m = re.search(r'/prompt/([0-9a-f-]+)', url)
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""

    def list_conversations(self) -> List[dict]:
        """Get all conversations from the sidebar."""
        try:
            convs = self._page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/prompt/"]'));
                    return links.map(a => ({
                        title: a.textContent?.trim(),
                        url: a.href
                    })).filter(c => c.title);
                }
            """)
            result = []
            for c in convs:
                if isinstance(c, dict):
                    result.append({
                        "id": c.get("url", "").split("/prompt/")[-1] if "/prompt/" in c.get("url", "") else "",
                        "title": c.get("title", ""),
                        "url": c.get("url", ""),
                    })
            return result
        except Exception:
            return []

    def use_as_reference(self, image_url: str, prompt: str, timeout: int = 120) -> Dict[str, Any]:
        """Use an existing image as a reference for a new generation."""
        try:
            ref_button = self._page.query_selector('button[aria-label="Use as reference"]')
            if ref_button:
                ref_button.click()
                time.sleep(2)

            self._dismiss_overlays()
            for selector in ['textarea[data-testid="composer-input"]', '[role="textbox"]', 'textarea']:
                try:
                    el = self._page.query_selector(selector)
                    if el:
                        el.click(timeout=5000)
                        self._page.keyboard.type(prompt, delay=30)
                        time.sleep(0.5)
                        self._page.keyboard.press("Enter")
                        break
                except Exception:
                    continue

            urls, text = self._wait_for_response(timeout)
            return {"urls": urls, "text": text, "conversation_id": self._get_conversation_id()}
        except Exception as e:
            return {"urls": [], "text": "", "conversation_id": "", "error": str(e)}

    def new_chat(self) -> None:
        """Start a new chat conversation."""
        try:
            link = self._page.query_selector('a:has-text("New chat")')
            if link:
                link.click()
                time.sleep(2)
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._page:
                self._page.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._ready = False
