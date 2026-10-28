"""The planner: pack -> Claude draft -> scheduler/validator (retry) -> fallback -> narrative -> DayPlan."""
from __future__ import annotations

import datetime as dt
import re
import time
import tomllib
import zipfile
from pathlib import Path

from . import fallback as fb
from . import freetime
from . import narrative as nar
from . import pack as packmod
from .llm import LLM, REFUSAL, first_message, retry_message
from .models import DISCLAIMERS, DayPlan, PlanRequest, Usage, Visit
from .schedule import Err, Router, Scheduler
from .timeutil import iso
from .validate import validate_draft

ROOT = Path(__file__).resolve().parents[2]


def _feed_dates(engine) -> str:
    parts = []
    for f in engine.feeds:
        date = None
        try:
            with zipfile.ZipFile(f.path) as z:
                if "feed_info.txt" in z.namelist():
                    m = re.search(r"\b(\d{8})-", z.read("feed_info.txt").decode("utf-8", "ignore"))
                    if m:
                        d = m.group(1)
                        date = f"built {d[:4]}-{d[4:6]}-{d[6:]}"
        except (OSError, zipfile.BadZipFile):
            pass
        valid = f"valid {f.first_date[:4]}-{f.first_date[4:6]}-{f.first_date[6:]} to {f.last_date[:4]}-{f.last_date[4:6]}-{f.last_date[6:]}"
        parts.append(f"{f.id} {date + ', ' if date else ''}{valid}")
    return "; ".join(parts)


class Planner:
    def __init__(self, engine, places, cfg: dict, client=None, accepted_legs=None, backend="api", runner=None):
        self.engine, self.lib, self.cfg = engine, places, cfg
        self.backend, self._runner = backend, runner
        self._client = client
        self._llm = None
        self.router = Router(engine)
        self.sched = Scheduler(engine, places, cfg, self.router)
        self.accepted_legs = accepted_legs
        self._attempt_errors = []
        self._attempt_drafts = []
        self._accepted_draft = None
        self.tags = set(places.cfg.vocab.get("tags", [])) | set(places.cfg.vocab.get("cuisine", []))
        self.tags |= set(places.cfg.vocab.get("sight_categories", [])) | set(places.cfg.vocab.get("food_categories", []))

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = LLM(self._client, self.cfg, self.backend, self._runner)
        return self._llm

    @property
    def model(self) -> str:
        if self.backend == "local":
            return self.cfg.get("local", {}).get("model_label", "local-planner")
        return self.cfg.get("model", {}).get("name", "claude-sonnet-5-5")

    @property
    def compact_pack(self) -> bool:
        """The compact pack is for the local backend only; the Claude backends always get the full pack."""
        return self.backend == "local" and self.cfg.get("pack", {}).get("profile", "full") == "compact"

    # ------------------------------------------------------------------------
    def plan(self, req: PlanRequest, use_llm: bool = True, debug: bool = False) -> DayPlan:
        t0 = time.perf_counter()
        req.check_interests(self.tags)
        usage = Usage()
        self._attempt_errors, self._attempt_drafts, self._accepted_draft = [], [], None
        pack = packmod.build(self.lib, self.engine, req, self.cfg, compact=self.compact_pack)
        warnings = [w for w in pack.warnings if not w.startswith(("hours last checked", "draft records"))]
        if len(pack.candidates) < 2:
            errs = [Err("no_journey", "fewer than two places can be reached and visited within the requested hours")]
            return self._no_plan(req, errs, warnings, usage, t0, "fallback", 0, debug)

        stops, res, attempts, source = None, None, 0, "fallback"
        last_errs: list[Err] = []
        draft_title = ""
        whys: dict = {}
        if use_llm:
            stops, res, attempts, last_errs, draft_title, whys, why_failed = self._llm_loop(req, pack, usage)
            if stops is not None:
                source = "llm"
            else:
                warnings.append("Built without the language model: " + why_failed)
        else:
            warnings.append("Built without the language model (fallback plan).")
        if stops is None:
            stops, fres = fb.build(self.sched, pack, req)
            if stops is None:
                errs = fres.errors if fres is not None else last_errs
                return self._no_plan(req, errs, warnings, usage, t0, "fallback", attempts, debug)
            res = fres
            whys = {c["id"]: _fallback_why(pack.items[c["id"]]) for c in pack.candidates}
            draft_title = _fallback_title(req)
        assert not res.errors      # nothing is shown that has not passed the validator
        return self._finish(req, pack, stops, res, source, attempts, usage, warnings, draft_title, whys, t0, debug)

    # ------------------------------------------------------------------------
    def _llm_loop(self, req, pack, usage):
        retries = int(self.cfg.get("retries", {}).get("max_retries", 2))
        messages = [first_message(pack.dumps())]
        errs: list[Err] = []
        attempts = 0
        why_failed = ""
        for _ in range(1 + retries):
            cr = self.llm.draft(messages)
            attempts += 1
            _add_usage(usage, cr.usage)
            if cr.error:
                why_failed = f"the model call failed ({cr.error})."
                return None, None, attempts, errs, "", {}, why_failed
            if cr.stop_reason == REFUSAL:
                return None, None, attempts, errs, "", {}, "the model declined the request."
            if cr.parsed is None:
                errs = [Err("schema", "the reply was cut off or was not valid JSON for the draft format")]
                self._attempt_errors.append(["schema"])
                entry = {"draft": None, "errors": [e.to_json() for e in errs]}
                if cr.raw:
                    entry = {"draft": None, "raw": cr.raw, "errors": entry["errors"]}
                self._attempt_drafts.append(entry)
                res = None
            else:
                errs, res = validate_draft(self.sched, req, cr.parsed, pack.ids, pack)
                if errs:
                    self._attempt_errors.append([e.code for e in errs])
                    self._attempt_drafts.append({"draft": self._draft_json(cr.parsed), "errors": [e.to_json() for e in errs]})
                if not errs:
                    draft = cr.parsed
                    self._accepted_draft = self._draft_json(draft)
                    return ([(s.place_id, s.visit_minutes) for s in draft.stops], res, attempts, [],
                            draft.title, {s.place_id: s.why for s in draft.stops}, "")
            if cr.assistant is None:
                messages.append({"role": "assistant", "content": "{}"})
            else:
                messages.append(cr.assistant)
            messages.append(retry_message(errs))
        codes = sorted({e.code for e in errs})
        return None, None, attempts, errs, "", {}, f"all {attempts} drafts failed the schedule check ({', '.join(codes)})."

    # ------------------------------------------------------------------------
    def _debug(self, on: bool):
        """Testing output: the accepted draft (null for a fallback or no plan) and every failed draft."""
        if not on:
            return None
        return {"draft": getattr(self, "_accepted_draft", None), "attempt_drafts": list(getattr(self, "_attempt_drafts", []))}

    def _no_plan(self, req, errs, warnings, usage, t0, source, attempts, debug=False):
        usage.latency_s = round(time.perf_counter() - t0, 3)
        d = dt.date.fromisoformat(req.date)
        cov = self.engine.coverage(d)
        warnings = list(dict.fromkeys(list(cov.warnings) + warnings))
        return DayPlan(request=req, status="no_plan", source=source, model=None, backend=self.backend, attempts=attempts,
                       warnings=warnings, errors=[e.to_json() for e in errs], usage=usage,
                       attempt_errors=list(getattr(self, "_attempt_errors", [])),
                       debug=self._debug(debug), disclaimers=self._disclaimers())

    def _disclaimers(self):
        return [DISCLAIMERS[0], DISCLAIMERS[1].format(feeds=_feed_dates(self.engine)), DISCLAIMERS[2]]

    def _draft_json(self, draft) -> dict:
        """A draft as kept in DayPlan.debug, with place names for reading (unknown ids keep no name)."""
        d = draft.model_dump(mode="json")
        for s in d["stops"]:
            p = self.lib.places.get(s["place_id"])
            s["name"] = p.name if p else None
        return d

    def _finish(self, req, pack, stops, res, source, attempts, usage, warnings, title, whys, t0, debug=False):
        d = dt.date.fromisoformat(req.date)
        safe = self.sched.safe_return(req, res)
        plan_b = self.sched.plan_b(req, res)
        w = list(warnings)
        w += list(pack.shortlist.coverage.warnings)
        w += res.warnings
        w += self.sched.accepted_leg_warnings(res, self.accepted_legs)
        stale = [p.name for p, *_ in res.visits
                 if p.provenance.checked is not None and (d - p.provenance.checked).days > self.lib.cfg.stale_days]
        if stale:
            w.append(f"Opening hours were last checked over {self.lib.cfg.stale_days} days ago for: " + ", ".join(stale) + ".")
        drafts = [p.name for p, *_ in res.visits if p.status != "confirmed"]
        if drafts:
            w.append("Draft place records (hours not confirmed): " + ", ".join(drafts) + ".")
        w += list(self.engine.warnings)
        res.timeline = freetime.apply(self.sched, req, res)      # after validation: long waits become free time
        for e in res.timeline:
            if isinstance(e, Visit) and not e.reason:
                e.reason = whys.get(e.place_id, "")     # the model's why, or the fallback's shortlist reasons
        plan = DayPlan(request=req, source=source, model=self.model if source == "llm" else None, backend=self.backend, attempts=attempts,
                       title=title, timeline=res.timeline, home_by=iso(res.home_arrive) if res.home_arrive else None,
                       last_safe_return=safe, plan_b=plan_b, disclaimers=self._disclaimers(),
                       warnings=list(dict.fromkeys(w)), usage=usage, attempt_errors=list(self._attempt_errors),
                       debug=self._debug(debug))
        self._narrate(plan, whys)
        usage.latency_s = round(time.perf_counter() - t0, 3)
        return plan

    def _narrate(self, plan: DayPlan, whys: dict):
        if plan.source != "llm":
            return
        payload = nar.payload(plan.timeline, whys, plan.home_by)
        try:
            cr = self.llm.narrate(payload)
        except Exception:
            plan.warnings.append("The narrative was skipped.")
            return
        _add_usage(plan.usage, cr.usage)
        if cr.error or cr.parsed is None:
            plan.warnings.append("The narrative could not be produced; the timeline stands on its own.")
            return
        probs = nar.check(cr.parsed, plan.timeline, self.lib, plan.request.origin.name, plan.home_by)
        # one retry on the local backend only (Changes.md C); the Claude backends keep their single call
        retries = int(self.cfg.get("local", {}).get("narrative_retries", 0)) if self.backend == "local" else 0
        if probs and retries > 0 and cr.assistant is not None:
            try:
                cr = self.llm.narrate_retry(payload, cr, nar.retry_message(cr.parsed, probs, plan.timeline))
            except Exception:
                plan.warnings.append("The narrative was skipped.")
                return
            _add_usage(plan.usage, cr.usage)
            if cr.error or cr.parsed is None:
                plan.warnings.append("The narrative could not be produced; the timeline stands on its own.")
                return
            probs = nar.check(cr.parsed, plan.timeline, self.lib, plan.request.origin.name, plan.home_by)
        if probs:
            plan.warnings.append("The narrative was dropped because it failed the check: " + "; ".join(probs[:3]) + ".")
            return
        plan.narrative = cr.parsed.model_dump(mode="json")


def _add_usage(usage: Usage, u: dict):
    usage.calls.append(u)
    usage.input_tokens += u.get("input_tokens", 0)
    usage.output_tokens += u.get("output_tokens", 0)
    usage.cache_read_tokens += u.get("cache_read_tokens", 0)
    usage.cache_write_tokens += u.get("cache_write_tokens", 0)


def _fallback_why(item) -> str:
    return "; ".join(item.why[:2])


def _fallback_title(req) -> str:
    return f"A day from {req.origin.name}"


def load(engine=None, places=None, config="planner/config.toml", client=None, backend="api", runner=None) -> Planner:
    import places as places_pkg
    import reach
    cfg_path = Path(config)
    if not cfg_path.is_absolute() and not cfg_path.exists():
        cfg_path = ROOT / config
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    engine = engine or reach.load(str(ROOT / "engine/config"))
    places = places or places_pkg.load(str(ROOT / "places/data"), engine=engine, config_dir=str(ROOT / "places/config"))
    acc = cfg.get("paths", {}).get("accepted_legs")
    acc = str(ROOT / acc) if acc else None
    return Planner(engine, places, cfg, client=client, accepted_legs=acc, backend=backend, runner=runner)
