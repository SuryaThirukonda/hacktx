"""Grounding operator commands against named world entities: streets, intersections, buildings (and what is next to
them, such as a carport), search sectors, landmarks, and people by their clothing.

A phrase resolves to exactly one entity, or the resolver asks a question ("Which building? I see two with
carports."), or it says it found nothing. It never guesses: two plausible entities are always a question. People
are matched by clothing colour words only (the session histogram and colour names, as D3_describe does), never by
face or identity, and only among people the drone sees right now.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..vision.appearance import descriptor_likelihood, parse_description

NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "first": 1, "second": 2, "third": 3, "fourth": 4}
ORDINALS = ("first", "second", "third", "fourth")
BUILDING_KINDS = ("house", "building", "barn", "shed", "hangar", "garage", "store")
PERSON_MATCH = 0.5  # descriptor likelihood a visible person needs to count as matching the words
PERSON_MARGIN = 2.0  # the best match must be this many times likelier than the next, or the resolver asks


@dataclass
class Entity:
    id: str
    kind: str  # street | intersection | building | sector | landmark | person
    label: str
    xy: tuple[float, float]
    names: tuple[str, ...] = ()  # lower-case aliases matched against the words
    attrs: dict[str, Any] = field(default_factory=dict)
    polygon: list[tuple[float, float]] | None = None

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "xy": [round(self.xy[0], 1), round(self.xy[1], 1)]}


@dataclass
class Resolution:
    status: str  # ok | ask | none
    entity: Entity | None = None
    question: str | None = None
    options: list[Entity] = field(default_factory=list)
    why: str = ""

    def view(self) -> dict[str, Any]:
        return {"status": self.status, "entity": self.entity.view() if self.entity else None, "question": self.question,
                "options": [o.view() for o in self.options], "why": self.why}


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9& ]+", " ", text.lower()).split())


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _street_aliases(name: str) -> tuple[str, ...]:
    n = name.lower()
    base = n.replace(" street", "").replace(" road", "").replace(" avenue", "")
    short = n.replace(" street", " st").replace(" road", " rd").replace(" avenue", " ave")
    return tuple(dict.fromkeys([n, short, base]))


def world_entities(world: Any, streets: dict | None, zones: list[Any] | None = None) -> list[Entity]:
    """Named entities of a simulator world (map data, buildings, sectors, landmarks). A real incident would build
    the same list from its map extract and the anchors an operator registered."""
    out: list[Entity] = []
    if streets:
        names = streets.get("names") or {}
        street_xy: dict[str, list[tuple[float, float]]] = {}
        for key, nm in names.items():
            a, b = key.split("|")
            street = nm.split(" between ")[0]
            street_xy.setdefault(street, []).extend([tuple(streets["nodes"][a]), tuple(streets["nodes"][b])])
        for street, pts in street_xy.items():
            P = np.array(pts, float)
            out.append(Entity(f"street:{street}", "street", street, (float(P[:, 0].mean()), float(P[:, 1].mean())),
                              _street_aliases(street), {"points": [tuple(map(float, p)) for p in P]}))
        for node, label in (streets.get("node_labels") or {}).items():
            if " & " not in label:
                continue
            a, b = [x.strip().lower() for x in label.split(" & ")]
            al = []
            for x, y in ((a, b), (b, a)):
                xs, ys = x.split()[0], y.split()[0]
                al += [f"{x} and {y}", f"{x} & {y}", f"{xs} and {ys}", f"{xs} & {ys}", f"corner of {xs} and {ys}"]
            out.append(Entity(f"node:{node}", "intersection", label, tuple(map(float, streets["nodes"][node])),
                              tuple(dict.fromkeys(al))))
    boxes = list(getattr(world, "boxes", []) or [])
    carports = [b for b in boxes if b.kind == "carport"]
    street_names = [(e.label, e.attrs["points"]) for e in out if e.kind == "street"]
    for b in boxes:
        if b.kind not in BUILDING_KINDS:
            continue
        x, y = b.center_xy
        near_carport = any(math.dist(b.center_xy, c.center_xy) < 15.0 for c in carports)  # the carport by its drive
        on = min(street_names, key=lambda s: _seg_dist(s[1], (x, y)))[0] if street_names else None
        name = f"{b.kind} {b.oid[len(b.kind) + 1:]}" if b.oid.startswith(f"{b.kind}-") else f"{b.kind} {b.oid}"
        label = name + (" with a carport" if near_carport else "") + (f" on {on}" if on else "")  # "house 5 ..."
        out.append(Entity(f"box:{b.oid}", "building", label, (float(x), float(y)), (b.kind,),
                          {"carport": near_carport, "street": on, "box": b.oid}))
    for z in zones if zones is not None else (getattr(world, "zones", []) or []):
        zid = getattr(z, "zone_id", None)
        if zid is None:
            continue
        P = np.array(getattr(z, "polygon", None) or getattr(z, "polygon_xy_m", []), float)
        if not len(P):
            continue
        kind = getattr(z, "kind", "zone")
        names = [zid.lower(), z.name.lower()]
        m = re.match(r"s(\d+)$", zid.lower())
        if m:
            names += [f"sector {m.group(1)}", f"sector s{m.group(1)}"] + \
                     [f"sector {w}" for w, n in NUMBERS.items() if n == int(m.group(1)) and w not in ORDINALS]
        out.append(Entity(f"zone:{zid}", "sector" if kind in ("sector", "task") else "landmark", z.name,
                          (float(P[:, 0].mean()), float(P[:, 1].mean())), tuple(dict.fromkeys(names)),
                          {"zone_kind": kind}, [tuple(map(float, p)) for p in P]))
    pad = getattr(world, "launchpad", None)
    if pad:
        out.append(Entity("landmark:pad", "landmark", "the launch pad", (float(pad[0]), float(pad[1])),
                          ("pad", "launch pad", "launchpad", "base", "station", "the base")))
    walk = [b for b in boxes if b.kind == "walkway"]
    if walk:
        P = np.array([b.center_xy for b in walk], float)
        out.append(Entity("landmark:walkway", "landmark", "the covered walkway", (float(P[:, 0].mean()), float(P[:, 1].mean())),
                          ("walkway", "covered walkway", "arcade"), {"ends": [tuple(map(float, P[0])), tuple(map(float, P[-1]))]}))
    return out


def _seg_dist(points: list[tuple[float, float]], xy: tuple[float, float]) -> float:
    best = 1e9
    p = np.asarray(xy, float)
    for a, b in zip(points[0::2], points[1::2]):
        A, B = np.asarray(a, float), np.asarray(b, float)
        ab = B - A
        t = float(np.clip(np.dot(p - A, ab) / max(float(np.dot(ab, ab)), 1e-9), 0.0, 1.0))
        best = min(best, float(np.linalg.norm(p - (A + t * ab))))
    return best


def nearest_on_street(e: Entity, xy: tuple[float, float]) -> tuple[float, float]:
    pts = e.attrs.get("points") or [e.xy, e.xy]
    best, bd = e.xy, 1e9
    p = np.asarray(xy, float)
    for a, b in zip(pts[0::2], pts[1::2]):
        A, B = np.asarray(a, float), np.asarray(b, float)
        ab = B - A
        t = float(np.clip(np.dot(p - A, ab) / max(float(np.dot(ab, ab)), 1e-9), 0.0, 1.0))
        q = A + t * ab
        d = float(np.linalg.norm(p - q))
        if d < bd:
            best, bd = (float(q[0]), float(q[1])), d
    return best


class Grounding:
    """Resolve the place or person in a command. ``in_view(xy)``: is a ground point in the camera's current view."""

    def __init__(self, entities: list[Entity], drone_xy: tuple[float, float] | None,
                 in_view: Callable[[tuple[float, float]], bool] | None = None) -> None:
        self.entities = entities
        self.drone_xy = drone_xy
        self.in_view = in_view

    def _near_drone(self, ents: list[Entity], radius_m: float = 80.0) -> list[Entity]:
        if self.drone_xy is None:
            return ents
        return sorted([e for e in ents if math.dist(e.xy, self.drone_xy) <= radius_m],
                      key=lambda e: math.dist(e.xy, self.drone_xy))

    def place(self, text: str) -> Resolution:
        t = _words(text)
        # 1. a sector by number or name
        m = re.search(r"sector\s+([a-z0-9]+)", t)
        sectors = [e for e in self.entities if e.kind == "sector"]
        if m:
            hit = [e for e in sectors if any(_has(t, n) for n in e.names)]
            if len(hit) == 1:
                return Resolution("ok", hit[0], why=f"sector named in the command: {hit[0].label}")
            if not sectors:
                return Resolution("none", why="this map has no search sectors")
            return Resolution("ask", question=f"Which sector? This map has {', '.join(e.id.split(':')[1] for e in sectors[:9])}.",
                              options=sectors[:9], why="no sector of that name")
        # 2. an intersection ("Oak and North")
        hit = [e for e in self.entities if e.kind == "intersection" and any(_has(t, n) for n in e.names)]
        if len(hit) == 1:
            return Resolution("ok", hit[0], why=f"intersection named: {hit[0].label}")
        # 3. a building by what is next to it or the street it is on
        if any(_has(t, k) for k in BUILDING_KINDS) or _has(t, "carport"):
            cands = [e for e in self.entities if e.kind == "building"]
            kinds = [k for k in BUILDING_KINDS if _has(t, k)]
            if kinds and kinds != ["house"]:
                cands = [e for e in cands if any(e.label.startswith(k) for k in kinds)]
            if _has(t, "carport"):
                cands = [e for e in cands if e.attrs.get("carport")]
            for e in [s for s in self.entities if s.kind == "street"]:
                if any(_has(t, n) for n in e.names[:2]):
                    cands = [c for c in cands if c.attrs.get("street") == e.label]
            what = "with carports" if _has(t, "carport") else (f"{kinds[0]}s" if kinds else "buildings")
            seen = [c for c in cands if self.in_view is not None and self.in_view(c.xy)]
            pool = seen or self._near_drone(cands)
            where = "I see" if seen else "near the drone there are"
            if len(pool) == 1:
                return Resolution("ok", pool[0], why=f"the only building {what} {'in view' if seen else 'nearby'}")
            if len(pool) > 1:
                n = len(pool)
                word = {2: "two", 3: "three", 4: "four"}.get(n, str(n))
                return Resolution("ask", question=f"Which building? {where[0].upper() + where[1:]} {word} {what}.",
                                  options=pool[:4], why=f"{n} buildings {what} {'in view' if seen else 'nearby'}")
            return Resolution("none", why=f"no building {what} in view or within 80 m")
        # 4. a landmark
        hit = [e for e in self.entities if e.kind == "landmark" and any(_has(t, n) for n in e.names)]
        if len(hit) == 1:
            return Resolution("ok", hit[0], why=f"landmark named: {hit[0].label}")
        # 5. a street (the nearest point on it)
        hit = [e for e in self.entities if e.kind == "street" and any(_has(t, n) for n in e.names)]
        if len(hit) == 1:
            e = hit[0]
            xy = nearest_on_street(e, self.drone_xy) if self.drone_xy else e.xy
            return Resolution("ok", Entity(e.id, "street", e.label, xy, e.names, e.attrs), why=f"street named: {e.label}")
        if len(hit) > 1:
            return Resolution("ask", question=f"Which street: {' or '.join(e.label for e in hit[:3])}?", options=hit[:3],
                              why="several streets match")
        # 6. a point in local metres ("go to 30 40")
        nums = re.findall(r"-?\d+(?:\.\d+)?", t)
        if len(nums) >= 2 and not m:
            x, y = float(nums[0]), float(nums[1])
            return Resolution("ok", Entity(f"point:{x:.0f},{y:.0f}", "point", f"the point ({x:.0f}, {y:.0f}) m", (x, y)),
                              why="local metres given in the command")
        return Resolution("none", why="no named place in the command")

    def person(self, text: str, people: list[dict[str, Any]]) -> Resolution:
        """``people``: visible person tracks {track_id, xy, hist, colors}. Clothing words only."""
        desc = parse_description(text)
        if desc is None:
            return Resolution("ask", question="Who? Describe their clothing, for example 'the one in the red shirt'.",
                              why="no clothing words in the command")
        scored = sorted(((descriptor_likelihood(desc, p.get("hist"), p.get("colors")), p) for p in people),
                        key=lambda sp: -sp[0])
        match = [(s, p) for s, p in scored if s >= PERSON_MATCH]
        def ent(p: dict[str, Any], s: float) -> Entity:
            return Entity(f"track:{p['track_id']}", "person", f"person {p['label']} (matches '{desc.text()}')",
                          tuple(p["xy"]) if p.get("xy") else (0.0, 0.0), attrs={"track_id": p["track_id"],
                                                                                "match": round(s, 3)})
        if not match:
            return Resolution("none", why=f"nobody in view matches '{desc.text()}'")
        if len(match) == 1 or match[0][0] >= PERSON_MARGIN * match[1][0]:
            s, p = match[0]
            return Resolution("ok", ent(p, s), why=f"the only person in view matching '{desc.text()}' (likelihood {s:.2f})")
        n = len(match)
        word = {2: "two", 3: "three"}.get(n, str(n))
        return Resolution("ask", question=f"Which one? I see {word} people matching '{desc.text()}'.",
                          options=[ent(p, s) for s, p in match[:4]], why=f"{n} people match the words")


def pick_option(text: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    """An answer to a question: "the first one", "two", "number 2", or words of an option's label."""
    t = _words(text)
    for w, n in NUMBERS.items():
        if _has(t, w) and 1 <= n <= len(options):
            return options[n - 1]
    m = re.search(r"\b(\d)\b", t)
    if m and 1 <= int(m.group(1)) <= len(options):
        return options[int(m.group(1)) - 1]
    words = set(t.split()) - {"the", "one", "on", "a", "with", "in", "that", "this", "please", "go", "to"}
    hits = [o for o in options if words and words & set(_words(o["label"]).split())]
    return hits[0] if len(hits) == 1 else None
