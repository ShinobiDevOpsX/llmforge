from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Hard upper bound enforced by the schema; the configurable, usually lower,
# limit (LLMFORGE_MAX_PROMPT_CHARS) is enforced by the generation service.
ABSOLUTE_MAX_PROMPT_CHARS = 100_000


class GenerateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={"examples": [{"prompt": "Explain Kubernetes", "model": "llama3.2:1b"}]},
    )

    prompt: str = Field(min_length=1, max_length=ABSOLUTE_MAX_PROMPT_CHARS)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:/-]+$",
        description="Model name. Defaults to the configured default model.",
    )
    max_tokens: int | None = Field(default=None, ge=1, description="Upper bound on generated tokens.")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class GenerateResponse(BaseModel):
    model: str
    response: str
    prompt_tokens: int | None = Field(description="Null when the runtime did not report it.")
    completion_tokens: int | None = Field(description="Null when the runtime did not report it.")
    duration_seconds: float
