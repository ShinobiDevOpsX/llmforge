"""Structured logging and prompt privacy."""

from __future__ import annotations

import json


def log_lines(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_access_log_is_json_with_request_id(make_client, capsys):
    client = make_client(log_level="INFO")
    response = client.get("/health", headers={"X-Request-ID": "trace-me-1"})
    access = [r for r in log_lines(capsys) if r["message"] == "request completed"]
    assert access, "expected an access log line"
    record = access[-1]
    assert record["request_id"] == response.headers["x-request-id"] == "trace-me-1"
    assert record["route"] == "/health"
    assert record["status_code"] == 200
    assert "duration_ms" in record


def test_traceparent_trace_id_is_logged(make_client, capsys):
    client = make_client(log_level="INFO")
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    client.get("/health", headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"})
    access = [r for r in log_lines(capsys) if r["message"] == "request completed"]
    assert access[-1]["trace_id"] == trace_id


def test_prompts_are_never_logged(make_client, auth_headers, capsys):
    client = make_client(log_level="DEBUG")
    prompt = "TOP-SECRET-PROMPT-CONTENT"
    assert client.post("/v1/generate", json={"prompt": prompt}, headers=auth_headers).status_code == 200
    out = capsys.readouterr().out
    assert prompt not in out
    gen = [json.loads(line) for line in out.splitlines() if '"generation completed"' in line]
    assert gen[-1]["prompt_chars"] == len(prompt)


def test_api_key_is_never_logged(make_client, auth_headers, capsys):
    client = make_client(log_level="DEBUG")
    client.post("/v1/generate", json={"prompt": "hi"}, headers=auth_headers)
    client.post("/v1/generate", json={"prompt": "hi"}, headers={"X-API-Key": "attempted-wrong-key-123"})
    out = capsys.readouterr().out
    assert auth_headers["X-API-Key"] not in out
    assert "attempted-wrong-key-123" not in out


def test_text_log_format(make_client, capsys):
    client = make_client(log_level="INFO", log_format="text")
    client.get("/health")
    out = capsys.readouterr().out
    assert "request completed" in out
    assert not out.lstrip().startswith("{")
