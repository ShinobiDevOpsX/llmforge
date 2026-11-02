from __future__ import annotations

from pydantic import BaseModel


class ModelSummary(BaseModel):
    name: str
    allowed: bool
    size_bytes: int | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None


class ModelListResponse(BaseModel):
    provider: str
    default_model: str
    models: list[ModelSummary]
