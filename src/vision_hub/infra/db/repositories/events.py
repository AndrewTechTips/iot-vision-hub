from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, Select, case, delete, func, literal, select, tuple_, update
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.functions import FunctionElement

from vision_hub.domain.history import ActivityBucket, EventRecord
from vision_hub.domain.motion import BoundingBox, MotionEvent
from vision_hub.domain.storage import SnapshotKind, StoredSnapshot
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import MotionEventRow, SnapshotRow


class SqlEventRepository:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def add_started(self, event: MotionEvent) -> None:
        async with self._sessions.begin() as session:
            session.add(_new_row(event))

    async def complete(
        self,
        event: MotionEvent,
        boxes: Sequence[BoundingBox],
        snapshots: Sequence[StoredSnapshot],
        *,
        snapshot_at: datetime | None = None,
        clip_started_at: datetime | None = None,
    ) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(MotionEventRow, event.id)
            if row is None:  # the start was never recorded (e.g. the database was down)
                row = _new_row(event)
                session.add(row)
            row.ended_at = event.ended_at
            row.peak_area_ratio = event.peak_area_ratio
            row.motion_frames = event.motion_frames
            row.person = event.person
            row.person_confidence = event.person_confidence
            row.person_at = event.person_at
            row.alert = event.alert
            row.snapshot_at = snapshot_at
            row.clip_started_at = clip_started_at
            row.boxes = [{"x": b.x, "y": b.y, "width": b.width, "height": b.height} for b in boxes]
            row.snapshots = [
                SnapshotRow(kind=s.kind.value, path=s.path, size_bytes=s.size_bytes)
                for s in snapshots
            ]

    async def get(self, event_id: str) -> EventRecord | None:
        async with self._sessions() as session:
            row = await session.get(MotionEventRow, event_id)
        return _to_record(row) if row else None

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
        query = select(MotionEventRow)
        if person is not None:
            query = query.where(MotionEventRow.person.is_(person))
        if device_id is not None:
            query = query.where(MotionEventRow.device_id == device_id)
        if since is not None:
            query = query.where(MotionEventRow.started_at >= since)
        if until is not None:
            query = query.where(MotionEventRow.started_at < until)
        if before is not None:
            # Typed literals: PostgreSQL will not compare a uuid column with a VARCHAR.
            started_at, event_id = before
            query = query.where(
                tuple_(MotionEventRow.started_at, MotionEventRow.id)
                < tuple_(
                    literal(started_at, MotionEventRow.started_at.type),
                    literal(event_id, MotionEventRow.id.type),
                )
            )
        query = query.order_by(MotionEventRow.started_at.desc(), MotionEventRow.id.desc())
        return await self._fetch(query.limit(limit))

    async def after(self, event_id: str, *, limit: int) -> Sequence[EventRecord]:
        query = (
            select(MotionEventRow)
            .where(MotionEventRow.id > event_id)
            .order_by(MotionEventRow.id)
            .limit(limit)
        )
        return await self._fetch(query)

    async def activity(
        self, *, since: datetime, until: datetime, device_id: str | None = None
    ) -> Sequence[ActivityBucket]:
        hour = epoch_hour(MotionEventRow.started_at).label("hour")
        query = (
            select(
                MotionEventRow.device_id,
                hour,
                func.count().label("events"),
                func.sum(case((MotionEventRow.person.is_(True), 1), else_=0)).label("people"),
                func.sum(case((MotionEventRow.alert.is_(True), 1), else_=0)).label("alerts"),
            )
            .where(MotionEventRow.started_at >= since, MotionEventRow.started_at < until)
            .group_by(MotionEventRow.device_id, hour)
            .order_by(hour, MotionEventRow.device_id)
        )
        if device_id is not None:
            query = query.where(MotionEventRow.device_id == device_id)
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        return [
            ActivityBucket(
                device_id=row.device_id,
                start=datetime.fromtimestamp(int(row.hour) * 3600, UTC),
                events=int(row.events),
                people=int(row.people or 0),
                alerts=int(row.alerts or 0),
            )
            for row in rows
        ]

    async def expired(
        self,
        cutoff: datetime,
        *,
        limit: int,
        only_device: str | None = None,
        exclude_devices: Collection[str] = (),
    ) -> Sequence[EventRecord]:
        query = select(MotionEventRow).where(MotionEventRow.started_at < cutoff)
        if only_device is not None:
            query = query.where(MotionEventRow.device_id == only_device)
        if exclude_devices:
            query = query.where(MotionEventRow.device_id.not_in(exclude_devices))
        return await self._fetch(query.order_by(MotionEventRow.started_at).limit(limit))

    async def delete(self, event_ids: Collection[str]) -> int:
        if not event_ids:
            return 0
        async with self._sessions.begin() as session:
            # Snapshot rows go with them (ON DELETE CASCADE).
            result = await session.execute(
                delete(MotionEventRow).where(MotionEventRow.id.in_(event_ids))
            )
        return int(getattr(result, "rowcount", 0))

    async def mark_interrupted(self) -> int:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(MotionEventRow)
                .where(MotionEventRow.ended_at.is_(None), MotionEventRow.interrupted.is_(False))
                .values(interrupted=True)
            )
        return int(getattr(result, "rowcount", 0))

    async def _fetch(self, query: Select[MotionEventRow]) -> Sequence[EventRecord]:
        async with self._sessions() as session:
            rows = await session.scalars(query)
            return [_to_record(row) for row in rows]


def _new_row(event: MotionEvent) -> MotionEventRow:
    return MotionEventRow(
        id=event.id,
        device_id=event.device_id,
        started_at=event.started_at,
        ended_at=event.ended_at,
        peak_area_ratio=event.peak_area_ratio,
        motion_frames=event.motion_frames,
        person=event.person,
        person_confidence=event.person_confidence,
        alert=event.alert,
        boxes=[],
        snapshots=[],
    )


def _to_record(row: MotionEventRow) -> EventRecord:
    return EventRecord(
        event=MotionEvent(
            id=row.id,
            device_id=row.device_id,
            started_at=row.started_at,
            ended_at=row.ended_at,
            peak_area_ratio=row.peak_area_ratio,
            motion_frames=row.motion_frames,
            person=row.person,
            person_confidence=row.person_confidence,
            person_at=row.person_at,
            alert=row.alert,
        ),
        boxes=tuple(BoundingBox(**box) for box in row.boxes),
        snapshots={
            SnapshotKind(s.kind): StoredSnapshot(SnapshotKind(s.kind), s.path, s.size_bytes)
            for s in row.snapshots
        },
        interrupted=row.interrupted,
        snapshot_at=row.snapshot_at,
        clip_started_at=row.clip_started_at,
    )


class epoch_hour(FunctionElement[int]):  # noqa: N801 - reads like the SQL function it stands for
    """Hours since the Unix epoch (UTC) of a timestamp column: a portable GROUP BY key. The
    database does the counting, and the result maps back to an exact instant on any backend."""

    type = Integer()
    inherit_cache = True
    name = "epoch_hour"


@compiles(epoch_hour, "postgresql")
def _epoch_hour_postgresql(element: epoch_hour, compiler: SQLCompiler, **kw: Any) -> str:
    column = compiler.process(element.clauses, **kw)
    return f"CAST(floor(extract(epoch FROM {column}) / 3600) AS INTEGER)"


@compiles(epoch_hour, "sqlite")
def _epoch_hour_sqlite(element: epoch_hour, compiler: SQLCompiler, **kw: Any) -> str:
    # Stored as UTC text without an offset (UtcDateTime); strftime reads it as UTC.
    column = compiler.process(element.clauses, **kw)
    return f"CAST(strftime('%s', {column}) AS INTEGER) / 3600"
