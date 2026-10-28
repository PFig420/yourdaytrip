"""Bus routes as mapped in OpenStreetMap (MapPlan.md 11): assemble route relations into lines and choose, per stop pair, between the
operator's mapped route and the shortest road path. Display only.

A relation is joined from its member ways into unbroken pieces (a gap splits it). A stop pair uses a relation only when both stops
lie within 40 m of the same piece, the second after the first in the direction of travel, and the piece between them is at most
twice the shortest road path. Otherwise the shortest path stays.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .graph import haversine

STOP_M = 40               # both stops must be this close to the same piece
LENGTH_RATIO = 2.0        # the piece between the stops may be this much longer than the shortest road path
DIFFER_M, DIFFER_SHARE = 30, 0.30
REASONS = ["far stop", "gap", "wrong direction", "too long"]       # in the order a pair gets closer to being accepted

# feed line -> how OSM tags its relations (MapPlan.md 11.2). Match on operator or network AND ref (or the name), never on ref alone.
AVEIROBUS = {"operator": {"AveiroBus"}, "network": {"AveiroBus"}}
BUSWAY = {"operator": {"BusWay", "Busway"}, "network": {"BusWay", "Busway"}}
LINES = {
    **{f"av:ab-{n}": {"who": AVEIROBUS, "ref": f"L{n}"} for n in (1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12)},
    "av:ab-15-azul": {"who": AVEIROBUS, "name": "Linha Centro: Azul"},
    "av:ab-15-verde": {"who": AVEIROBUS, "name": "Linha Centro: Verde"},
    "av:bw-16": {"who": BUSWAY, "ref": "16"}, "av:bw-17": {"who": BUSWAY, "ref": "17"},
    "av:bw-32": {"who": BUSWAY, "ref": "32"}, "av:bw-37": {"who": BUSWAY, "ref": "37"},
    # av:bw-36 (Costa Nova) is left out on purpose (Pedro, 2026-10-28): OSM only has the former operator's relation
    # (Transdev "5951 Aveiro => Costa Nova"), and one of its pairs ran 8.2 km past no stop where the road is 5.0 km.
    # Add a Busway 36 entry here once Busway's own line 36 is mapped in OSM.
}


def match(tags: dict, rule: dict) -> bool:
    who = rule["who"]
    if tags.get("route") != "bus":
        return False
    if not (tags.get("operator") in who["operator"] or tags.get("network") in who["network"]):
        return False
    if "ref" in rule:
        return tags.get("ref") == rule["ref"]
    return tags.get("name") == rule["name"]


def match_relations(relations: list[dict]) -> dict[str, list[dict]]:
    """feed route id -> its OSM relations."""
    return {line: [r for r in relations if match(r.get("tags", {}), rule)] for line, rule in LINES.items()}


# ---- assembling a relation into pieces ---------------------------------------------------------------------------------
@dataclass
class Piece:
    coords: list                                 # [(lat, lon), ...]
    nodes: list = field(default_factory=list)
    cum: list = field(default_factory=list)      # metres from the start to each vertex

    def __post_init__(self):
        self.cum = [0.0]
        for a, b in zip(self.coords, self.coords[1:]):
            self.cum.append(self.cum[-1] + haversine(a[0], a[1], b[0], b[1]))

    @property
    def length(self):
        return self.cum[-1]


def _way(ways, ref):
    w = ways.get(ref)
    if not w or len(w["nodes"]) < 2 or len(w["nodes"]) != len(w["geometry"]):
        return None
    return list(w["nodes"]), [tuple(g) for g in w["geometry"]], w.get("tags", {})


def assemble(members: list[dict], ways: dict) -> list[Piece]:
    """Join the member ways, in member order, into unbroken pieces. Roles '', 'forward' and 'backward' are used; platforms and stops are
    skipped. A way is flipped when its end, not its start, meets the piece so far. Two consecutive ways that share no node split the
    relation. A closed way (a roundabout) is cut between the entry and the exit, in the direction of travel."""
    seq = []
    for m in members:
        if m.get("type") == "way" and m.get("role", "") in ("", "forward", "backward"):
            w = _way(ways, m["ref"])
            if w:
                seq.append(w)
    pieces, cur = [], None            # cur: [nodes, coords]

    def flush():
        nonlocal cur
        if cur and len(cur[0]) >= 2:
            pieces.append(Piece(cur[1], cur[0]))
        cur = None

    for i, (nodes, coords, tags) in enumerate(seq):
        closed = nodes[0] == nodes[-1] and len(nodes) > 3
        nxt = seq[i + 1] if i + 1 < len(seq) else None
        if cur is None:
            if closed:
                continue                                           # a roundabout cannot start a piece
            if nxt is not None:                                     # the first way: orient it towards the next one
                ends = {nxt[0][0], nxt[0][-1]}
                if nodes[0] in ends and nodes[-1] not in ends:
                    nodes, coords = nodes[::-1], coords[::-1]
            cur = [list(nodes), list(coords)]
            continue
        tail = cur[0][-1]
        if closed:
            if tail not in nodes:
                flush()
                continue
            exits = [n for n in ((nxt[0][0], nxt[0][-1]) if nxt else ()) if n in nodes and n != tail]
            if not exits:
                continue                                           # nothing to leave by: stay on the way before
            k, x = nodes.index(tail), nodes.index(exits[0])
            ring, rc = nodes[:-1], coords[:-1]
            n = len(ring)
            idx = [(k + j) % n for j in range(1, (x - k) % n + 1)]       # the way's own direction is the direction of travel
            cur[0] += [ring[j] for j in idx]
            cur[1] += [rc[j] for j in idx]
            continue
        if nodes[0] == tail:
            pass
        elif nodes[-1] == tail:
            nodes, coords = nodes[::-1], coords[::-1]
        else:                                                      # a gap: start a new piece
            flush()
            if nxt is not None and not (nxt[0][0] == nxt[0][-1]):
                ends = {nxt[0][0], nxt[0][-1]}
                if nodes[0] in ends and nodes[-1] not in ends:
                    nodes, coords = nodes[::-1], coords[::-1]
            cur = [list(nodes), list(coords)]
            continue
        cur[0] += nodes[1:]
        cur[1] += coords[1:]
    flush()
    return pieces


# ---- projecting a stop onto a piece ------------------------------------------------------------------------------------------
def project(piece: Piece, lat: float, lon: float, limit: float = STOP_M):
    """-> (s metres along the piece, distance, (lat, lon) of the projection) for the nearest point within limit, else None."""
    coslat = math.cos(math.radians(lat))
    best = None
    for i in range(len(piece.coords) - 1):
        (la1, lo1), (la2, lo2) = piece.coords[i], piece.coords[i + 1]
        x1, y1 = (lo1 - lon) * coslat * 111_320, (la1 - lat) * 110_540
        x2, y2 = (lo2 - lon) * coslat * 111_320, (la2 - lat) * 110_540
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / L2))
        d = math.hypot(x1 + t * dx, y1 + t * dy)
        if d <= limit and (best is None or d < best[1]):
            seg = piece.cum[i + 1] - piece.cum[i]
            best = (piece.cum[i] + t * seg, d, (la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t), i, t)
    return best


def project_all(piece: Piece, lat: float, lon: float, limit: float = STOP_M) -> list:
    """Every place a piece passes within `limit` of the point, one projection per pass (a line that loops, or goes out and comes
    back along the same street, passes a stop twice). Sorted by distance along the piece."""
    coslat = math.cos(math.radians(lat))
    runs, cur = [], None
    for i in range(len(piece.coords) - 1):
        (la1, lo1), (la2, lo2) = piece.coords[i], piece.coords[i + 1]
        x1, y1 = (lo1 - lon) * coslat * 111_320, (la1 - lat) * 110_540
        x2, y2 = (lo2 - lon) * coslat * 111_320, (la2 - lat) * 110_540
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / L2))
        d = math.hypot(x1 + t * dx, y1 + t * dy)
        if d <= limit:
            seg = piece.cum[i + 1] - piece.cum[i]
            hit = (piece.cum[i] + t * seg, d, (la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t), i, t)
            if cur is not None and cur[-1][3] == i - 1:
                cur.append(hit)
            else:
                cur = [hit]
                runs.append(cur)
    return [min(r, key=lambda h: h[1]) for r in runs]


def subpath(piece: Piece, pa, pb) -> list[tuple[float, float]]:
    """The piece between two projections (pa before pb), as [(lat, lon)], starting and ending on the projections."""
    out = [pa[2]]
    for i in range(pa[3] + 1, pb[3] + 1):
        out.append(piece.coords[i])
    out.append(pb[2])
    return out


@dataclass
class Choice:
    relation: int | None = None
    coords: list | None = None
    length: float = 0.0
    reason: str = ""            # when no relation was used: the furthest reason among the relations tried


def choose(pieces_by_rel: dict[int, list[Piece]], a, b, shortest_m) -> Choice:
    """The relation path for the stop pair a -> b ((lat, lon) each), or the reason none could be used. Of the relations that pass,
    the one with the shortest piece between the stops wins. `shortest_m`: the shortest road path's length (None: no such path, the
    length test is skipped)."""
    best, worst = None, -1
    for rid, pieces in pieces_by_rel.items():
        pa = [(p, project_all(p, a[0], a[1])) for p in pieces]
        pb = [(p, project_all(p, b[0], b[1])) for p in pieces]
        if not any(x for _, x in pa) or not any(x for _, x in pb):
            worst = max(worst, 0)
            continue
        both = [(p, x, y) for (p, x), (_, y) in zip(pa, pb) if x and y]
        if not both:
            worst = max(worst, 1)
            continue
        ordered = [(p, x, y) for p, xs, ys in both for x in xs for y in ys if y[0] > x[0]]     # a pass of each, b after a
        if not ordered:
            worst = max(worst, 2)
            continue
        ok = [(y[0] - x[0], p, x, y) for p, x, y in ordered if shortest_m is None or (y[0] - x[0]) <= LENGTH_RATIO * shortest_m]
        if not ok:
            worst = max(worst, 3)
            continue
        ln, p, x, y = min(ok, key=lambda t: t[0])
        if best is None or ln < best.length:
            best = Choice(rid, [tuple(a)] + subpath(p, x, y) + [tuple(b)], ln)
    if best is not None:
        return best
    return Choice(reason=REASONS[worst] if worst >= 0 else "far stop")


def differs(rel_coords, short_coords) -> bool:
    """True when the relation's path and the shortest path disagree for more than 30 % of their length: the share of either one's
    length that lies more than 30 m from the other."""
    def share(a, b):
        pts = _dense(a)
        if not pts:
            return 0.0
        far = sum(1 for p in pts if min(_dist_to(p, b[i], b[i + 1]) for i in range(len(b) - 1)) > DIFFER_M)
        return far / len(pts)
    return max(share(rel_coords, short_coords), share(short_coords, rel_coords)) > DIFFER_SHARE


def _dense(coords, step=20.0):
    pts = []
    for a, b in zip(coords, coords[1:]):
        n = max(1, int(haversine(a[0], a[1], b[0], b[1]) // step))
        pts += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]
    return pts


def _dist_to(p, a, b):
    coslat = math.cos(math.radians(p[0]))
    x1, y1 = (a[1] - p[1]) * coslat * 111_320, (a[0] - p[0]) * 110_540
    x2, y2 = (b[1] - p[1]) * coslat * 111_320, (b[0] - p[0]) * 110_540
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / L2))
    return math.hypot(x1 + t * dx, y1 + t * dy)


def overlay(ride: dict, pairs_by_line: dict, coordsof: dict, relations: list, ways: dict, short_names: dict | None = None) -> dict:
    """Choose, per bus stop pair, between a relation and the shortest path already in `ride` ({key: polyline}).

    pairs_by_line: feed route id -> {(stop_a, stop_b)} from its trips. Returns {"ride", "source", "flags", "stats", "relations"}:
    the ride dict with relation paths swapped in, `source[key]` = "relation:<id>" or "shortest" for every bus key, new flags per key,
    per-line counts, and the matched relations' ids, lines, names and OSM timestamps."""
    from .graph import decode, encode, path_length
    short_names = short_names or {}
    matched = match_relations(relations)
    pieces = {r["id"]: assemble(r.get("members", []), ways) for rels in matched.values() for r in rels}
    out = dict(ride)
    source = {k: "shortest" for k in ride if k.endswith("|bus")}
    flags: dict[str, list] = {}
    stats = {line: {"pairs": 0, "relation": 0, "far stop": 0, "gap": 0, "wrong direction": 0, "too long": 0, "differs": 0}
             for line, rels in matched.items() if rels}
    by_key: dict[tuple, list] = {}
    for line, prs in pairs_by_line.items():
        if matched.get(line):
            for ab in prs:
                by_key.setdefault(ab, []).append(line)
    for (a, b), lines in sorted(by_key.items()):
        key = f"{a}|{b}|bus"
        short = [tuple(c) for c in decode(ride[key])] if key in ride else None
        shortest_m = path_length(short) if short else None
        per_line = {}
        for line in lines:
            per_line[line] = choose({r["id"]: pieces[r["id"]] for r in matched[line]}, coordsof[a], coordsof[b], shortest_m)
            stats[line]["pairs"] += 1
            if per_line[line].relation is not None:
                stats[line]["relation"] += 1
            else:
                stats[line][per_line[line].reason] += 1
        ch = choose({r["id"]: pieces[r["id"]] for line in lines for r in matched[line]}, coordsof[a], coordsof[b], shortest_m)
        if ch.relation is not None:
            out[key] = encode(ch.coords)
            source[key] = f"relation:{ch.relation}"
            if short and differs(ch.coords, short):
                flags.setdefault(key, []).append(f"relation-differs from the shortest path (relation {ch.relation})")
                for line in lines:
                    if per_line[line].relation is not None:
                        stats[line]["differs"] += 1
        else:
            for line in lines:
                flags.setdefault(key, []).append(f"relation-rejected {per_line[line].reason} (line {short_names.get(line, line)})")
    rels_meta = {}
    for line, rels in matched.items():
        for r in rels:
            m = rels_meta.setdefault(str(r["id"]), {"lines": [], "name": r.get("tags", {}).get("name", ""),
                                                     "timestamp": r.get("timestamp", ""), "pieces": len(pieces[r["id"]])})
            m["lines"].append(short_names.get(line, line))
    return {"ride": out, "source": source, "flags": flags, "stats": stats, "relations": rels_meta}
