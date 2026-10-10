"""Recorded motion events, as stored in the event history."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from vision_hub.domain.motion import BoundingBox, MotionEvent
from vision_hub.domain.storage import SnapshotKind, StoredSnapshot


@dataclass(frozen=True, slots=True)
class EventRecord:
    event: MotionEvent
    boxes: tuple[BoundingBox, ...] = ()
    snapshots: dict[SnapshotKind, StoredSnapshot] = field(default_factory=dict)
    interrupted: bool = False  # the hub stopped abruptly before the event ended
    snapshot_at: datetime | None = None  # when the snapshot's frame (the most motion) was captured
    clip_started_at: datetime | None = None  # the clip's first frame

    @property
    def complete(self) -> bool:
        """False while the event is in progress, and for interrupted events."""
        return self.event.ended_at is not None


@dataclass(frozen=True, slots=True)
class ActivityBucket:
    """How many events one camera started in one hour (UTC)."""

    device_id: str
    start: datetime  # the hour, timezone-aware UTC
    events: int
    people: int  # with a person seen
    alerts: int


class EventRepository(Protocol):
    async def add_started(self, event: MotionEvent) -> None: ...

    async def complete(
        self,
        event: MotionEvent,
        boxes: Sequence[BoundingBox],
        snapshots: Sequence[StoredSnapshot],
        *,
        snapshot_at: datetime | None = None,
        clip_started_at: datetime | None = None,
    ) -> None:
        """Store the finished event (inserting it if the start was never recorded), with when
        its snapshot was taken and its clip starts, so a player can mark those moments."""
        ...

    async def get(self, event_id: str) -> EventRecord | None: ...

    async def list(
        self,
        *,
        limit: int,
        device_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: tuple[datetime, str] | None = None,
        person: bool | None = None,
    ) -> Sequence[EventRecord]:
        """Newest first. ``before`` is the (started_at, id) keyset cursor; ``person`` keeps only
        events with (True) or without (False) a person seen; unchecked events match neither."""
        ...

    async def after(self, event_id: str, *, limit: int) -> Sequence[EventRecord]:
        """Events newer than ``event_id`` (UUIDv7 ids sort by time), oldest first."""
        ...

    async def activity(
        self, *, since: datetime, until: datetime, device_id: str | None = None
    ) -> Sequence[ActivityBucket]:
        """Events started in [since, until), counted per camera and hour by the database.
        Hours without events are left out."""
        ...

    async def expired(
        self,
        cutoff: datetime,
        *,
        limit: int,
        only_device: str | None = None,
        exclude_devices: Collection[str] = (),
    ) -> Sequence[EventRecord]: ...

    async def delete(self, event_ids: Collection[str]) -> int: ...

    async def mark_interrupted(self) -> int:
        """Flag events that never ended (the hub stopped abruptly); run before cameras start.
        Returns how many were flagged."""
        ...
