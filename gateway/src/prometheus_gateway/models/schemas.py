"""OpenAI-compatible request/response schemas for the completions proxy.

Implements: memory/specs/001-gateway-core.md — allowlist approach (AC-6 security consideration)
Only explicitly declared fields are forwarded to llama.cpp — unknown fields are dropped.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class FunctionCall(BaseModel):
    name: str
    arguments: str  # JSON-encoded string, per the OpenAI tool-calling shape


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class ChatMessage(BaseModel):
    role: str
    # RM-35: content is optional — an assistant message that only calls a tool has
    # content: null, matching OpenAI's shape.
    content: str | list[ContentPart] | None = None
    # RM-35: set on an assistant message that's calling one or more tools.
    tool_calls: list[ToolCall] | None = None
    # RM-35: set on a "tool" role message — which tool_calls entry this is answering.
    tool_call_id: str | None = None

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        allowed = {"system", "user", "assistant", "tool"}
        if v not in allowed:
            raise ValueError(f"role must be one of {allowed}, got {v!r}")
        return v


class FunctionDefinition(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, object] | None = None  # JSON Schema object


class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: FunctionDefinition


def ignored_parameters(body: BaseModel) -> list[str]:
    """Fields the caller sent that this gateway will not act on — PRM-127.

    Every request schema here is an allowlist that now *accepts* extras rather
    than dropping them unseen, so this is the one place that turns "Pydantic
    kept it aside" into an answer: the names, sorted, for the response header
    and for the `require_parameters` refusal.

    `require_parameters` itself is a declared field, so it never appears here —
    asking to be told is not one of the things you can be told about.
    """
    return sorted(body.model_extra or {})


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
    # RM-35: native tool-calling — forwarded as-is to llama.cpp, which does the actual
    # grammar-constrained generation and tool_calls parsing. The gateway only proxies.
    tools: list[ToolDefinition] | None = None
    tool_choice: str | dict[str, object] | None = None
    # PRM-126: structured outputs. llama.cpp constrains generation to the schema
    # — verified against a live backend, which returned {"capital": "Lima"} for a
    # two-field schema. Forwarded as-is for the same reason `tools` is: the
    # engine does the grammar work and validating the schema here would be a
    # second, drifting copy of its rules.
    response_format: dict[str, object] | None = None
    # PRM-187: Apeiron's `P2` #12 — how confident the grounder was, so an agent can
    # decide when to ask a human. llama.cpp implements both, so this is a
    # forward, not a feature: `logprobs` turns them on and `top_logprobs` asks for
    # the N alternatives per position.
    #
    # Declared rather than left to `extra`, for the reason `raw_scores` was: an
    # undeclared field is reported as ignored on **every** engine, including the
    # one that honours it. Where a backend does not implement it the engine's own
    # answer stands — this gateway does not emulate it, and a caller who needs to
    # know asks with `require_parameters`.
    logprobs: bool | None = None
    top_logprobs: int | None = Field(default=None, ge=0, le=20)

    # PRM-127: accepted and reported, not refused — OpenRouter's model rather
    # than OpenAI's. A gateway in front of engines that differ in what they
    # honour should not turn an unsupported parameter into a failed request:
    # the caller usually still wants the completion. But OpenRouter can afford
    # to ignore quietly because its clients can look up what each provider
    # supports; ours could not, so ignoring here meant a setting that did
    # nothing and said nothing.
    #
    # `extra="allow"` keeps the allowlist's actual guarantee — `to_llama_payload`
    # names every field it forwards, so an unknown one still never reaches the
    # engine (AC-5/AC-6) — while letting the gateway *see* what it is dropping
    # and name it in `X-Prometheus-Ignored-Parameters`.
    model_config = ConfigDict(extra="allow")

    # PRM-127: OpenRouter's `provider.require_parameters`, one level flatter.
    # Off by default, so nothing that works today stops working. On, an
    # unhonoured parameter is a 400 instead of a silent drop — for the caller
    # who would rather fail than be quietly given something else, which is the
    # right default for nobody and the right option for anyone doing
    # reproducibility or structured extraction.
    require_parameters: bool = False

    @model_validator(mode="after")
    def top_logprobs_needs_logprobs(self) -> ChatCompletionRequest:
        """PRM-187: the pairing rule is the engine's, and it is enforced here.

        Measured against a running llama-server: `top_logprobs` without
        `logprobs: true` is answered `400 top_logprobs requires logprobs to be
        set to true` — and `logprobs: false` is rejected the same way, so the
        flag must be present *and* true, not merely absent.

        This is one of the few places the gateway checks a pairing instead of
        forwarding and letting the engine answer. The reason is PRM-174: that
        400 would reach the caller in llama.cpp's error shape, not this
        gateway's problem+json envelope, and an error that leaves by a
        different door is the thing PRM-174 closed. Rejecting here costs one
        comparison and keeps every refusal in one shape.
        """
        if self.top_logprobs is not None and not self.logprobs:
            raise ValueError("top_logprobs requires logprobs to be true")
        return self

    def to_llama_payload(self) -> dict[str, object]:
        """Serialise to a dict suitable for forwarding — drops None fields."""
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [m.model_dump(exclude_none=True) for m in self.messages],
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
        if self.tools is not None:
            payload["tools"] = [t.model_dump(exclude_none=True) for t in self.tools]
        if self.tool_choice is not None:
            payload["tool_choice"] = self.tool_choice
        if self.response_format is not None:
            payload["response_format"] = self.response_format
        if self.logprobs is not None:
            payload["logprobs"] = self.logprobs
        if self.top_logprobs is not None:
            payload["top_logprobs"] = self.top_logprobs
        return payload


class EmbeddingsRequest(BaseModel):
    """Allowlist schema for /v1/embeddings — RM-09.

    Mirrors OpenAI's embeddings request shape (model + input only; the
    dimensions/encoding_format options some providers add are not supported).
    """

    model_config = ConfigDict(extra="allow")  # PRM-127

    model: str
    input: str | list[str]
    require_parameters: bool = False

    def to_llama_payload(self) -> dict[str, object]:
        return {"model": self.model, "input": self.input}


class RerankRequest(BaseModel):
    """Allowlist schema for /v1/rerank — PRM-106.

    The shape is the de-facto one (Cohere, Jina, and llama.cpp's own /rerank):
    one query, N documents, scores back with the original indices. Deliberately
    not a chat request with a scoring prompt — that is what a client had to do
    without this endpoint, and it forced them to reconstruct the score from
    logprobs, prefill the chat template by hand, and spend one request per
    document against the rate limit.
    """

    model_config = ConfigDict(extra="allow")  # PRM-127

    model: str
    query: str
    documents: list[str]
    require_parameters: bool = False
    # Cohere calls this top_n; keep the name callers already use. None = all.
    top_n: int | None = None
    # PRM-183: the score before the sigmoid. Asked for in Centinela's `C-01 §2`,
    # because a reranker's probabilities saturate near 1.0 — they measured 0.99
    # for a document only loosely related — and a saturated probability cannot be
    # calibrated while the logit behind it can.
    #
    # **Declared, not an extra, and that is a decision.** An undeclared field
    # would be reported by `ignored_parameters` on every engine, including the one
    # that honours it. Declared, it is honoured where the engine has it and named
    # in `X-Prometheus-Ignored-Parameters` where it does not — see the engine-aware
    # check in the handler, which is a second reason that header can fire.
    raw_scores: bool | None = None

    def to_llama_payload(self) -> dict[str, object]:
        """Deprecated by PRM-183 — `rerank_dialects` builds the upstream body now.

        Kept because dropping a public method of a request model is a separate
        change from making the upstream shape per-engine, and nothing in the
        gateway calls it any more.
        """
        payload: dict[str, object] = {
            "model": self.model,
            "query": self.query,
            "documents": self.documents,
        }
        if self.top_n is not None:
            payload["top_n"] = self.top_n
        return payload


class ImageGenerationRequest(BaseModel):
    """Allowlist schema for /v1/images/generations — RM-38.

    Mirrors OpenAI's images request shape (model/prompt plus the optional
    n/size the sd-server backend also accepts).
    """

    model_config = ConfigDict(extra="allow")  # PRM-127

    model: str
    prompt: str
    n: int | None = None
    size: str | None = None
    require_parameters: bool = False

    def to_backend_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"model": self.model, "prompt": self.prompt}
        if self.n is not None:
            payload["n"] = self.n
        if self.size is not None:
            payload["size"] = self.size
        return payload
