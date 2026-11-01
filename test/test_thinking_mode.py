"""Tests for the default chat reasoning mode in the SDK and REST API."""

import asyncio
from unittest.mock import Mock

from metaai_api.api_server import ChatRequest, chat, client_manager
from metaai_api.main import MetaAI


def _fake_ai():
    ai = MetaAI.__new__(MetaAI)
    ai._get_browser = Mock()
    ai._browser = Mock()
    ai._browser.send_message.return_value = {
        "text": "reply",
        "conversation_id": "conversation-1",
    }
    return ai


def test_sdk_prompt_defaults_to_thinking():
    ai = _fake_ai()

    ai.prompt("hello", new_conversation=False)

    ai._browser.send_message.assert_called_once_with(
        "hello", timeout=60, thinking_mode=True
    )


def test_sdk_prompt_can_select_instant():
    ai = _fake_ai()

    ai.prompt("hello", new_conversation=False, thinking_mode=False)

    ai._browser.send_message.assert_called_once_with(
        "hello", timeout=60, thinking_mode=False
    )


def test_rest_chat_defaults_to_thinking(monkeypatch):
    client = Mock()
    client.prompt.return_value = {
        "message": "reply",
        "conversation_id": "conversation-1",
    }
    monkeypatch.setattr(client_manager, "get_client", lambda: client)

    result = asyncio.run(chat(ChatRequest(message="hello"), _=True))

    assert result["success"] is True
    assert client.prompt.call_args.kwargs["thinking_mode"] is True


def test_rest_chat_can_select_instant(monkeypatch):
    client = Mock()
    client.prompt.return_value = {
        "message": "reply",
        "conversation_id": "conversation-1",
    }
    monkeypatch.setattr(client_manager, "get_client", lambda: client)

    req = ChatRequest(message="hello", thinking_mode=False)
    result = asyncio.run(chat(req, _=True))

    assert result["success"] is True
    assert client.prompt.call_args.kwargs["thinking_mode"] is False
