from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest

from vision_hub.vision.frame import Frame
from vision_hub.vision.tamper import (
    TamperEnded,
    TamperKind,
    TamperMonitor,
    TamperSettings,
    TamperStarted,
    measure,
    similarity,
)

T0 = datetime(2026, 10, 10, 12, tzinfo=UTC)
FPS = 10


def scene(seed: int = 1) -> Frame:
    """A room-like picture: walls, a door, furniture, some texture."""
    rng = np.random.default_rng(seed)
    frame = np.full((360, 640, 3), 120, np.uint8)
    for _ in range(25):
        x, y = rng.integers(0, 600), rng.integers(0, 330)
        w, h = rng.integers(20, 160), rng.integers(20, 160)
        shade = int(rng.integers(30, 230))
        cv2.rectangle(frame, (int(x), int(y)), (int(x + w), int(y + h)), (shade,) * 3, -1)
    noise = rng.normal(0, 4, frame.shape)
    return np.asarray(np.clip(frame + noise, 0, 255), np.uint8)


ROOM = scene()


def covered(_: Frame) -> Frame:
    return np.full_like(ROOM, 18)  # a hand over the lens


def blurred(frame: Frame) -> Frame:
    return np.asarray(cv2.GaussianBlur(frame, (41, 41), 0), np.uint8)


def moved(_: Frame) -> Frame:
    return scene(seed=7)  # turned to face something else


def run(
    monitor: TamperMonitor,
    seconds: float,
    picture: Callable[[float], Frame],
    start: float = 0,
) -> Iterator[TamperStarted | TamperEnded]:
    """Feeds ``seconds`` of frames at FPS from ``start``; yields the changes."""
    for index in range(round(seconds * FPS)):
        t = start + index / FPS
        if (change := monitor.update(picture(t), T0 + timedelta(seconds=t))) is not None:
            yield change


def learned(settings: TamperSettings | None = None) -> TamperMonitor:
    monitor = TamperMonitor(settings)
    assert list(run(monitor, 12, lambda _t: ROOM)) == []
    return monitor


@pytest.mark.parametrize(
    ("spoil", "kind"),
    [(covered, TamperKind.COVERED), (blurred, TamperKind.BLURRED), (moved, TamperKind.MOVED)],
)
def test_tampering_is_raised_after_it_lasts_and_cleared_when_normal_returns(
    spoil: Callable[[Frame], Frame], kind: TamperKind
) -> None:
    monitor = learned()

    during = list(run(monitor, 15, lambda _t: spoil(ROOM), start=12))
    after = list(run(monitor, 8, lambda _t: ROOM, start=27))

    assert during == [TamperStarted(kind=kind, at=T0 + timedelta(seconds=12))]
    assert monitor.active is None
    [ended] = after
    assert isinstance(ended, TamperEnded)
    assert ended.kind is kind
    assert not ended.accepted
    # Normal has to last a few seconds before it counts.
    assert ended.at >= T0 + timedelta(seconds=27 + 4)


def test_short_disturbances_raise_nothing() -> None:
    monitor = learned()

    def passing(t: float) -> Frame:
        frame = ROOM.copy()
        if 13 <= t < 16:  # someone walks right past the lens for three seconds
            cv2.rectangle(frame, (100, 0), (520, 360), (40, 40, 40), -1)
        return frame

    assert list(run(monitor, 20, passing, start=12)) == []


def test_dusk_is_not_tampering() -> None:
    """The light fading over 40 minutes: normal follows it."""
    monitor = learned(TamperSettings(checks_per_second=0.5))

    def fading(t: float) -> Frame:
        level = max(0.15, 1 - (t - 12) / 2400)
        return np.asarray(ROOM * level, np.uint8)

    changes = []
    for minute in range(40):
        start = 12 + minute * 60
        # One frame every 2 s is enough for the monitor at 0.5 checks a second.
        for t in np.arange(start, start + 60, 2.0):
            change = monitor.update(fading(float(t)), T0 + timedelta(seconds=float(t)))
            if change:
                changes.append(change)

    assert changes == []


def test_a_camera_moved_for_good_is_accepted_and_watched_from_there() -> None:
    monitor = learned(TamperSettings(accept_moved_minutes=1))
    elsewhere = moved(ROOM)

    changes = list(run(monitor, 75, lambda _t: elsewhere, start=12))
    later = list(run(monitor, 10, lambda _t: elsewhere, start=87))

    assert [type(c) for c in changes] == [TamperStarted, TamperEnded]
    assert isinstance(changes[1], TamperEnded)
    assert changes[1].accepted
    assert later == []  # the new view is normal now


def test_nothing_is_judged_while_learning() -> None:
    monitor = TamperMonitor()

    assert list(run(monitor, 9, lambda _t: covered(ROOM))) == []


def test_the_measures() -> None:
    room, dark = measure(ROOM), measure(covered(ROOM))

    assert room.edges.shape == (90, 160)
    assert dark.texture < room.texture * 0.1
    assert measure(blurred(ROOM)).sharpness < room.sharpness * 0.3
    # Dimming changes neither texture much nor sharpness: they are relative.
    dim = measure(np.asarray(ROOM * 0.4, np.uint8))
    assert dim.sharpness == pytest.approx(room.sharpness, rel=0.15)
    assert dim.texture > room.texture * 0.6
    assert similarity(room.edges, measure(ROOM).edges) == pytest.approx(1)
    assert similarity(room.edges, measure(moved(ROOM)).edges) < 0.35
    assert similarity(room.edges, dark.edges) == 0.0  # no edges at all
