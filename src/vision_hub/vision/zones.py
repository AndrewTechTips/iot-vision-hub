"""Zone rules on tracked people (vision/tracking.py): someone lingering in an area, someone
crossing a line.

Where a person stands is the bottom centre of their box, their feet: a head poking into an
area from behind does not count, a foot inside it does.

* **Area** — occupied while anyone stands in it. Lingering starts once it has been occupied for
  ``dwell_seconds`` (from when it became occupied) and ends once it has been empty for a moment.
  It counts presence, not a track's id: if tracking swaps ids as someone walks up to the camera,
  the clock keeps running.
* **Line** — crossed when a track's feet move from one side to the other between two frames. The
  same track crossing back and forth within a few seconds counts once.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from vision_hub.vision.config import Zone
from vision_hub.vision.tracking import TrackView

type Point = tuple[float, float]

EMPTY_SECONDS = 1.5  # an area empty this long is no longer occupied
RECROSS_SECONDS = 3.0  # a track crossing the same line again within this counts once


class ZoneRule(StrEnum):
    LINGERING = "lingering"
    CROSSED = "crossed"


@dataclass(frozen=True, slots=True)
class ZoneStarted:
    zone: str
    rule: ZoneRule
    at: datetime  # when it began: the area became occupied, or the line was crossed


@dataclass(frozen=True, slots=True)
class ZoneEnded:
    zone: str
    rule: ZoneRule
    at: datetime


type ZoneChange = ZoneStarted | ZoneEnded


@dataclass(slots=True)
class _Area:
    zone: Zone
    occupied_since: datetime | None = None
    last_occupied: datetime | None = None
    lingering: bool = False


@dataclass(slots=True)
class _Line:
    zone: Zone
    feet: dict[int, Point]  # track id -> where its feet were on the previous frame
    crossed: dict[int, datetime]  # track id -> when it last crossed


class ZoneWatcher:
    """One per camera. ``update`` with each frame's tracks; returns what started or ended."""

    def __init__(self, zones: Sequence[Zone], width: int, height: int) -> None:
        self._width, self._height = width, height
        self._areas = [_Area(zone) for zone in zones if zone.kind == "area"]
        self._lines = [_Line(zone, {}, {}) for zone in zones if zone.kind == "line"]

    @property
    def active(self) -> bool:
        return bool(self._areas or self._lines)

    def update(
        self, tracks: Sequence[TrackView], at: datetime, *, anyone: bool = False
    ) -> list[ZoneChange]:
        """``anyone``: count every track, not only people (when nobody can check for people)."""
        people = [t for t in tracks if anyone or t.person is not None]
        feet = {t.id: self._normalise(t.feet) for t in people}
        changes: list[ZoneChange] = []
        for area in self._areas:
            changes.extend(self._watch_area(area, feet.values(), at))
        for line in self._lines:
            changes.extend(self._watch_line(line, feet, at))
        return changes

    def close(self, at: datetime) -> list[ZoneChange]:
        """The camera stopped: lingering ends."""
        changes: list[ZoneChange] = []
        for area in self._areas:
            if area.lingering:
                changes.append(ZoneEnded(area.zone.name, ZoneRule.LINGERING, at))
            area.occupied_since = area.last_occupied = None
            area.lingering = False
        return changes

    def _watch_area(self, area: _Area, feet: Iterable[Point], at: datetime) -> list[ZoneChange]:
        polygon = area.zone.points
        inside = any(contains(polygon, point) for point in feet)
        if inside:
            area.occupied_since = area.occupied_since or at
            area.last_occupied = at
        elif area.last_occupied is not None and at - area.last_occupied > timedelta(
            seconds=EMPTY_SECONDS
        ):
            ended = area.last_occupied
            area.occupied_since = area.last_occupied = None
            if area.lingering:
                area.lingering = False
                return [ZoneEnded(area.zone.name, ZoneRule.LINGERING, ended)]
            return []
        since = area.occupied_since
        if (
            not area.lingering
            and since is not None
            and at - since >= timedelta(seconds=area.zone.dwell_seconds)
        ):
            area.lingering = True
            return [ZoneStarted(area.zone.name, ZoneRule.LINGERING, since)]
        return []

    def _watch_line(self, line: _Line, feet: dict[int, Point], at: datetime) -> list[ZoneChange]:
        a, b = line.zone.points
        changes: list[ZoneChange] = []
        for track, now in feet.items():
            before = line.feet.get(track)
            last = line.crossed.get(track)
            recent = last is not None and at - last < timedelta(seconds=RECROSS_SECONDS)
            if before is not None and not recent and crosses(before, now, a, b):
                line.crossed[track] = at
                changes.append(ZoneStarted(line.zone.name, ZoneRule.CROSSED, at))
                changes.append(ZoneEnded(line.zone.name, ZoneRule.CROSSED, at))
        line.feet = feet.copy()
        horizon = at - timedelta(seconds=RECROSS_SECONDS)
        line.crossed = {track: when for track, when in line.crossed.items() if when >= horizon}
        return changes

    def _normalise(self, point: tuple[float, float]) -> Point:
        return (point[0] / self._width, point[1] / self._height)


def contains(polygon: Sequence[Point], point: Point) -> bool:
    """Even-odd rule: whether ``point`` lies inside ``polygon``."""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, [*polygon[1:], polygon[0]], strict=True):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def crosses(p1: Point, p2: Point, a: Point, b: Point) -> bool:
    """Whether the segment p1-p2 crosses the segment a-b."""

    def side(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1, d2 = side(a, b, p1), side(a, b, p2)
    d3, d4 = side(p1, p2, a), side(p1, p2, b)
    return d1 * d2 < 0 and d3 * d4 < 0
