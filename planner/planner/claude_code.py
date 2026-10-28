"""Test backend: the local Claude Code CLI on Pedro's subscription (Planner.md 2.4). Local testing only.

One subprocess per planner or narrative call, in a fresh empty directory, user message on stdin. The CLI can only use
the Claude.ai login: API-key variables are removed from its environment and `--bare` is never passed.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REMOVED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
# any error that talks about a limit, quota or reset time is treated as the subscription running out: stopping is the safe reading
LIMIT = re.compile(r"\blimits?\b|too many requests|quota|out of (extra )?usage|resets? (at|in)", re.I)
LOGIN = re.compile(r"not logged in|please run /login|/login|invalid api key|authentication", re.I)
TIMEOUT_S = 900


class BackendError(Exception):
    """kind: usage_limit | not_logged_in | cli_failed | no_structured_output | timeout | no_binary

    The local backend (local.py) also uses: connection | http_status_<n> | context_overflow | max_tokens"""

    def __init__(self, kind: str, message: str = ""):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind, self.message = kind, message
        self.partial = ""          # max_tokens (local backend): the text generated before the cut-off, at most 4,000 characters

    @property
    def fatal(self) -> bool:
        return self.kind in ("usage_limit", "not_logged_in", "no_binary", "connection")


def find_binary() -> str:
    """`claude` on PATH, else the newest VS Code extension copy, else PLANNER_CLAUDE_BIN."""
    found = shutil.which("claude")
    if found:
        return found
    cands = glob.glob(str(Path.home() / ".vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"))
    if cands:
        def ver(p):
            m = re.search(r"claude-code-([\d.]+)", p)
            return tuple(int(x) for x in m.group(1).split(".") if x.isdigit()) if m else ()
        return sorted(cands, key=ver)[-1]
    env = os.environ.get("PLANNER_CLAUDE_BIN")
    if env and Path(env).exists():
        return env
    raise BackendError("no_binary", "claude not found on PATH, in the VS Code extension folder, or in PLANNER_CLAUDE_BIN")


def clean_env(env=None) -> dict:
    out = dict(os.environ if env is None else env)
    for k in REMOVED_ENV:
        out.pop(k, None)
    return out


def command(binary: str, model: str, effort: str, system: str, schema: dict) -> list[str]:
    return [binary, "-p", "--model", model, "--effort", effort, "--system-prompt", system,
            "--json-schema", json.dumps(schema, separators=(",", ":")), "--output-format", "json",
            "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]


def subprocess_runner(cmd, stdin, env, cwd, timeout=TIMEOUT_S):
    """-> (returncode, stdout, stderr). Replaced by a fake in tests."""
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def render_messages(messages) -> str:
    """The user message, or the retry history as text (the CLI takes one prompt, not a message list)."""
    if len(messages) == 1:
        return _text(messages[0])
    parts = []
    for m in messages:
        who = "Visitor request" if m["role"] == "user" else "Your previous draft"
        parts.append(f"[{who}]\n{_text(m)}")
    return "\n\n".join(parts)


def _text(m) -> str:
    c = m["content"]
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")


class ClaudeCodeBackend:
    def __init__(self, model: str, runner=None, binary: str | None = None):
        self.model = model
        self.runner = runner or subprocess_runner
        self._binary = binary
        self.last_raw = None

    @property
    def binary(self) -> str:
        if self._binary is None:
            self._binary = find_binary()
        return self._binary

    def call(self, *, system: str, messages, schema_cls, effort: str) -> tuple[object, dict]:
        """-> (parsed pydantic object, usage dict). Raises BackendError."""
        cmd = command(self.binary, self.model, effort, system, schema_cls.model_json_schema())
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="planner-cc-") as cwd:
            try:
                rc, out, err = self.runner(cmd, render_messages(messages), clean_env(), cwd)
            except subprocess.TimeoutExpired:
                raise BackendError("timeout", f"no answer after {TIMEOUT_S} s") from None
            except OSError as e:
                raise BackendError("cli_failed", type(e).__name__) from None
        latency = time.perf_counter() - t0
        try:
            data = json.loads(out) if out.strip() else {}
        except ValueError:
            data = {}
        self.last_raw = data
        text = " ".join(str(x) for x in (data.get("result"), err) if x)
        usage = _usage(data, latency, self.model)
        if rc != 0 or data.get("is_error") or not data:
            if LOGIN.search(text) and not LIMIT.search(text):
                raise BackendError("not_logged_in", "run `claude` once and log in with your Claude.ai account")
            if LIMIT.search(text):
                raise BackendError("usage_limit", text[:300])
            raise BackendError("cli_failed", f"exit {rc}: {text[:300]}")
        if data.get("structured_output") is None:
            if LIMIT.search(text):
                raise BackendError("usage_limit", text[:300])
            raise BackendError("no_structured_output", text[:300])
        try:
            return schema_cls.model_validate(data["structured_output"]), usage
        except ValueError as e:
            raise BackendError("no_structured_output", f"answer did not fit the schema: {str(e)[:200]}") from None


def _usage(data: dict, latency: float, model: str) -> dict:
    u = data.get("usage") or {}
    return {"model": model, "latency_s": round(latency, 3),
            "input_tokens": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
            "output_tokens": u.get("output_tokens") or 0,
            "cache_read_tokens": u.get("cache_read_input_tokens") or 0,
            "cache_write_tokens": u.get("cache_creation_input_tokens") or 0,
            "notional_usd": data.get("total_cost_usd"), "cli_duration_ms": data.get("duration_ms")}
