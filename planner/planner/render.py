"""Human-readable output of a DayPlan."""
from __future__ import annotations

from .models import DayPlan


def _t(s):
    return s[11:16]


def render(p: DayPlan) -> str:
    r = p.request
    out = [f"{p.title or 'Day plan'}  [{p.source}{', ' + p.model if p.model else ''}, attempts {p.attempts}]",
           f"{r.origin.name} · {r.date} · {r.leave_after}-{r.back_by}{' (leave by)' if r.deadline == 'leave' else ''} · {r.pace}", ""]
    if p.debug and p.debug.get("draft"):
        d = p.debug["draft"]
        out += [f"Model notes (testing): {d['title']}", f"  {d['notes'] or 'no notes'}", ""]
    if p.status != "ok":
        out.append("No valid plan could be built:")
        out += [f"  - {e['code']}: {e['message']}" for e in p.errors]
    for e in p.timeline:
        if e.type == "visit":
            tag = " (fixed entry)" if e.entry == "fixed" else ""
            tag += " (inserted: fills time before dinner)" if e.inserted else ""
            meal = f" [{e.meal}]" if e.meal else ""
            out.append(f"{_t(e.start)}-{_t(e.end)}  VISIT {e.name}{meal}{tag}")
        elif e.type == "ride":
            out.append(f"{_t(e.board_time)}-{_t(e.alight_time)}  {e.mode} {e.route_short} {e.headsign}: {e.board_stop} -> {e.alight_stop}")
        elif e.type == "walk":
            out.append(f"{_t(e.depart)}-{_t(e.arrive)}  walk {e.metres} m to {e.to}")
        elif e.type == "free_time":
            out.append(f"{_t(e.from_)}-{_t(e.until)}  free time at {e.at} ({e.reason})")
            for x in e.suggestions:
                until = f", open until {x['open_until']}" if x["open_until"] else ""
                out.append(f"             idea: {x['name']}{' (' + x['point'] + ')' if x.get('point') else ''}, {x['walk_m']} m ({x['walk_min']} min) on foot, about {x['minutes_there']} min there{until}")
        else:
            out.append(f"{_t(e.from_)}-{_t(e.until)}  wait at {e.at} ({e.reason})")
    if p.home_by:
        out.append(f"\nHome by {_t(p.home_by)}")
    s = p.last_safe_return
    if s:
        v = s.first_vehicle
        veh = f"; {v['mode']} {v['route_short']} at {_t(v['board_time'])} from {v['board_stop']}" if v else ""
        out.append(f"Last safe return: leave {s.from_place} by {_t(s.depart)}{veh}; home {_t(s.arrive_home)}")
        if s.backup:
            out.append(f"  one departure earlier: {_t(s.backup['depart'])}, home {_t(s.backup['arrive_home'])}")
    for b in p.plan_b:
        m = b.missed
        nxt = f"next gets you to {b.target} at {_t(b.arrive)}" if b.arrive else b.note
        out.append(f"Plan B: if you miss {m['route_short']} {m['headsign']} at {_t(m['board_time'])} ({b.change_at}), {nxt}")
    if p.narrative:
        out += ["", p.narrative["summary"]]
        out += [f"  - {s['line']}" for s in p.narrative["stop_lines"]]
    if p.warnings:
        out += ["", "Warnings:"] + [f"  - {w}" for w in p.warnings]
    out += [""] + p.disclaimers
    u = p.usage
    if u.calls:
        out.append(f"\nTokens: in {u.input_tokens}, out {u.output_tokens}, cache read {u.cache_read_tokens}; {u.latency_s:.1f}s")
    return "\n".join(out)
