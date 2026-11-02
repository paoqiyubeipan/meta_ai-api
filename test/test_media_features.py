import base64
import time
from types import SimpleNamespace
from unittest.mock import Mock
import requests

from fastapi.testclient import TestClient
import pytest

from metaai_api import api_server
from metaai_api.exceptions import GenerationError
from metaai_api.generation import MEDIA_LIBRARY_FEED_DOC_ID, GenerationAPI
from metaai_api.image_upload import ImageUploader
from metaai_api.playwright_backend import PlaywrightBackend
from metaai_api.state_store import SQLiteStateStore

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload"


@pytest.fixture(autouse=True)
def isolated_state_store(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "video_job_store", SQLiteStateStore(str(tmp_path / "state.sqlite3")))
    api_server.active_video_jobs.clear()


def test_detect_image_mime_requires_known_signature():
    assert api_server.detect_image_mime(PNG_BYTES) == "image/png"
    assert api_server.detect_image_mime(b"not an image") is None


def test_generic_file_uploader_posts_document_bytes_and_mime(monkeypatch):
    pdf_bytes = b"%PDF-1.7\nsynthetic test document\n"
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"media_id":"uploaded-pdf-1"}'

        def json(self):
            return {"media_id": "uploaded-pdf-1"}

        def raise_for_status(self):
            raise AssertionError("unexpected HTTP error")

    class FakeSession:
        def post(self, url, headers, data, timeout):
            captured.update(url=url, headers=headers, data=data, timeout=timeout)
            return FakeResponse()

    monkeypatch.setattr(requests, "Session", lambda: FakeSession())
    uploader = ImageUploader(session=None, cookies={}, access_token="ecto1:test-token")
    result = uploader.upload_file_bytes(pdf_bytes, "report.pdf", "application/pdf")

    assert result["media_id"] == "uploaded-pdf-1"
    assert result["file_name"] == "report.pdf"
    assert captured["data"] == pdf_bytes
    assert captured["headers"]["desired_upload_handler"] == "genai_document"
    assert captured["headers"]["x-entity-name"] == "report.pdf"
    assert captured["headers"]["x-entity-type"] == "application/pdf"


def test_image_json_accepts_data_url_and_cleans_temp_file(monkeypatch):
    client_stub = SimpleNamespace(
        generate_image_new=Mock(return_value={
            "success": True,
            "image_urls": ["https://example.test/generated.png"],
            "conversation_id": "conv-1",
            "prompt": "test",
        })
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    test_client = TestClient(api_server.app)
    payload = {
        "prompt": "use the reference",
        "reference_image": "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode(),
    }
    response = test_client.post("/image", json=payload)
    assert response.status_code == 200
    assert response.json()["image_urls"] == ["https://example.test/generated.png"]
    paths = client_stub.generate_image_new.call_args.kwargs["reference_images"]
    assert len(paths) == 1
    from pathlib import Path
    assert not Path(paths[0]).exists()


def test_image_rejects_non_data_url_reference(monkeypatch):
    response = TestClient(api_server.app).post(
        "/image", json={"prompt": "x", "reference_image": "https://example.test/a.png"}
    )
    assert response.status_code == 400


def test_multipart_reference_image_reaches_sdk(monkeypatch):
    client_stub = SimpleNamespace(
        generate_image_new=Mock(return_value={
            "success": True,
            "image_urls": ["https://example.test/result.png"],
            "conversation_id": "conv-2",
            "prompt": "restyle this",
        })
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    response = TestClient(api_server.app).post(
        "/image/reference",
        data={"prompt": "restyle this", "orientation": "SQUARE"},
        files={"reference_image": ("ref.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 200
    kwargs = client_stub.generate_image_new.call_args.kwargs
    assert kwargs["orientation"] == "SQUARE"
    assert len(kwargs["reference_images"]) == 1


def test_upload_returns_meta_media_id(monkeypatch):
    client_stub = SimpleNamespace(
        access_token="ecto1:token",
        upload_image=Mock(return_value={
            "success": True,
            "media_id": "media-123",
            "upload_session_id": "upload-123",
            "file_name": "ref.png",
            "file_size": len(PNG_BYTES),
            "mime_type": "image/png",
        }),
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    response = TestClient(api_server.app).post(
        "/upload", files={"file": ("ref.png", PNG_BYTES, "image/png")}
    )
    assert response.status_code == 200
    assert response.json()["media_id"] == "media-123"
    client_stub.upload_image.assert_called_once()
    assert api_server.video_job_store.get_uploaded_image("media-123")["image_bytes"] == PNG_BYTES


def test_uploaded_media_id_can_be_used_as_image_reference(monkeypatch):
    api_server.video_job_store.save_uploaded_image("media-ref-1", PNG_BYTES, "image/png", "ref.png")
    observed = {}

    def generate_image_new(*args, **kwargs):
        from pathlib import Path
        observed["image_bytes"] = Path(kwargs["reference_images"][0]).read_bytes()
        return {
            "success": True,
            "image_urls": ["https://example.test/with-upload.png"],
            "conversation_id": "conv-upload-ref",
            "prompt": "use uploaded image",
        }

    client_stub = SimpleNamespace(
        generate_image_new=Mock(side_effect=generate_image_new)
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    response = TestClient(api_server.app).post(
        "/image", json={"prompt": "use uploaded image", "media_ids": ["media-ref-1"]}
    )
    assert response.status_code == 200
    path = client_stub.generate_image_new.call_args.kwargs["reference_images"][0]
    from pathlib import Path
    assert observed["image_bytes"] == PNG_BYTES
    assert not Path(path).exists()


def test_unknown_uploaded_media_id_is_rejected(monkeypatch):
    client_stub = SimpleNamespace(generate_image_new=Mock())
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    response = TestClient(api_server.app).post(
        "/image", json={"prompt": "use upload", "media_ids": ["not-cached"]}
    )
    assert response.status_code == 400
    client_stub.generate_image_new.assert_not_called()


def test_upload_rejects_non_image_without_calling_meta(monkeypatch):
    client_stub = SimpleNamespace(access_token="ecto1:token", upload_image=Mock())
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    response = TestClient(api_server.app).post(
        "/upload", files={"file": ("not.png", b"not an image", "image/png")}
    )
    assert response.status_code == 415
    client_stub.upload_image.assert_not_called()


def test_upload_requires_access_token(monkeypatch):
    client_stub = SimpleNamespace(access_token=None, upload_image=Mock())
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    response = TestClient(api_server.app).post(
        "/upload", files={"file": ("ref.png", PNG_BYTES, "image/png")}
    )
    assert response.status_code == 503


def test_file_upload_returns_id_and_downloads_cached_original(monkeypatch):
    pdf_bytes = b"%PDF-1.7\nsynthetic test document\n"
    client_stub = SimpleNamespace(
        access_token="ecto1:token",
        upload_file=Mock(return_value={
            "success": True,
            "media_id": "file-123",
            "upload_session_id": "upload-file-123",
        }),
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    test_client = TestClient(api_server.app)

    uploaded = test_client.post(
        "/files", files={"file": ("report.pdf", pdf_bytes, "application/pdf")}
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["file_id"] == "file-123"
    assert uploaded.json()["download_url"] == "/files/file-123"
    assert uploaded.json()["local_copy_expires_in_hours"] == 24
    client_stub.upload_file.assert_called_once_with(pdf_bytes, "report.pdf", "application/pdf")

    downloaded = test_client.get("/files/file-123")
    assert downloaded.status_code == 200
    assert downloaded.content == pdf_bytes
    assert downloaded.headers["content-type"] == "application/pdf"
    assert "report.pdf" in downloaded.headers["content-disposition"]


def test_file_upload_rejects_oversized_payload_before_upstream(monkeypatch):
    client_stub = SimpleNamespace(access_token="ecto1:token", upload_file=Mock())
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    monkeypatch.setattr(api_server, "MAX_FILE_UPLOAD_BYTES", 4)
    response = TestClient(api_server.app).post(
        "/files", files={"file": ("large.zip", b"12345", "application/zip")}
    )
    assert response.status_code == 413
    client_stub.upload_file.assert_not_called()


def test_file_download_returns_not_found_after_cache_miss():
    response = TestClient(api_server.app).get("/files/not-cached")
    assert response.status_code == 404


def test_media_library_query_resolves_cdn_filename_id_after_pagination():
    media_url = (
        "https://scontent-sin11-2.xx.fbcdn.net/v/t39.105495-0/"
        "884713913_1695157365544419_8243916468314085481_n.webp?signature=test"
    )
    unrelated_page = {
        "data": {
            "mediaLibraryFeed": {
                "edges": [{"node": {"images": [{"id": "unrelated", "url": "https://scontent.example.fbcdn.net/x.webp"}]}}],
                "pageInfo": {"endCursor": "cursor-1", "hasNextPage": True},
            }
        }
    }
    target_page = {
        "data": {
            "mediaLibraryFeed": {
                "edges": [{
                    "node": {
                        "id": "v2-msg:1448513431669836",
                        "images": [{
                            "id": "1448513431669836",
                            "response_id": "1448513431669836",
                            "externalConversationId": "fed7e680-9a9c-4893-b9fd-87946b2dcb5c",
                            "url": media_url,
                            "downloadableFileName": "watercolor-variation.webp",
                        }],
                    }
                }],
                "pageInfo": {"endCursor": None, "hasNextPage": False},
            }
        }
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    calls = []

    def post(_url, **kwargs):
        calls.append(kwargs)
        after = kwargs["json"]["variables"]["after"]
        return FakeResponse(unrelated_page if after is None else target_page)

    client = GenerationAPI(session=SimpleNamespace(post=post))
    result = client.fetch_imagine_card_media("1695157365544419", "IMAGE_CARD")

    image = result["data"]["imagineCardMedia"]["images"][0]
    assert image["id"] == "1448513431669836"
    assert client.extract_media_urls(result) == [media_url]
    assert len(calls) == 2
    for call in calls:
        assert call["json"]["doc_id"] == MEDIA_LIBRARY_FEED_DOC_ID
        assert call["json"]["variables"]["shape"] == "v2"
        assert call["json"]["variables"]["first"] == 20
    assert calls[0]["json"]["variables"]["after"] is None
    assert calls[1]["json"]["variables"]["after"] == "cursor-1"


def test_extract_media_urls_accepts_raw_current_media_feed():
    media_url = "https://scontent.example.fbcdn.net/generated/image.webp"
    payload = {
        "data": {
            "mediaLibraryFeed": {
                "edges": [{"node": {"images": [{"id": "media-1", "url": media_url}]}}]
            }
        }
    }
    assert GenerationAPI().extract_media_urls(payload) == [media_url]


def test_media_library_graphql_errors_are_not_reported_as_not_found():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errors": [{"message": "Persisted query rejected"}]}

    client = GenerationAPI(session=SimpleNamespace(post=Mock(return_value=FakeResponse())))
    with pytest.raises(GenerationError, match="Persisted query rejected"):
        client.fetch_media_library_page()


def test_download_generated_media_streams_from_meta_cdn(monkeypatch):
    media_url = "https://scontent.example.fbcdn.net/generated/landscape.jpg"
    client_stub = SimpleNamespace(
        fetch_card_media=Mock(return_value={"data": "ignored"}),
        generation_api=SimpleNamespace(extract_media_urls=Mock(return_value=[media_url])),
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "image/jpeg", "Content-Length": "4"}
        closed = False

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"JPEG"

        def close(self):
            self.closed = True

    remote = FakeResponse()
    get = Mock(return_value=remote)
    monkeypatch.setattr(api_server.requests, "get", get)
    response = TestClient(api_server.app).get("/media/card-123/download?card_type=IMAGE_CARD")
    assert response.status_code == 200
    assert response.content == b"JPEG"
    assert response.headers["content-type"] == "image/jpeg"
    assert "landscape.jpg" in response.headers["content-disposition"]
    get.assert_called_once_with(media_url, stream=True, timeout=(10, 60), allow_redirects=False)
    assert remote.closed


def test_download_generated_media_rejects_non_meta_cdn_url(monkeypatch):
    client_stub = SimpleNamespace(
        fetch_card_media=Mock(return_value={}),
        generation_api=SimpleNamespace(
            extract_media_urls=Mock(return_value=["https://127.0.0.1/private"])
        ),
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    get = Mock()
    monkeypatch.setattr(api_server.requests, "get", get)
    response = TestClient(api_server.app).get("/media/card-123/download")
    assert response.status_code == 502
    get.assert_not_called()


def test_video_route_calls_sdk(monkeypatch):
    client_stub = SimpleNamespace(
        generate_video_new=Mock(return_value={
            "success": True,
            "video_urls": ["https://example.test/video.mp4"],
            "conversation_id": "conv-v",
        })
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    response = TestClient(api_server.app).post(
        "/video", json={"prompt": "animate a sunset", "auto_poll": True}
    )
    assert response.status_code == 200
    assert response.json()["video_urls"] == ["https://example.test/video.mp4"]
    assert client_stub.generate_video_new.call_args.args[0] == "animate a sunset"


def test_video_async_job_can_be_polled(monkeypatch):
    client_stub = SimpleNamespace(
        cookies={"datr": "d", "ecto_1_sess": "e"},
        generate_video_new=Mock(return_value={"success": True, "processing": True, "conversation_id": "conv-async"}),
    )

    class FakeVideoGenerator:
        def __init__(self, cookies_dict):
            assert cookies_dict == client_stub.cookies

        def fetch_video_urls(self, **kwargs):
            assert kwargs["conversation_id"] == "conv-async"
            return ["https://example.test/v.mp4"]

    monkeypatch.setattr("metaai_api.video_generation.VideoGenerator", FakeVideoGenerator)
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    test_client = TestClient(api_server.app)
    created = test_client.post("/video/async", json={"prompt": "a short clip", "wait_before_poll": 0})
    assert created.status_code == 200
    job_id = created.json()["job_id"]
    for _ in range(100):
        status = test_client.get(f"/video/jobs/{job_id}")
        if status.json()["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert status.json()["status"] == "completed"
    assert status.json()["result"]["video_urls"] == ["https://example.test/v.mp4"]
    assert status.json()["conversation_id"] == "conv-async"
    assert client_stub.generate_video_new.call_args.kwargs["auto_poll"] is False


def test_video_job_survives_store_reopen_and_resumes_polling(tmp_path):
    db_path = str(tmp_path / "persistent.sqlite3")
    first_store = SQLiteStateStore(db_path)
    first_store.create_video_job("job-resume", {"prompt": "a clip", "max_attempts": 2})
    first_store.update_video_job("job-resume", status="processing", conversation_id="conv-saved")

    reopened_store = SQLiteStateStore(db_path)
    job = reopened_store.get_video_job("job-resume")
    recoverable = reopened_store.get_recoverable_video_jobs()
    assert job["conversation_id"] == "conv-saved"
    assert job["request"]["prompt"] == "a clip"
    assert [item["job_id"] for item in recoverable] == ["job-resume"]


def test_app_startup_launches_recoverable_jobs(monkeypatch):
    api_server.video_job_store.create_video_job("job-startup", {"prompt": "a clip"})
    api_server.video_job_store.update_video_job(
        "job-startup", status="processing", conversation_id="conv-startup"
    )
    launched = []
    monkeypatch.setattr(api_server, "_launch_video_job", launched.append)
    with TestClient(api_server.app):
        pass
    assert launched == ["job-startup"]


def test_ambiguous_submission_is_not_retried_automatically(tmp_path):
    store = SQLiteStateStore(str(tmp_path / "ambiguous.sqlite3"))
    store.create_video_job("job-ambiguous", {"prompt": "a clip"})
    store.update_video_job("job-ambiguous", status="submitting")
    assert store.get_recoverable_video_jobs() == []
    job = store.get_video_job("job-ambiguous")
    assert job["status"] == "interrupted"
    assert "duplicate" in job["error"]


def test_playwright_backend_attaches_files_before_prompt():
    class FakeInput:
        def __init__(self):
            self.paths = None

        def set_input_files(self, paths, timeout):
            self.paths = paths

    fake_input = FakeInput()
    backend = object.__new__(PlaywrightBackend)
    backend._ready = True
    backend._page = SimpleNamespace(query_selector_all=lambda selector: [fake_input])
    backend._dismiss_overlays = lambda: None
    backend.send_message = Mock(return_value={"urls": ["generated"], "text": "", "conversation_id": "c"})
    result = backend.send_message_with_images("make art", ["/tmp/ref.png"], timeout=1)
    assert fake_input.paths == ["/tmp/ref.png"]
    backend.send_message.assert_called_once_with(
        "make art", timeout=1, thinking_mode=False, expected_media=True
    )
    assert result["urls"] == ["generated"]


def test_playwright_backend_opens_add_attachment_when_file_input_is_lazy():
    class FakeInput:
        def __init__(self):
            self.paths = None

        def set_input_files(self, paths, timeout):
            self.paths = paths

    class FakeButton:
        def __init__(self, page):
            self.page = page
            self.clicked = False

        def click(self, timeout):
            self.clicked = True
            self.page.file_inputs.append(FakeInput())

    class FakePage:
        def __init__(self):
            self.file_inputs = []
            self.button = FakeButton(self)
            self.waited_for_preview = None

        def query_selector_all(self, selector):
            if selector == 'input[type="file"]':
                return self.file_inputs
            return []

        def query_selector(self, selector):
            return self.button if "composer-add-attachment-button" in selector else None

        def wait_for_selector(self, selector, state, timeout):
            assert selector == 'input[type="file"]'
            assert state == "attached"
            assert self.file_inputs

        def wait_for_function(self, expression, *, arg, timeout):
            self.waited_for_preview = (arg, timeout)

    page = FakePage()
    backend = object.__new__(PlaywrightBackend)
    backend._ready = True
    backend._page = page
    backend._dismiss_overlays = lambda: None
    backend.send_message = Mock(return_value={"urls": ["generated"], "text": "", "conversation_id": "c"})

    result = backend.send_message_with_images("make art", ["/tmp/ref.png"], timeout=1)

    assert page.button.clicked
    assert page.file_inputs[0].paths == ["/tmp/ref.png"]
    assert page.waited_for_preview == (1, 15000)
    assert result["urls"] == ["generated"]
    backend.send_message.assert_called_once_with(
        "make art", timeout=1, thinking_mode=False, expected_media=True
    )


def test_playwright_backend_activates_add_button_when_input_already_exists():
    class FakeInput:
        def __init__(self):
            self.paths = None

        def set_input_files(self, paths, timeout):
            self.paths = paths

    class FakeButton:
        def __init__(self):
            self.clicked = False

        def click(self, timeout):
            self.clicked = True

    file_input = FakeInput()
    button = FakeButton()
    page = SimpleNamespace(
        query_selector_all=lambda selector: [file_input] if selector == 'input[type="file"]' else [],
        query_selector=lambda selector: button if "composer-add-attachment-button" in selector else None,
        wait_for_function=lambda _expression, *, arg, timeout: None,
    )
    backend = object.__new__(PlaywrightBackend)
    backend._ready = True
    backend._page = page
    backend._dismiss_overlays = lambda: None
    backend.send_message = Mock(return_value={"urls": ["generated"], "text": "", "conversation_id": "c"})

    result = backend.send_message_with_images("make art", ["/tmp/ref.png"], timeout=1)

    assert button.clicked
    assert file_input.paths == ["/tmp/ref.png"]
    assert result["urls"] == ["generated"]


def test_playwright_backend_waits_for_successful_rupload_before_sending():
    class FakeResponse:
        url = "https://rupload.meta.ai/gen_ai_document_gen_ai_tenant/session"
        status = 200

    class FakeInput:
        def __init__(self, page):
            self.page = page
            self.paths = None

        def set_input_files(self, paths, timeout):
            self.paths = paths
            self.page.upload_listener(FakeResponse())

    class FakeButton:
        def click(self, timeout):
            pass

    class FakePage:
        def __init__(self):
            self.upload_listener = None
            self.file_input = FakeInput(self)

        def query_selector_all(self, selector):
            return [self.file_input] if selector == 'input[type="file"]' else []

        def query_selector(self, selector):
            return FakeButton() if "composer-add-attachment-button" in selector else None

        def wait_for_selector(self, selector, state, timeout):
            assert selector == 'input[type="file"]'
            assert state == "attached"

        def on(self, event, listener):
            assert event == "response"
            self.upload_listener = listener

        def remove_listener(self, event, listener):
            self.upload_listener = None

        def wait_for_timeout(self, milliseconds):
            pass

        def wait_for_function(self, expression, *, arg, timeout):
            assert arg == 1

    page = FakePage()
    backend = object.__new__(PlaywrightBackend)
    backend._ready = True
    backend._page = page
    backend._dismiss_overlays = lambda: None

    def send_after_upload(*args, **kwargs):
        assert page.upload_listener is None
        return {"urls": ["generated"], "text": "", "conversation_id": "c"}

    backend.send_message = Mock(side_effect=send_after_upload)
    result = backend.send_message_with_images("make art", ["/tmp/ref.png"], timeout=1)

    assert page.file_input.paths == ["/tmp/ref.png"]
    assert result["urls"] == ["generated"]
    backend.send_message.assert_called_once_with(
        "make art", timeout=1, thinking_mode=False, expected_media=True
    )


def test_playwright_backend_does_not_send_when_attachment_control_is_missing():
    from metaai_api.exceptions import ConnectionError

    backend = object.__new__(PlaywrightBackend)
    backend._ready = True
    backend._page = SimpleNamespace(
        query_selector_all=lambda _selector: [],
        query_selector=lambda _selector: None,
        wait_for_selector=Mock(side_effect=TimeoutError("not found")),
    )
    backend._dismiss_overlays = lambda: None
    backend.send_message = Mock()

    with pytest.raises(ConnectionError, match="Add attachment control"):
        backend.send_message_with_images("make art", ["/tmp/ref.png"], timeout=1)

    backend.send_message.assert_not_called()


def test_playwright_backend_fills_editable_composer_and_clicks_send():
    prompt = "Use the attached reference image"

    class FakeEditor:
        def __init__(self):
            self.text = ""

        def is_visible(self):
            return True

        def get_attribute(self, name):
            return "true" if name == "contenteditable" else None

        def scroll_into_view_if_needed(self, timeout):
            pass

        def fill(self, value, timeout):
            self.text = value

        def evaluate(self, _expression):
            return self.text

    class FakeSendButton:
        def __init__(self, editor, page):
            self.editor = editor
            self.page = page
            self.clicked = False

        def is_visible(self):
            return True

        def is_enabled(self):
            return True

        def click(self, timeout):
            assert self.editor.text == prompt
            self.clicked = True
            self.page.sent = True

    editor = FakeEditor()
    selectors = []

    class FakePage:
        def __init__(self):
            self.keyboard = SimpleNamespace(press=Mock(), type=Mock())
            self.sent = False
            self.generated_image = SimpleNamespace(
                get_attribute=lambda name: (
                    "https://scontent.xx.fbcdn.net/generated.webp" if name == "src" else None
                )
            )
            self.send_button = FakeSendButton(editor, self)

        def query_selector(self, selector):
            selectors.append(selector)
            if selector == '[data-testid="composer-input"][contenteditable="true"]':
                return editor
            if selector == '[data-testid="composer-send-button"], button[aria-label="Send"]':
                return self.send_button
            return None

        def query_selector_all(self, selector):
            if selector == 'img[src], video[src], source[src], a[href]' and self.sent:
                return [self.generated_image]
            return []

    page = FakePage()
    backend = object.__new__(PlaywrightBackend)
    backend._ready = True
    backend._page = page
    backend._switch_mode = lambda _mode: None
    backend._dismiss_overlays = lambda: None

    def wait_for_response(_timeout, existing_urls):
        assert existing_urls == set()
        return (["https://scontent.xx.fbcdn.net/generated.webp"], "done")

    backend._wait_for_response = lambda timeout, existing_urls, expected_media=False: wait_for_response(
        timeout, existing_urls
    )
    backend._get_conversation_id = lambda: "conversation"

    result = backend.send_message(prompt, timeout=1)

    assert editor.text == prompt
    assert page.send_button.clicked
    assert '[data-testid="composer-input"][contenteditable="true"]' in selectors
    assert backend._page.keyboard.press.call_count == 0
    assert result == {
        "urls": ["https://scontent.xx.fbcdn.net/generated.webp"],
        "text": "done",
        "conversation_id": "conversation",
    }


def test_playwright_backend_waits_for_media_after_text_is_stable(monkeypatch):
    from metaai_api import playwright_backend

    monkeypatch.setattr(playwright_backend.time, "sleep", lambda _seconds: None)
    image_url = "https://scontent.xx.fbcdn.net/v/t39.105495-1/generated.webp"
    image = SimpleNamespace(
        get_attribute=lambda name: image_url if name == "src" else None
    )

    class FakePage:
        def __init__(self):
            self.checks = 0

        def query_selector_all(self, _selector):
            self.checks += 1
            return [image] if self.checks >= 4 else []

    backend = object.__new__(PlaywrightBackend)
    backend._page = FakePage()
    backend._extract_text = lambda: "Here's your watercolor variation."

    urls, text = backend._wait_for_response(
        timeout=5, existing_urls=set(), expected_media=True
    )

    assert urls == [image_url]
    assert text == "Here's your watercolor variation."
    assert backend._page.checks == 4


def test_image_field_alias_accepts_data_url(monkeypatch):
    client_stub = SimpleNamespace(
        generate_image_new=Mock(return_value={
            "success": True,
            "image_urls": ["https://example.test/alias.png"],
            "conversation_id": "conv-alias",
            "prompt": "alias",
        })
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    data_url = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()
    response = TestClient(api_server.app).post("/image", json={"prompt": "alias", "image": data_url})
    assert response.status_code == 200
    assert len(client_stub.generate_image_new.call_args.kwargs["reference_images"]) == 1


def test_sdk_video_wrapper_uses_graphql_generator(monkeypatch):
    from metaai_api.main import MetaAI

    class FakeVideoGenerator:
        def __init__(self, cookies_dict):
            self.cookies = cookies_dict

        def generate_video(self, **kwargs):
            assert kwargs["prompt"] == "test clip"
            assert kwargs["verbose"] is False
            return {"success": True, "video_urls": ["https://example.test/v.mp4"]}

    monkeypatch.setattr("metaai_api.video_generation.VideoGenerator", FakeVideoGenerator)
    ai = object.__new__(MetaAI)
    ai.cookies = {"datr": "d", "ecto_1_sess": "e"}
    ai.generate_video_new("test clip", auto_poll=True, max_poll_attempts=2)
    assert FakeVideoGenerator


def test_video_generator_extracts_tokens_from_initial_html(monkeypatch):
    from metaai_api.video_generation import VideoGenerator

    response = SimpleNamespace(
        text='xx "LSD",[],{"token":"lsd-123"} yy DTSGInitData",[],{"token":"dtsg-456"}',
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr("metaai_api.video_generation.requests.get", lambda *args, **kwargs: response)
    assert VideoGenerator.get_lsd_and_dtsg("datr=d; ecto_1_sess=e") == {
        "lsd": "lsd-123",
        "fb_dtsg": "dtsg-456",
    }


def test_video_unavailable_markers_cover_live_refusals():
    from metaai_api.video_generation import _is_video_unavailable

    live_en = (
        "Video generation isn't available to you right now, so I can't create "
        "that 5-second video of a cat walking in a garden."
    )
    live_ja = "動画生成は現在ご利用いただけません。"
    assert _is_video_unavailable(live_en)
    assert _is_video_unavailable(live_ja)
    assert not _is_video_unavailable("Here is your video: https://example.test/v.mp4")
    assert not _is_video_unavailable("")


def test_polling_stops_and_reports_unavailability(monkeypatch):
    from metaai_api.exceptions import VideoUnavailableError
    from metaai_api.video_generation import VideoGenerator

    generator = object.__new__(VideoGenerator)
    generator.cookies = {"datr": "d"}
    generator.lsd = "l"
    generator.fb_dtsg = "f"
    generator.cookies_str = "datr=d"
    response = SimpleNamespace(status_code=200, text="動画生成は現在ご利用いただけません。", headers={})
    monkeypatch.setattr("metaai_api.video_generation.requests.post", lambda *a, **k: response)
    monkeypatch.setattr("metaai_api.video_generation.time.sleep", lambda *_: None)
    with pytest.raises(VideoUnavailableError):
        generator.fetch_video_urls("conv-1", max_attempts=1, wait_seconds=0, verbose=False)


def test_video_route_maps_unavailability_to_503(monkeypatch):
    from metaai_api.exceptions import VideoUnavailableError

    client_stub = SimpleNamespace(
        generate_video_new=Mock(side_effect=VideoUnavailableError("not available"))
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    response = TestClient(api_server.app).post("/video", json={"prompt": "a clip"})
    assert response.status_code == 503
    assert "not available for this account" in response.json()["detail"]
