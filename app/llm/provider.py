"""LLM Provider。

用 httpx 直连 OpenAI-compatible API，不引入 openai SDK。
支持 chat / chat_json，记录 token 与耗时。测试时注入 httpx.MockTransport。
"""
import json
import time
from dataclasses import dataclass, field

import httpx


class LLMError(Exception):
    """LLM 调用或输出解析失败。"""


@dataclass
class LLMUsage:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
        }


@dataclass
class LLMResult:
    content: str
    usage: LLMUsage
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"content": self.content, "usage": self.usage.to_dict()}


class LLMProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 120.0,
        temperature: float = 0.2,
        transport=None,
    ):
        if not base_url or not api_key or not model:
            raise ValueError("LLM 配置缺失：base_url/api_key/model 均必填")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self._transport = transport  # None -> 默认 httpx.Client

    @classmethod
    def from_settings(cls, settings) -> "LLMProvider":
        return cls(
            base_url=settings.LLM_API_BASE,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            timeout=settings.llm_timeout,
        )

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, transport=self._transport)

    def chat(self, messages, *, response_format=None, temperature=None,
             max_tokens=None) -> LLMResult:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if response_format:
            body["response_format"] = response_format
        if max_tokens:
            body["max_tokens"] = max_tokens

        url = f"{self.base_url}/chat/completions"
        started = time.monotonic()
        try:
            with self._client() as client:
                resp = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as e:
            raise LLMError(f"LLM 请求失败: {e}") from e
        latency_ms = int((time.monotonic() - started) * 1000)

        if resp.status_code != 200:
            raise LLMError(f"LLM 返回 {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"LLM 响应格式异常: {e}; 原文 {str(data)[:300]}") from e

        usage_raw = data.get("usage", {})
        usage = LLMUsage(
            model=self.model,
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
            completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            total_tokens=int(usage_raw.get("total_tokens", 0)),
            latency_ms=latency_ms,
        )
        return LLMResult(content=content, usage=usage, raw=data)

    def chat_json(self, messages, **kw) -> tuple[dict, LLMResult]:
        result = self.chat(
            messages,
            response_format={"type": "json_object"},
            **kw,
        )
        try:
            data = json.loads(result.content)
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM 输出不是合法 JSON: {e}; 原文 {result.content[:300]}") from e
        if not isinstance(data, dict):
            raise LLMError(f"LLM 输出应为 JSON 对象，得到 {type(data).__name__}")
        return data, result
