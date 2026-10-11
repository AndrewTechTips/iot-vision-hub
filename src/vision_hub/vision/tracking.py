"""Object tracking: the same someone from one frame to the next.

Dense where it is cheap, sparse where it is not. Motion boxes come with every analysed frame;
nearby ones are merged into one object (a walking person is legs, arms and a head that move
apart), and objects are matched across frames by how much they overlap. The person model is far
too slow for every frame (about 280 ms a check on a typical CPU, AD-22), so its rare answers
label tracks instead: the track whose motion lies inside a person's box becomes that person,
and keeps the person's full body as its box, moved along with its motion.

Someone standing still makes no motion. A person track that stops moving is held where it is
and asks for a person check now and then (``needs_check``): seen again, it stays; gone, it ends.
That is what lets a rule notice someone lingering at a door.
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.persons import Person

ABSORB_SHARE = 0.6  # a track this much inside another is part of it


@dataclass(frozen=True, slots=True)
class TrackerSettings:
    merge_margin: float = 0.03  # motion boxes this close (share of the width) are one object
    min_overlap: float = 0.3  # share of a motion box that must lie in a track to belong to it
    max_gap_seconds: float = 1.0  # an object without motion for this long is gone
    still_seconds: float = 1.0  # a person without motion for this long is standing still
    hold_seconds: float = 4.0  # a still person is kept this long without being seen again
    confirm_seconds: float = 2.0  # how often to look again at a still person
    min_hits: int = 2  # frames an object needs before it is shown
    max_looks: int = 3  # person checks an object gets; still nobody: it is just motion
    history_seconds: float = 3.0  # how far back a late person check can be matched


@dataclass(frozen=True, slots=True)
class TrackView:
    """A track as the rest of the hub sees it, on the frame just analysed."""

    id: int
    box: BoundingBox  # the person's body once known, else the moving object
    person: float | None  # the person score, once a check matched this track
    still: bool  # a person standing still (held, no motion now)
    since: datetime

    @property
    def feet(self) -> tuple[float, float]:
        """Where it stands: the bottom centre of its box."""
        return (self.box.x + self.box.width / 2, float(self.box.y + self.box.height))


@dataclass(slots=True)
class _Track:
    id: int
    blob: BoundingBox  # its motion, merged
    since: datetime
    last_motion: datetime
    hits: int = 1
    person: float | None = None
    body: BoundingBox | None = None  # the person's box when last seen...
    body_anchor: tuple[float, float] | None = None  # ...and the motion's centre at that time
    confirmed: datetime | None = None
    seen_moving: datetime | None = None  # the last frame it had motion on
    looks: int = 0  # person checks that ran while it was shown and found nobody in it
    history: deque[tuple[datetime, BoundingBox]] = field(default_factory=deque)

    def box(self) -> BoundingBox:
        """The body when last seen, moved with the motion and grown to cover it (someone
        walking up to the camera gets bigger between checks)."""
        if self.body is None or self.body_anchor is None:
            return self.blob
        cx, cy = _centre(self.blob)
        moved = _shift(self.body, cx - self.body_anchor[0], cy - self.body_anchor[1])
        return union([moved, self.blob]) if self.last_motion == self.seen_moving else moved

    def blob_at(self, at: datetime) -> BoundingBox:
        """Its motion when the frame of ``at`` was captured (the closest one kept)."""
        if not self.history:
            return self.blob
        return min(self.history, key=lambda item: abs((item[0] - at).total_seconds()))[1]


class Tracker:
    """One per camera, fed by its worker thread."""

    def __init__(self, width: int, settings: TrackerSettings | None = None) -> None:
        self._settings = settings or TrackerSettings()
        self._margin = round(width * self._settings.merge_margin)
        self._tracks: list[_Track] = []
        self._next_id = 1

    def update(self, boxes: Sequence[BoundingBox], at: datetime) -> list[TrackView]:
        """A frame's motion boxes; returns the tracks shown on it."""
        blobs = merge(boxes, self._margin)
        claimed: dict[int, list[BoundingBox]] = {}
        fresh: list[BoundingBox] = []
        for blob in blobs:
            best = max(self._tracks, key=lambda t: _inside(blob, self._region(t)), default=None)
            if best is not None and _inside(blob, self._region(best)) >= self._settings.min_overlap:
                claimed.setdefault(best.id, []).append(blob)
            else:
                fresh.append(blob)
        kept: list[_Track] = []
        for track in self._tracks:
            if track.id in claimed:
                track.blob = union(claimed[track.id])
                track.last_motion = track.seen_moving = at
                track.hits += 1
                self._remember(track, at)
                kept.append(track)
            elif self._alive(track, at):
                kept.append(track)
        for blob in fresh:
            track = _Track(id=self._next_id, blob=blob, since=at, last_motion=at)
            self._next_id += 1
            self._remember(track, at)
            kept.append(track)
        self._tracks = self._absorb(kept)
        return self.views(at)

    def views(self, at: datetime) -> list[TrackView]:
        return [
            TrackView(
                id=t.id,
                box=t.box(),
                person=t.person,
                still=t.person is not None and self._still(t, at),
                since=t.since,
            )
            for t in self._tracks
            if t.hits >= self._settings.min_hits or t.person is not None
        ]

    def people_seen(self, people: Sequence[Person], at: datetime) -> None:
        """What a person check found on the frame captured at ``at`` (possibly a few frames
        ago). Labels the tracks the people stand in; ends still people who are gone."""
        matched: set[int] = set()
        for person in people:
            # Every unclaimed track inside this person is part of them: the oldest keeps its id.
            inside = [
                track
                for track in self._tracks
                if track.id not in matched
                and (
                    _inside(track.blob_at(at), person.box) >= self._settings.min_overlap
                    # or the person lies within the track (up close, motion covers them)
                    or _inside(person.box, track.box()) >= ABSORB_SHARE
                )
            ]
            if inside:
                track = min(inside, key=lambda candidate: candidate.since)
                for other in inside:
                    if other is not track:
                        track.blob = union([track.blob, other.blob])
                        track.last_motion = max(track.last_motion, other.last_motion)
                        track.hits = max(track.hits, other.hits)
                self._tracks = [t for t in self._tracks if t is track or t not in inside]
                track.person = person.confidence
                track.body = person.box
                track.body_anchor = _centre(track.blob_at(at))
                track.confirmed = at
            else:
                # Someone who has not moved since the camera started: a still person track.
                track = _Track(
                    id=self._next_id,
                    blob=person.box,
                    since=at,
                    last_motion=at,
                    person=person.confidence,
                    body=person.box,
                    body_anchor=_centre(person.box),
                    confirmed=at,
                )
                self._next_id += 1
                self._tracks.append(track)
            matched.add(track.id)
        for track in self._tracks:
            if (
                track.id not in matched
                and track.person is None
                and track.hits >= self._settings.min_hits
            ):
                track.looks += 1
        # A still person the check did not find has left.
        self._tracks = [
            t for t in self._tracks if t.id in matched or t.person is None or not self._still(t, at)
        ]

    def wants_check(self, at: datetime) -> bool:
        """Whether a person check would help: a still person to look at again, or a shown
        object that has not been looked at enough to tell whether it is someone."""
        return self.needs_check(at) or any(
            t.person is None
            and t.hits >= self._settings.min_hits
            and t.looks < self._settings.max_looks
            for t in self._tracks
        )

    def needs_check(self, at: datetime) -> bool:
        """Whether a still person is due to be looked at again."""
        every = timedelta(seconds=self._settings.confirm_seconds)
        return any(
            t.person is not None
            and self._still(t, at)
            and (t.confirmed is None or at - t.confirmed >= every)
            for t in self._tracks
        )

    def _absorb(self, tracks: list[_Track]) -> list[_Track]:
        """Tracks that lie mostly inside another (pieces of one mover that drifted apart) join
        it; the older one keeps its id, and a person label wins."""
        tracks = sorted(tracks, key=lambda track: (track.person is None, track.since))
        kept: list[_Track] = []
        for track in tracks:
            host = next(
                (k for k in kept if _inside(track.box(), self._region(k)) >= ABSORB_SHARE), None
            )
            if host is None:
                kept.append(track)
                continue
            host.blob = union([host.blob, track.blob])
            host.last_motion = max(host.last_motion, track.last_motion)
            host.hits = max(host.hits, track.hits)
        return sorted(kept, key=lambda track: track.id)

    def _region(self, track: _Track) -> BoundingBox:
        """Where its next motion is expected: its box, a margin around it."""
        return _grow(track.box(), self._margin)

    def _still(self, track: _Track, at: datetime) -> bool:
        return at - track.last_motion >= timedelta(seconds=self._settings.still_seconds)

    def _alive(self, track: _Track, at: datetime) -> bool:
        settings = self._settings
        if track.person is None:
            return at - track.last_motion <= timedelta(seconds=settings.max_gap_seconds)
        seen = max(track.last_motion, track.confirmed or track.last_motion)
        return at - seen <= timedelta(seconds=settings.hold_seconds)

    def _remember(self, track: _Track, at: datetime) -> None:
        track.history.append((at, track.blob))
        horizon = at - timedelta(seconds=self._settings.history_seconds)
        while track.history and track.history[0][0] < horizon:
            track.history.popleft()


def merge(boxes: Sequence[BoundingBox], margin: int) -> list[BoundingBox]:
    """Boxes closer than ``margin`` pixels, joined: one object per group."""
    groups = list(boxes)
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if _overlaps(_grow(groups[i], margin), groups[j]):
                    groups[i] = union([groups[i], groups[j]])
                    del groups[j]
                    merged = True
                    break
            if merged:
                break
    return groups


def union(boxes: Sequence[BoundingBox]) -> BoundingBox:
    left = min(b.x for b in boxes)
    top = min(b.y for b in boxes)
    right = max(b.x + b.width for b in boxes)
    bottom = max(b.y + b.height for b in boxes)
    return BoundingBox(x=left, y=top, width=right - left, height=bottom - top)


def _inside(box: BoundingBox, region: BoundingBox) -> float:
    """The share of ``box`` that lies inside ``region``."""
    left, top = max(box.x, region.x), max(box.y, region.y)
    right = min(box.x + box.width, region.x + region.width)
    bottom = min(box.y + box.height, region.y + region.height)
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top) / max(box.area, 1)


def _overlaps(a: BoundingBox, b: BoundingBox) -> bool:
    return (
        a.x < b.x + b.width
        and b.x < a.x + a.width
        and a.y < b.y + b.height
        and b.y < a.y + a.height
    )


def _grow(box: BoundingBox, margin: int) -> BoundingBox:
    return BoundingBox(
        x=box.x - margin,
        y=box.y - margin,
        width=box.width + 2 * margin,
        height=box.height + 2 * margin,
    )


def _centre(box: BoundingBox) -> tuple[float, float]:
    return (box.x + box.width / 2, box.y + box.height / 2)


def _shift(box: BoundingBox, dx: float, dy: float) -> BoundingBox:
    return BoundingBox(x=round(box.x + dx), y=round(box.y + dy), width=box.width, height=box.height)
