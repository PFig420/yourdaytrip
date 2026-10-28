"""Local backend: a self-hosted llama-server speaking the OpenAI-compatible chat endpoint (Planner.md 2.5).

It does what the Claude backends do and nothing more: one schema-constrained call, parsed into the same Pydantic model.
Server setup lives in /home/pedro/selfhostedLLM. Any failure is a BackendError, so it ends in a retry or the fallback plan.
"""
from __future__ import annotations

import copy
import json
import os
import time

import httpx

from .claude_code import BackendError
from .models import NarrativeOut

DEFAULTS = {"base_url": "http://127.0.0.1:8080", "model_alias": "local-planner", "timeout_s": 900, "max_tokens": 2048,
            "narrative_max_tokens": 1024, "temperature": 0.3, "top_p": 0.8, "seed": 42, "api_key_env": "LOCAL_LLM_API_KEY"}
# keywords llama.cpp's schema-to-grammar converter may reject; only dropped when the server refuses the full schema
PARTIAL_MAX = 4000
_UNSUPPORTED = {"title", "default", "description", "examples"}


def simplify_schema(schema: dict) -> dict:
    """A copy without annotation keywords. The Pydantic models are never changed; code re-checks every limit anyway."""
    def walk(x):
        if isinstance(x, dict):
            return {k: ({n: walk(v) for n, v in val.items()} if k in ("properties", "$defs") else walk(val))
                    for k, val in x.items() if k not in _UNSUPPORTED}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(copy.deepcopy(schema))


def _text(m) -> str:
    c = m["content"]
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")


def to_chat_messages(system: str, messages) -> list[dict]:
    return [{"role": "system", "content": system}] + [{"role": m["role"], "content": _text(m)} for m in messages]


class LocalBackend:
    def __init__(self, cfg: dict | None = None, transport=None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.transport = transport
        self.last_raw = None

    @property
    def label(self) -> str:
        return self.cfg.get("model_label") or self.cfg["model_alias"]

    def _headers(self) -> dict:
        key = os.environ.get(self.cfg.get("api_key_env") or "", "")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _post(self, body: dict) -> httpx.Response:
        url = self.cfg["base_url"].rstrip("/") + "/v1/chat/completions"
        try:
            with httpx.Client(transport=self.transport, timeout=float(self.cfg["timeout_s"])) as c:
                return c.post(url, json=body, headers=self._headers())
        except httpx.TimeoutException:
            raise BackendError("timeout", f"no answer after {self.cfg['timeout_s']} s") from None
        except httpx.TransportError as e:
            raise BackendError("connection", type(e).__name__) from None

    def health(self) -> bool:
        try:
            with httpx.Client(transport=self.transport, timeout=5) as c:
                return c.get(self.cfg["base_url"].rstrip("/") + "/health", headers=self._headers()).status_code == 200
        except httpx.HTTPError:
            return False

    def call(self, *, system: str, messages, schema_cls, effort: str = "n/a") -> tuple[object, dict]:
        """-> (parsed pydantic object, usage dict). Raises BackendError. `effort` is ignored: the model has none."""
        c = self.cfg
        schema = schema_cls.model_json_schema()
        body = {"model": c["model_alias"], "messages": to_chat_messages(system, messages), "stream": False,
                "temperature": c["temperature"], "top_p": c["top_p"], "seed": c["seed"],
                "max_tokens": c["narrative_max_tokens"] if schema_cls is NarrativeOut else c["max_tokens"],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": schema_cls.__name__, "strict": True, "schema": schema}}}
        t0 = time.perf_counter()
        r = self._post(body)
        if r.status_code == 400 and not _is_overflow(r):
            # the server may not accept some schema keyword: retry once with annotations removed
            body["response_format"]["json_schema"]["schema"] = simplify_schema(schema)
            r = self._post(body)
        latency = time.perf_counter() - t0
        if not 200 <= r.status_code < 300:
            if _is_overflow(r):
                raise BackendError("context_overflow", r.text[:300])
            raise BackendError(f"http_status_{r.status_code}", r.text[:300])
        try:
            data = r.json()
            choice = data["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError):
            raise BackendError("no_structured_output", "the reply was not a chat completion") from None
        self.last_raw = data
        usage = _usage(data, latency, self.label)
        content = (choice.get("message") or {}).get("content") or ""
        if choice.get("finish_reason") == "length":
            err = BackendError("max_tokens", f"stopped at {c['max_tokens']} output tokens")
            err.partial = content[:PARTIAL_MAX]            # kept for debugging; never sent back to the model
            raise err
        try:
            return schema_cls.model_validate_json(content), usage
        except ValueError as e:
            raise BackendError("no_structured_output", f"{str(e)[:200]} | {content[:120]}") from None


def _is_overflow(r: httpx.Response) -> bool:
    t = r.text.lower()
    return r.status_code in (400, 413) and ("exceed_context" in t or ("context" in t and ("exceed" in t or "too long" in t)))


def _usage(data: dict, latency: float, label: str) -> dict:
    u = data.get("usage") or {}
    t = data.get("timings") or {}
    cached = t.get("cache_n")
    if cached is None:
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return {"model": label, "effort": "n/a", "latency_s": round(latency, 3),
            "input_tokens": u.get("prompt_tokens") or 0, "output_tokens": u.get("completion_tokens") or 0,
            "cache_read_tokens": cached or 0, "cache_write_tokens": 0, "notional_usd": None,
            "prompt_tps": round(t["prompt_per_second"], 2) if t.get("prompt_per_second") else None,
            "gen_tps": round(t["predicted_per_second"], 2) if t.get("predicted_per_second") else None}
