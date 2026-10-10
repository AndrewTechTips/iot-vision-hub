from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from vision_hub.domain.motion import BoundingBox, EventKind, MotionEvent
from vision_hub.domain.storage import SnapshotKind, StoredSnapshot
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import SnapshotRow
from vision_hub.infra.db.repositories.events import SqlEventRepository
from vision_hub.infra.storage.local import LocalSnapshotStore
from vision_hub.services.events import RetentionService

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def event(n: int, device_id: str = "porch", *, started: datetime | None = None) -> MotionEvent:
    """Ids are zero-padded so they sort like UUIDv7 (time-ordered)."""
    start = started or T0 + timedelta(minutes=n)
    return MotionEvent(
        id=f"00000000-0000-7000-8000-{n:012d}",
        device_id=device_id,
        started_at=start,
        ended_at=start + timedelta(seconds=5),
        peak_area_ratio=0.1,
        motion_frames=50,
    )


def snapshot(kind: SnapshotKind, n: int) -> StoredSnapshot:
    return StoredSnapshot(kind=kind, path=f"2026/10/01/{n}-{kind}.jpg", size_bytes=100)


class TestRecording:
    async def test_start_then_complete(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        started = MotionEvent(id=event(1).id, device_id="porch", started_at=T0)

        await repository.add_started(started)
        interrupted = await repository.get(started.id)
        await repository.complete(
            event(1), [BoundingBox(1, 2, 3, 4)], [snapshot(SnapshotKind.CLEAN, 1)]
        )
        record = await repository.get(started.id)

        assert interrupted is not None
        assert interrupted.complete is False
        assert record is not None
        assert record.complete is True
        assert record.boxes == (BoundingBox(1, 2, 3, 4),)
        assert record.snapshots[SnapshotKind.CLEAN].path == "2026/10/01/1-clean.jpg"
        assert record.event.ended_at == event(1).ended_at

    async def test_moments_are_kept(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        found = T0 + timedelta(seconds=2)

        await repository.complete(
            replace(event(3), person=True, person_at=found),
            [],
            [],
            snapshot_at=T0 + timedelta(seconds=3),
            clip_started_at=T0 - timedelta(seconds=3),
        )
        record = await repository.get(event(3).id)

        assert record is not None
        assert record.event.person_at == found
        assert record.snapshot_at == T0 + timedelta(seconds=3)
        assert record.clip_started_at == T0 - timedelta(seconds=3)

    async def test_complete_without_a_recorded_start(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)

        await repository.complete(event(2), [], [])

        assert await repository.get(event(2).id) is not None

    async def test_unknown_event(self, sessions: Sessions) -> None:
        assert await SqlEventRepository(sessions).get(event(99).id) is None


class TestQueries:
    async def seed(self, repository: SqlEventRepository) -> None:
        for n in range(1, 7):
            await repository.complete(event(n, "porch" if n % 2 else "gate"), [], [])

    async def test_newest_first_with_filters(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        await self.seed(repository)

        everything = await repository.list(limit=10)
        porch = await repository.list(limit=10, device_id="porch")
        window = await repository.list(
            limit=10, since=T0 + timedelta(minutes=2), until=T0 + timedelta(minutes=4)
        )

        assert [r.event.id[-1] for r in everything] == list("654321")
        assert [r.event.id[-1] for r in porch] == list("531")
        assert [r.event.id[-1] for r in window] == list("32")

    async def test_keyset_pagination(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        await self.seed(repository)

        first = await repository.list(limit=4)
        last = first[-1].event
        second = await repository.list(limit=4, before=(last.started_at, last.id))

        assert [r.event.id[-1] for r in first] == list("6543")
        assert [r.event.id[-1] for r in second] == list("21")

    async def test_events_after_an_id(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        await self.seed(repository)

        newer = await repository.after(event(3).id, limit=10)

        assert [r.event.id[-1] for r in newer] == list("456")  # oldest first


class TestPersons:
    async def test_person_results_are_stored_and_filtered(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        verdicts = {1: (True, 0.91, True), 2: (False, 0.12, False), 3: (None, None, True)}
        for n, (person, confidence, alert) in verdicts.items():
            await repository.complete(
                replace(event(n), person=person, person_confidence=confidence, alert=alert),
                [],
                [],
            )

        stored = {r.event.id[-1]: r.event for r in await repository.list(limit=10)}
        with_person = await repository.list(limit=10, person=True)
        without = await repository.list(limit=10, person=False)

        assert (stored["1"].person, stored["1"].person_confidence, stored["1"].alert) == (
            True,
            0.91,
            True,
        )
        assert (stored["2"].person, stored["2"].alert) == (False, False)
        assert (stored["3"].person, stored["3"].person_confidence) == (None, None)
        assert [r.event.id[-1] for r in with_person] == ["1"]
        assert [r.event.id[-1] for r in without] == ["2"]  # unchecked events match neither


class TestTamperEvents:
    async def test_kind_is_kept_and_open_events_can_be_ended(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        covered = replace(event(5), ended_at=None, kind=EventKind.COVERED)

        await repository.complete(covered, [], [snapshot(SnapshotKind.CLEAN, 5)])
        await repository.end(covered.id, T0 + timedelta(minutes=3))
        await repository.end(covered.id, T0 + timedelta(hours=1))  # only open events end
        record = await repository.get(covered.id)

        assert record is not None
        assert record.event.kind is EventKind.COVERED
        assert record.event.ended_at == T0 + timedelta(minutes=3)
        assert record.snapshots[SnapshotKind.CLEAN].path == "2026/10/01/5-clean.jpg"

    async def test_tampering_is_not_activity(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        await repository.complete(event(1), [], [])
        await repository.complete(replace(event(2), kind=EventKind.MOVED), [], [])

        buckets = await repository.activity(since=T0, until=T0 + timedelta(hours=1))

        assert [b.events for b in buckets] == [1]


class TestActivity:
    async def test_counts_per_camera_and_hour_in_the_database(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        seen = [
            (1, "porch", T0 + timedelta(minutes=1), True, True),
            (2, "porch", T0 + timedelta(minutes=59, seconds=59), False, False),
            (3, "porch", T0 + timedelta(hours=1), None, True),  # the next hour
            (4, "gate", T0 + timedelta(minutes=30), True, True),
            (5, "gate", T0 - timedelta(days=2), True, True),  # before the window
        ]
        for n, device, started, person, alert in seen:
            base = event(n, device, started=started)
            await repository.complete(replace(base, person=person, alert=alert), [], [])

        day = timedelta(days=1)
        buckets = await repository.activity(since=T0 - day, until=T0 + day)

        assert [(b.device_id, b.start, b.events, b.people, b.alerts) for b in buckets] == [
            ("gate", T0, 1, 1, 1),
            ("porch", T0, 2, 1, 1),
            ("porch", T0 + timedelta(hours=1), 1, 0, 1),
        ]
        assert all(b.start.tzinfo is UTC for b in buckets)

    async def test_one_camera_and_an_empty_window(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        for n in range(1, 5):
            await repository.complete(event(n, "porch" if n % 2 else "gate"), [], [])

        hour = timedelta(hours=1)
        porch = await repository.activity(since=T0, until=T0 + hour, device_id="porch")
        empty = await repository.activity(since=T0 + hour, until=T0 + 2 * hour)

        assert [(b.device_id, b.events) for b in porch] == [("porch", 2)]
        assert empty == []


class TestRetention:
    async def test_deletes_rows_and_files_per_device_policy(
        self, sessions: Sessions, tmp_path: Path
    ) -> None:
        repository = SqlEventRepository(sessions)
        store = LocalSnapshotStore(tmp_path / "snapshots")
        now = T0 + timedelta(days=40)
        ages = {"porch": 35, "gate": 35, "deleted-camera": 35, "porch-new": 5}
        for n, (device, days) in enumerate(ages.items(), start=1):
            device_id = device.removesuffix("-new")
            started = now - timedelta(days=days)
            image = await store.save(event(n).id, SnapshotKind.CLEAN, b"jpeg", started)
            clip = await store.save(event(n).id, SnapshotKind.CLIP, b"webm", started)
            await repository.complete(event(n, device_id, started=started), [], [image, clip])

        retention = RetentionService(
            repository,
            store,
            default_days=30,
            overrides=lambda: _async({"gate": 90}),  # gate keeps events for 90 days
            interval_seconds=3600,
            clock=lambda: now,
        )
        deleted = await retention.run_once()

        remaining = {r.event.device_id for r in await repository.list(limit=10)}
        assert deleted == 2  # porch (35 d > 30 d) and the deleted camera's history
        assert remaining == {"gate", "porch"}  # gate: 35 d < 90 d; porch: the 5-day-old event
        assert len(list((tmp_path / "snapshots").rglob("*.jpg"))) == 2
        assert len(list((tmp_path / "snapshots").rglob("*.webm"))) == 2  # clips go with them
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SnapshotRow)) == 4

    async def test_nothing_to_delete(self, sessions: Sessions, tmp_path: Path) -> None:
        retention = RetentionService(
            SqlEventRepository(sessions),
            LocalSnapshotStore(tmp_path),
            default_days=30,
            overrides=lambda: _async({}),
            interval_seconds=3600,
        )

        assert await retention.run_once() == 0


async def _async(value: dict[str, int]) -> dict[str, int]:
    return value
