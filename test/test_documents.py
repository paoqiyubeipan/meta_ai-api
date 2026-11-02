"""Tests for exporting Meta AI generated document artifacts."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from metaai_api import api_server
from metaai_api.documents import (
    DocumentExporter,
    is_allowed_document_url,
    parse_document_artifacts,
)
from metaai_api.exceptions import DocumentExportError

PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"
ASSET_URL = (
    "https://1448526705001842.a.metaaiusercontent.com/pdf"
    "?artifact_uuid=a74a422b-1c36-419a-a58c-0928558d2ad4&ext=1801365627&hash=TESTHASH"
)


def _sandbox_block(**overrides):
    block = {
        "asset_id": "1448526705001842",
        "uuid": "a74a422b-1c36-419a-a58c-0928558d2ad4",
        "viewer_actions": ["CAN_DOWNLOAD", "SHARE"],
        "file_extension": "pdf",
        "url": ASSET_URL,
        "artifact_type": "PDF",
        "title": "Generated",
    }
    block.update(overrides)
    return block


def _conversation_html(blocks):
    """Mimic the RSC payload: JSON with escaped quotes inside the page."""
    encoded = json.dumps(blocks[0]).replace('"', '\\"').replace("&", "\\u0026")
    payload = '"html_artifact_sandbox":' + encoded
    return f"<html><script>{payload}</script></html>"


def _viewer_page(pdf_bytes: bytes) -> str:
    encoded = base64.b64encode(pdf_bytes).decode()
    return f'<script>self.__next_f.push([1,"{{\\"pdfBase64\\":\\"{encoded}\\"}}"])</script>'


@pytest.fixture(autouse=True)
def isolated_state_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        api_server, "video_job_store", api_server.SQLiteStateStore(str(tmp_path / "state.sqlite3"))
    )


def test_parse_document_artifacts_from_escaped_conversation_payload():
    documents = parse_document_artifacts(_conversation_html([_sandbox_block()]))
    assert len(documents) == 1
    document = documents[0]
    assert document["artifact_uuid"] == "a74a422b-1c36-419a-a58c-0928558d2ad4"
    assert document["file_name"] == "Generated.pdf"
    assert document["mime_type"] == "application/pdf"
    assert document["url"].startswith("https://1448526705001842.a.metaaiusercontent.com/pdf?")
    assert "&hash=TESTHASH" in document["url"]


def test_parse_document_artifacts_deduplicates_and_skips_foreign_hosts():
    html = _conversation_html([_sandbox_block()])
    html += _conversation_html([_sandbox_block(url="https://evil.example.com/pdf?x=1")])
    documents = parse_document_artifacts(html)
    assert [d["artifact_uuid"] for d in documents] == ["a74a422b-1c36-419a-a58c-0928558d2ad4"]


@pytest.mark.parametrize(
    "url,allowed",
    [
        (ASSET_URL, True),
        ("http://1448526705001842.a.metaaiusercontent.com/pdf", False),
        ("https://metaaiusercontent.com.evil.test/pdf", False),
        ("https://user:pass@1448526705001842.a.metaaiusercontent.com/pdf", False),
        ("https://127.0.0.1/pdf", False),
    ],
)
def test_document_host_allow_list(url, allowed):
    assert is_allowed_document_url(url) is allowed


def test_fetch_document_pdf_decodes_embedded_bytes():
    session = SimpleNamespace(get=Mock(return_value=SimpleNamespace(
        text=_viewer_page(PDF_BYTES), raise_for_status=Mock()
    )))
    exporter = DocumentExporter(session=session, cookies={"datr": "x", "ecto_1_sess": "y"})
    assert exporter.fetch_document_pdf(ASSET_URL) == PDF_BYTES


def test_fetch_document_pdf_rejects_missing_payload():
    session = SimpleNamespace(get=Mock(return_value=SimpleNamespace(
        text="<html>no document here</html>", raise_for_status=Mock()
    )))
    exporter = DocumentExporter(session=session)
    with pytest.raises(DocumentExportError, match="no file export"):
        exporter.fetch_document_pdf(ASSET_URL)


def test_download_returns_first_pdf_document():
    page = SimpleNamespace(text=_viewer_page(PDF_BYTES), raise_for_status=Mock())
    get = Mock(side_effect=[SimpleNamespace(
        text=_conversation_html([_sandbox_block()]), raise_for_status=Mock()
    ), page])
    exporter = DocumentExporter(session=SimpleNamespace(get=get), cookies={"datr": "x"})
    result = exporter.download("FC4C2022-145C-4867-B38E-DC0867EABD3F", 0)
    assert result["file_bytes"] == PDF_BYTES
    assert result["file_name"] == "Generated.pdf"
    assert get.call_count == 2


def test_list_documents_endpoint_returns_download_urls(monkeypatch):
    client_stub = SimpleNamespace(
        cookies={"datr": "x"},
        access_token=None,
        user_agent="test-agent",
        client=SimpleNamespace(session=SimpleNamespace()),
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    monkeypatch.setattr(
        DocumentExporter,
        "list_documents",
        lambda self, conversation_id: [{
            "index": 0,
            "file_name": "Generated.pdf",
            "mime_type": "application/pdf",
            "file_extension": "pdf",
            "artifact_uuid": "a74a422b-1c36-419a-a58c-0928558d2ad4",
            "asset_id": "1448526705001842",
            "artifact_type": "PDF",
            "viewer_actions": ["CAN_DOWNLOAD"],
            "url": ASSET_URL,
        }],
    )
    response = TestClient(api_server.app).get("/conversations/conv-123/documents")
    assert response.status_code == 200
    body = response.json()
    assert body["documents"][0]["file_name"] == "Generated.pdf"
    assert body["documents"][0]["download_url"] == "/conversations/conv-123/documents/0/download"


def test_download_document_endpoint_streams_pdf(monkeypatch):
    client_stub = SimpleNamespace(
        cookies={"datr": "x"},
        client=SimpleNamespace(session=SimpleNamespace()),
        access_token=None,
        user_agent="test-agent",
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)
    monkeypatch.setattr(
        DocumentExporter,
        "download",
        lambda self, conversation_id, index: {
            "file_name": "Generated.pdf",
            "file_bytes": PDF_BYTES,
        },
    )
    response = TestClient(api_server.app).get("/conversations/conv-123/documents/0/download")
    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert response.headers["content-type"] == "application/pdf"
    assert "Generated.pdf" in response.headers["content-disposition"]


def test_download_document_endpoint_reports_missing_document(monkeypatch):
    client_stub = SimpleNamespace(
        cookies={"datr": "x"},
        client=SimpleNamespace(session=SimpleNamespace()),
        access_token=None,
        user_agent="test-agent",
    )
    monkeypatch.setattr(api_server.client_manager, "get_client", lambda: client_stub)

    def raise_error(self, conversation_id, index):
        raise DocumentExportError("This conversation has no downloadable Meta AI document")

    monkeypatch.setattr(DocumentExporter, "download", raise_error)
    response = TestClient(api_server.app).get("/conversations/conv-123/documents/0/download")
    assert response.status_code == 404

def test_fetch_document_pdf_handles_rsc_reference_payload():
    """The live page stores the model reference and the data in a flight row."""
    encoded = base64.b64encode(PDF_BYTES).decode()
    page = (
        '<script>self.__next_f.push([1,"9:[\\"$\\",\\"$L16\\",null,'
        '{\\"pdfBase64\\":\\"$17\\",\\"title\\":\\"generated.pdf\\"}]"])</script>'
        f'<script>self.__next_f.push([1,"17:T{len(encoded):x},{encoded}"])</script>'
    )
    session = SimpleNamespace(get=Mock(return_value=SimpleNamespace(
        text=page, raise_for_status=Mock()
    )))
    exporter = DocumentExporter(session=session)
    assert exporter.fetch_document_pdf(ASSET_URL) == PDF_BYTES
