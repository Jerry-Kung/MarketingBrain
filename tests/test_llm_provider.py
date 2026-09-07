"""LLM Provider 测试（用 httpx.MockTransport，不连真实 API）。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from app.llm.provider import LLMProvider, LLMResult, LLMUsage, LLMError


def _handler(request):
    body = json.loads(request.content)
    assert request.headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "test-model"
    payload = {
        "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }
    return httpx.Response(200, json=payload)


def _make_provider():
    transport = httpx.MockTransport(_handler)
    return LLMProvider(
        base_url="https://llm.example.com/v1",
        api_key="test-key",
        model="test-model",
        transport=transport,
    )


class TestLLMProvider:
    def test_chat_returns_content_and_usage(self):
        p = _make_provider()
        result = p.chat([{"role": "user", "content": "hi"}], max_tokens=10)
        assert isinstance(result, LLMResult)
        assert result.content == '{"ok": true}'
        assert result.usage.prompt_tokens == 100
        assert result.usage.completion_tokens == 50
        assert result.usage.total_tokens == 150
        assert result.usage.model == "test-model"

    def test_chat_url_and_auth(self):
        captured = []

        def handler(req):
            captured.append(req)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        transport = httpx.MockTransport(handler)
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        p.chat([{"role": "user", "content": "x"}])
        assert captured[0].url.path == "/v1/chat/completions"
        assert captured[0].headers["Authorization"] == "Bearer k"

    def test_chat_json_parses(self):
        p = _make_provider()
        data, result = p.chat_json([{"role": "user", "content": "hi"}])
        assert data == {"ok": True}
        assert result.usage.total_tokens == 150

    def test_chat_json_invalid_raises(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={
                "choices": [{"message": {"content": "not json at all"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        )
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        with pytest.raises(LLMError):
            p.chat_json([{"role": "user", "content": "hi"}])

    def test_http_error_raises_llm_error(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(500, json={"error": "boom"})
        )
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        with pytest.raises(LLMError):
            p.chat([{"role": "user", "content": "x"}])

    def test_chat_200_with_non_json_body_raises_llm_error(self):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, content=b"<html>gateway error</html>")
        )
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        with pytest.raises(LLMError):
            p.chat([{"role": "user", "content": "x"}])

    def test_from_settings(self, monkeypatch):
        from app.core.config import Settings
        monkeypatch.setenv("LLM_API_BASE", "https://llm.example.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "m")
        s = Settings()
        p = LLMProvider.from_settings(s)
        assert p.model == "m"
        assert p.base_url == "https://llm.example.com/v1"

    def test_chat_accepts_timeout_override(self):
        """Controller 冒烟修复：chat/chat_json 需支持按调用覆盖超时（长输出 >300s）。"""
        seen = {}
        def handler(request):
            seen["timeout"] = request.extensions.get("timeout")
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
        transport = httpx.MockTransport(handler)
        p = LLMProvider("https://llm.example.com/v1", "k", "m", transport=transport)
        # 不传入时用实例默认
        p.chat([{"role": "user", "content": "x"}])
        # 显式覆盖时不报错且可用（MockTransport 不实际等待）
        res = p.chat([{"role": "user", "content": "x"}], timeout=300.0)
        assert res.content == "ok"
