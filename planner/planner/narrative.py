"""The narrative check (Planner.md 8): the story may only repeat what the checked timeline says."""
from __future__ import annotations

import json
import re

from places.text import fold

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d")
TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
BAD = [
    (re.compile(r"[€$£]|\beuros?\b|\beur\b", re.I), "a price"),
    (re.compile(r"https?://|www\.|\.(com|pt|org|net)\b", re.I), "a web address"),
    (re.compile(r"\+\d{2,3}[\s\d]{6,}|\b\d{3}[\s-]?\d{3}[\s-]?\d{3}\b|\d{7,}"), "a phone number"),
]


def timeline_times(entries) -> set[tuple[int, int]]:
    out = set()
    for e in entries:
        d = e.model_dump(mode="json", by_alias=True) if hasattr(e, "model_dump") else e
        for k in ("depart", "arrive", "board_time", "alight_time", "from", "until", "start", "end"):
            v = d.get(k)
            if isinstance(v, str) and ISO.match(v):
                out.add((int(v[11:13]), int(v[14:16])))
    return out


def payload(plan_entries, whys: dict, home_by: str | None) -> str:
    """What the narrative call sees: the validated timeline in plain words, and each stop's reason."""
    rows = []
    for e in plan_entries:
        d = e.model_dump(mode="json", by_alias=True)
        t = d["type"]
        h = lambda s: s[11:16]
        if t == "visit":
            rows.append({"visit": d["name"], "place_id": d["place_id"], "from": h(d["start"]), "to": h(d["end"]),
                         "meal": d["meal"], "why": whys.get(d["place_id"]) or d.get("reason", "")})
        elif t == "ride":
            rows.append({"ride": f"{d['mode']} {d['route_short']}", "from": d["board_stop"], "at": h(d["board_time"]),
                         "to": d["alight_stop"], "arrives": h(d["alight_time"])})
        elif t == "walk":
            rows.append({"walk_m": d["metres"], "to": d["to"]})
    return json.dumps({"timeline": rows, "home_by": home_by[11:16] if home_by else None}, ensure_ascii=False) + \
        "\n\nWrite the summary and one line per visit."


def check(n, entries, lib, origin_name: str, home_by: str | None = None) -> list[str]:
    problems = []
    visits = [e for e in entries if getattr(e, "type", "") == "visit"]
    allowed = timeline_times(entries)
    if home_by:
        allowed.add((int(home_by[11:13]), int(home_by[14:16])))
    texts = [n.summary] + [s.line for s in n.stop_lines]
    blob = " ".join(texts)
    if len(n.summary.split()) > 80:
        problems.append("summary is longer than 80 words")
    for s in n.stop_lines:
        if len(s.line.split()) > 25:
            problems.append(f"line for {s.place_id} is longer than 25 words")
    want = [v.place_id for v in visits]
    got = [s.place_id for s in n.stop_lines]
    if sorted(want) != sorted(got):
        problems.append("stop_lines do not match the stops in the plan")
    for h, m in TIME.findall(blob):
        if (int(h), int(m)) not in allowed:
            problems.append(f"the time {h}:{m} is not in the timeline")
    for rx, what in BAD:
        if rx.search(blob):
            problems.append(f"it mentions {what}")
    # other places: remove the plan's own names first, then look for any other place name
    folded = fold(blob)
    for v in visits:
        folded = folded.replace(fold(v.name), " ")
    folded = folded.replace(fold(origin_name), " ")
    stop_ids = set(want)
    for p in lib.places.values():
        if p.id in stop_ids or p.status == "rejected":
            continue
        nm = fold(p.name)
        if len(nm) >= 6 and nm in folded:
            problems.append(f"it mentions {p.name}, which is not a stop")
            break
    return problems


def retry_message(n, problems: list[str], entries) -> str:
    """The user turn of the narrative retry (local backend): what failed, with the numbers and ids needed to fix it."""
    out = []
    summary = len(n.summary.split())
    if summary > 80:
        out.append(f"- Your summary has {summary} words; the limit is 80.")
    for s in n.stop_lines:
        words = len(s.line.split())
        if words > 25:
            out.append(f"- The line for {s.place_id} has {words} words; the limit is 25.")
    if any(p.startswith("stop_lines do not match") for p in problems):
        want = [e.place_id for e in entries if getattr(e, "type", "") == "visit"]
        got = [s.place_id for s in n.stop_lines]
        out.append(f"- stop_lines must have exactly one entry for each of these place_ids, in this order: {', '.join(want)}. "
                   f"Yours had: {', '.join(got) or 'none'}.")
    out += [f"- {p}" for p in problems
            if not (p.endswith("words") or p.startswith("stop_lines do not match"))]
    return "That summary did not pass the check:\n" + "\n".join(out) + "\n\nReturn a corrected summary in the same format. Change only what is needed."
