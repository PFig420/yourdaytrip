"""Step 3 and the narrative: prompts, schemas and the calls to Claude (Planner.md 2.3, 5, 8).

Claude picks and orders places and suggests stay lengths. It never sees or writes a clock time, route or trip.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import anthropic

from .models import DayDraft, NarrativeOut

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Stable and first: this text must stay byte-identical between requests so the prompt cache can hit.
SYSTEM_PROMPT = """You plan one realistic day out by public transport for a visitor. You choose which places to visit, in what order, and how long to stay. Code then schedules the day with real timetables and checks it, so you never output clock times, routes or trip ids.

Rules, each with its reason:
1. Copy candidate ids exactly as written in the pack. Any other id fails the check.
2. Order stops so travel stays short. The travel_minutes table gives rough minutes between candidates (a missing pair means no good connection). Stops in the same cluster are within a short walk of each other.
3. A stay must fit inside one slot, because a visit that runs past closing fails. Slots are when a visit can happen, from opening hours and the first possible arrival: enter no later than the slot's enter_by, and make sure entry plus your stay ends by the slot's to. A place with closed_between is shut in that break, so pick the slot before or after it and never stay across it. A place with fixed_entry_times can only be entered at those times. Keep each stay between visit_min and visit_typical.
4. Include exactly one stop for each entry in day.meals_required, because the day is checked for every meal it spans. Use a food place whose meals include it, ordered so the meal starts inside its window; the food place's slots show when it is open.
5. Leave time to get home: the last stop must end well before its leave_by, because the way home may need a wait or a change.
6. Use between day.min_stops and day.max_stops stops; those limits are checked. A relaxed day stays near visit_typical and a packed day near visit_min.
7. Do not plan to reach a place more than 45 minutes before it opens, or before its next fixed entry, because waiting that long fails the check. earliest_arrival and each slot's from show when you can first be there, so if the day starts early, begin with a place that is already open. The one exception is the wait before dinner, which is allowed when the dinner place is a short walk from the previous stop.
8. Never output times, routes or trips. Only place ids, stay lengths in minutes and a short reason.
9. If you leave out something the visitor asked for, or accept a trade-off, say so in notes.

Choose places that match the visitor's interests and free text, then give the day a short title (8 words or fewer). Keep each why to 25 words or fewer and notes to 40 words or fewer."""

NARRATIVE_SYSTEM = """You write a short, friendly summary of a day plan that has already been checked. You receive the final timeline and the reason each stop was chosen.

Rules, each with its reason:
1. Use only times that appear in the timeline, written as HH:MM. Anything else will be rejected as invented.
2. Mention only the places in the plan. Do not suggest other places.
3. Do not mention prices, phone numbers or web addresses; they may be out of date.
4. summary: at most 80 words. One line per stop in stop_lines (use its place_id), at most 25 words each."""

REFUSAL = "refusal"


@dataclass
class CallResult:
    parsed: object = None
    stop_reason: str = ""
    error: str = ""             # "" or a short class description (never a key or request text)
    usage: dict = field(default_factory=dict)
    assistant: object = None    # the assistant turn to append on retry
    raw: str = ""               # a draft cut off at the output limit: the text so far (local backend, for debugging)


class LLM:
    def __init__(self, client=None, cfg: dict | None = None, backend: str = "api", runner=None):
        cfg = cfg or {}
        m = cfg.get("model", {})
        self.backend = backend
        self.fatal = ""         # set when the backend cannot continue at all (usage limit, not logged in)
        if backend == "api":
            self.client = client if client is not None else anthropic.Anthropic()
        elif backend in ("claude-code", "local"):
            self.client = None
        else:
            raise ValueError("backend must be 'api', 'claude-code' or 'local'")
        self._runner = runner      # claude-code: subprocess runner; local: httpx transport
        self.cfg_local = cfg.get("local", {})
        self.model = self.cfg_local.get("model_label", "local-planner") if backend == "local" else m.get("name", "claude-sonnet-5-5")
        self.effort = m.get("effort", "medium")
        self.narrative_effort = m.get("narrative_effort", "low")
        self.max_tokens = int(m.get("max_tokens", 16000))
        self.server_fallback = bool(m.get("server_fallback", True))
        self._cc = None
        self._local = None

    # ---- one call ----------------------------------------------------------
    def _parse(self, *, system, messages, schema, effort, label) -> CallResult:
        if self.backend == "claude-code":
            return self._parse_cc(system, messages, schema, effort, label)
        if self.backend == "local":
            return self._parse_local(system, messages, schema, effort, label)
        kw = dict(model=self.model, max_tokens=self.max_tokens, system=system, messages=messages,
                  output_format=schema, output_config={"effort": effort},
                  cache_control={"type": "ephemeral"})
        if self.server_fallback:
            kw.update(betas=[FALLBACK_BETA], fallbacks="default")
        t0 = time.perf_counter()
        out = CallResult()
        try:
            resp = self.client.beta.messages.parse(**kw)
        except anthropic.RateLimitError:
            out.error = "rate_limit"
        except anthropic.APIStatusError as e:
            out.error = f"api_status_{getattr(e, 'status_code', '')}"
        except anthropic.APIConnectionError:
            out.error = "connection"
        except ValueError:
            # the SDK could not parse the reply into the schema
            out.error = "unparseable"
        dt = time.perf_counter() - t0
        out.usage = {"label": label, "model": self.model, "latency_s": round(dt, 3)}
        if out.error:
            return out
        out.stop_reason = getattr(resp, "stop_reason", "") or ""
        u = getattr(resp, "usage", None)
        if u is not None:
            out.usage.update(input_tokens=getattr(u, "input_tokens", 0) or 0,
                             output_tokens=getattr(u, "output_tokens", 0) or 0,
                             cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                             cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0)
        out.usage["stop_reason"] = out.stop_reason
        if out.stop_reason == REFUSAL:
            return out
        if out.stop_reason == "max_tokens":
            return out
        out.parsed = getattr(resp, "parsed_output", None)
        out.assistant = _assistant_turn(resp, out.parsed)
        return out

    def _parse_cc(self, system, messages, schema, effort, label) -> CallResult:
        from .claude_code import ClaudeCodeBackend
        if self._cc is None:
            self._cc = ClaudeCodeBackend(self.model, self._runner)
        return self._parse_backend(self._cc, system, messages, schema, effort, label)

    def _parse_local(self, system, messages, schema, effort, label) -> CallResult:
        from .local import LocalBackend
        if self._local is None:
            self._local = LocalBackend(self.cfg_local, self._runner)
        return self._parse_backend(self._local, system, messages, schema, effort, label)

    def _parse_backend(self, backend, system, messages, schema, effort, label) -> CallResult:
        from .claude_code import BackendError
        out = CallResult()
        t0 = time.perf_counter()
        try:
            parsed, usage = backend.call(system=system, messages=messages, schema_cls=schema, effort=effort)
        except BackendError as e:
            out.usage = {"label": label, "model": self.model, "latency_s": round(time.perf_counter() - t0, 3),
                         "backend_error": str(e)[:400]}
            if e.kind == "max_tokens":
                # a cut-off draft is retried as a schema error, as on the Claude backends (Planner.md 7)
                out.stop_reason, out.raw = "max_tokens", e.partial
                return out
            out.error = e.kind
            if e.fatal:
                self.fatal = e.kind
            return out
        out.usage = {"label": label, **usage, "stop_reason": "end_turn"}
        out.stop_reason = "end_turn"
        out.parsed = parsed
        out.assistant = {"role": "assistant", "content": parsed.model_dump_json()}
        return out

    def draft(self, messages) -> CallResult:
        return self._parse(system=SYSTEM_PROMPT, messages=messages, schema=DayDraft, effort=self.effort, label="draft")

    def narrate(self, payload: str) -> CallResult:
        return self._parse(system=NARRATIVE_SYSTEM, messages=[{"role": "user", "content": payload}],
                           schema=NarrativeOut, effort=self.narrative_effort, label="narrative")

    def narrate_retry(self, payload: str, failed: CallResult, problems: str) -> CallResult:
        """Local backend only: the original turn, the failed answer, then the problems. The earlier turns are never edited,
        so the server's prefix cache is reused. Same system prompt, schema and token limit as the first call."""
        messages = [{"role": "user", "content": payload}, failed.assistant, {"role": "user", "content": problems}]
        return self._parse(system=NARRATIVE_SYSTEM, messages=messages, schema=NarrativeOut,
                           effort=self.narrative_effort, label="narrative_retry")


def _assistant_turn(resp, parsed):
    """The assistant turn to keep when a retry is needed (append only: the earlier turns are never edited)."""
    blocks = getattr(resp, "content", None)
    if blocks:
        out = []
        for b in blocks:
            d = b.model_dump(mode="json", exclude_none=True) if hasattr(b, "model_dump") else dict(b)
            d.pop("parsed_output", None)
            out.append(d)
        return {"role": "assistant", "content": out}
    text = parsed.model_dump_json() if parsed is not None else ""
    return {"role": "assistant", "content": text}


def first_message(pack_json: str) -> dict:
    return {"role": "user", "content": f"Candidate pack and request (JSON):\n{pack_json}\n\nPlan the day."}


def retry_message(errors) -> dict:
    lines = "\n".join(f"- {e.line()}" for e in errors)
    return {"role": "user", "content": "That draft did not pass the schedule check:\n" + lines +
            "\n\nReturn a corrected draft in the same format. Change only what the errors require."}
