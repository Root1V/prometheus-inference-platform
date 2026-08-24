"""OpenAI-compatible request/response schemas for the completions proxy.

Implements: memory/specs/001-gateway-core.md — allowlist approach (AC-6 security consideration)
Only explicitly declared fields are forwarded to llama.cpp — unknown fields are dropped.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator


class TextContentPart(BaseModel):
    type: Literal["text"]
    text: str


class ImageUrl(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def url_must_be_data_uri(cls, v: str) -> str:
        """RM-09: only inline base64 images are accepted.

        Allowing the backend to fetch an operator-unknown http(s) URL would turn
        /v1/chat/completions into an SSRF proxy for whatever the backend process
        can reach. Callers must inline the image as a data: URI instead.
        """
        if not v.startswith("data:image/"):
            raise ValueError(
                "image_url.url must be a data: URI (e.g. 'data:image/png;base64,...') — "
                "remote http(s) URLs are not allowed"
            )
        return v


class ImageContentPart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageUrl


ContentPart = Annotated[Union[TextContentPart, ImageContentPart], Field(discriminator="type")]


class ChatMessage(BaseModel):
    role: str
    content: str | list[ContentPart]

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


class EmbeddingsRequest(BaseModel):
    """Allowlist schema for /v1/embeddings — RM-09.

    Mirrors OpenAI's embeddings request shape (model + input only; the
    dimensions/encoding_format options some providers add are not supported).
    """

    model: str
    input: str | list[str]

    def to_llama_payload(self) -> dict[str, object]:
        return {"model": self.model, "input": self.input}
