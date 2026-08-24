"""OpenAI-compatible request/response schemas for the completions proxy.

Implements: memory/specs/001-gateway-core.md — allowlist approach (AC-6 security consideration)
Only explicitly declared fields are forwarded to llama.cpp — unknown fields are dropped.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        allowed = {"system", "user", "assistant", "tool"}
        if v not in allowed:
            raise ValueError(f"role must be one of {allowed}, got {v!r}")
        return v


class ChatCompletionRequest(BaseModel):
    """Allowlist schema — only these fields are forwarded to llama.cpp.

    Implements: memory/specs/001-gateway-core.md — AC-5, AC-6, security considerations
    """

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    stop: list[str] | str | None = None

    def to_llama_payload(self) -> dict[str, object]:
        """Serialise to a dict suitable for forwarding — drops None fields."""
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [m.model_dump() for m in self.messages],
            "stream": self.stream,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        if self.stop is not None:
            payload["stop"] = self.stop
        return payload
