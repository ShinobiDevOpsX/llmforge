"""Prometheus metric definitions.

Label sets are deliberately small and bounded: HTTP route *templates* (never
raw paths), status codes, provider names and allowlisted model names. Prompts,
API keys and client identifiers are never used as labels.
"""

from prometheus_client import Counter, Gauge, Histogram, Info

from app import __version__

REQUESTS_TOTAL = Counter(
    "llmforge_requests_total",
    "HTTP requests handled by the gateway.",
    ["method", "route", "status"],
)

REQUEST_DURATION = Histogram(
    "llmforge_request_duration_seconds",
    "End-to-end HTTP request latency.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120),
)

ACTIVE_REQUESTS = Gauge(
    "llmforge_active_requests",
    "HTTP requests currently in flight.",
)

INFERENCE_DURATION = Histogram(
    "llmforge_inference_duration_seconds",
    "Time spent waiting for the model runtime to produce a completion.",
    ["provider", "model", "outcome"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120, 240),
)

ACTIVE_GENERATIONS = Gauge(
    "llmforge_active_generations",
    "Generations currently being processed by the model runtime.",
    ["provider"],
)

INPUT_TOKENS = Counter(
    "llmforge_input_tokens_total",
    "Prompt tokens processed, as reported by the model runtime.",
    ["provider", "model"],
)

OUTPUT_TOKENS = Counter(
    "llmforge_output_tokens_total",
    "Completion tokens generated, as reported by the model runtime.",
    ["provider", "model"],
)

GENERATION_ERRORS = Counter(
    "llmforge_generation_errors_total",
    "Failed generation attempts by error type.",
    ["provider", "model", "error_type"],
)

MODEL_AVAILABILITY = Gauge(
    "llmforge_model_availability",
    "1 if the model is loaded in the runtime and usable, 0 otherwise.",
    ["provider", "model"],
)

RATE_LIMITED_TOTAL = Counter(
    "llmforge_rate_limited_total",
    "Requests rejected by the rate limiter.",
)

BUILD_INFO = Info("llmforge_build", "Build information.")
BUILD_INFO.info({"version": __version__})

# Status codes the /v1 API can return. Pre-creating these series means the
# first error of a kind is visible to rate() (a series that first appears with
# value N shows no increase), which keeps error-rate alerts reliable.
API_STATUS_CODES = ("200", "400", "401", "413", "422", "429", "500", "502", "503", "504")


def initialize_request_series(routes: list[tuple[str, str]]) -> None:
    """Export zero-valued request counters for (method, route template) pairs."""
    for method, route in routes:
        for status in API_STATUS_CODES:
            REQUESTS_TOTAL.labels(method, route, status)
