from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.config import DetectionConfig, Zone
from vision_hub.vision.tracking import TrackView
from vision_hub.vision.zones import (
    ZoneEnded,
    ZoneRule,
    ZoneStarted,
    ZoneWatcher,
    contains,
    crosses,
)

T0 = datetime(2026, 10, 11, 12, tzinfo=UTC)
DOORSTEP = Zone(
    name="Doorstep", kind="area", points=((0.5, 0.5), (1, 0.5), (1, 1), (0.5, 1)), dwell_seconds=5
)
GATE = Zone(name="Gate", kind="line", points=((0.5, 0), (0.5, 1)))


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def someone(x: int, *, track: int = 1, person: float | None = 0.9) -> TrackView:
    """A person whose feet are at (x, 400) on a 640x480 picture."""
    return TrackView(
        id=track, box=BoundingBox(x - 25, 200, 50, 200), person=person, still=False, since=T0
    )


def watch(zones: list[Zone]) -> ZoneWatcher:
    return ZoneWatcher(zones, 640, 480)


class TestAreas:
    def test_lingering_starts_after_the_dwell_and_ends_when_they_leave(self) -> None:
        watcher = watch([DOORSTEP])
        changes = []
        for tenth in range(80):  # 8 s at the door (feet at x=480: inside), then away
            changes += watcher.update([someone(480)], at(tenth / 10))
        for tenth in range(80, 120):
            changes += watcher.update([someone(100)], at(tenth / 10))

        assert changes == [
            ZoneStarted("Doorstep", ZoneRule.LINGERING, at(0)),
            ZoneEnded("Doorstep", ZoneRule.LINGERING, at(7.9)),
        ]

    def test_a_short_visit_raises_nothing(self) -> None:
        watcher = watch([DOORSTEP])

        changes = [c for tenth in range(30) for c in watcher.update([someone(480)], at(tenth / 10))]
        changes += watcher.update([], at(6))

        assert changes == []

    def test_a_new_track_id_does_not_reset_the_clock(self) -> None:
        watcher = watch([DOORSTEP])
        changes = []
        for tenth in range(60):
            changes += watcher.update([someone(480, track=1 if tenth < 30 else 2)], at(tenth / 10))

        assert changes == [ZoneStarted("Doorstep", ZoneRule.LINGERING, at(0))]

    def test_only_people_count_unless_nobody_can_check(self) -> None:
        watcher = watch([DOORSTEP])
        thing = someone(480, person=None)

        quiet = [c for tenth in range(70) for c in watcher.update([thing], at(tenth / 10))]
        loud = [
            c
            for tenth in range(70, 140)
            for c in watcher.update([thing], at(tenth / 10), anyone=True)
        ]

        assert quiet == []
        assert [type(c) for c in loud] == [ZoneStarted]

    def test_feet_on_the_bottom_edge_are_in_an_area_drawn_to_it(self) -> None:
        watcher = watch([DOORSTEP])
        close_up = TrackView(  # walked up to the camera: the box ends at the picture's edge
            id=1, box=BoundingBox(400, 100, 200, 380), person=0.9, still=False, since=T0
        )

        changes = [c for tenth in range(60) for c in watcher.update([close_up], at(tenth / 10))]

        assert [type(c) for c in changes] == [ZoneStarted]

    def test_closing_ends_lingering(self) -> None:
        watcher = watch([DOORSTEP])
        for tenth in range(60):
            watcher.update([someone(480)], at(tenth / 10))

        assert watcher.close(at(7)) == [ZoneEnded("Doorstep", ZoneRule.LINGERING, at(7))]


class TestLines:
    def test_crossing_is_noticed_once(self) -> None:
        watcher = watch([GATE])
        changes = []
        for step, x in enumerate([200, 280, 300, 340, 360, 300, 340]):  # crosses at 320, wobbles
            changes += watcher.update([someone(x)], at(step / 10))

        assert changes == [
            ZoneStarted("Gate", ZoneRule.CROSSED, at(0.3)),
            ZoneEnded("Gate", ZoneRule.CROSSED, at(0.3)),
        ]

    def test_crossing_back_later_counts_again(self) -> None:
        watcher = watch([GATE])
        watcher.update([someone(300)], at(0))
        first = watcher.update([someone(340)], at(0.1))
        watcher.update([someone(340)], at(5))
        back = watcher.update([someone(300)], at(5.1))

        assert [type(c) for c in first] == [ZoneStarted, ZoneEnded]
        assert [type(c) for c in back] == [ZoneStarted, ZoneEnded]

    def test_walking_alongside_is_not_crossing(self) -> None:
        watcher = watch([GATE])

        changes = [
            c
            for step in range(10)
            for c in watcher.update([someone(100 + step * 5)], at(step / 10))
        ]

        assert changes == []


def test_geometry() -> None:
    square = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    assert contains(square, (0.5, 0.5))
    assert not contains(square, (1.5, 0.5))
    assert crosses((0, 0.5), (1, 0.5), (0.5, 0), (0.5, 1))
    assert not crosses((0, 0.5), (0.4, 0.5), (0.5, 0), (0.5, 1))


class TestConfig:
    def test_zones_are_checked(self) -> None:
        with pytest.raises(ValidationError, match="at least 3 points"):
            Zone(name="A", kind="area", points=((0, 0), (1, 1)))
        with pytest.raises(ValidationError, match="exactly 2 points"):
            Zone(name="L", kind="line", points=((0, 0), (1, 1), (0, 1)))
        with pytest.raises(ValidationError, match="unique"):
            DetectionConfig(zones=(GATE, GATE.model_copy(update={"name": "gate"})))

    def test_no_zones_by_default(self) -> None:
        assert DetectionConfig().zones == ()
        assert not watch([]).active
