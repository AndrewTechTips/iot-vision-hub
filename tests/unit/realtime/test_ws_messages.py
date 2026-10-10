from dataclasses import replace
from datetime import UTC, datetime, timedelta

from vision_hub.domain.events import CameraRestoredEvent, CameraTamperedEvent
from vision_hub.domain.history import EventRecord
from vision_hub.domain.motion import EventKind, MotionEvent
from vision_hub.schemas.ws import (
    MotionEndedMessage,
    MotionStartedMessage,
    replayed_message,
    to_message,
)

T0 = datetime(2026, 10, 10, 12, tzinfo=UTC)
COVERED = MotionEvent(id="t1", device_id="porch", started_at=T0, kind=EventKind.COVERED)


def test_tampering_travels_as_an_event_of_its_kind() -> None:
    started = to_message(CameraTamperedEvent(event=COVERED, snapshot_jpeg=b"x"))
    ended = to_message(
        CameraRestoredEvent(event=replace(COVERED, ended_at=T0 + timedelta(seconds=20)))
    )

    assert isinstance(started, MotionStartedMessage)
    assert (started.device_id, started.data.event_id, started.data.kind) == (
        "porch",
        "t1",
        EventKind.COVERED,
    )
    assert isinstance(ended, MotionEndedMessage)
    assert ended.data.kind is EventKind.COVERED
    assert ended.data.alert is True
    assert ended.data.boxes == []


def test_replays_keep_the_kind() -> None:
    open_record = EventRecord(event=COVERED)
    over = EventRecord(event=replace(COVERED, ended_at=T0 + timedelta(seconds=5)))

    started, ended = replayed_message(open_record), replayed_message(over)

    assert isinstance(started, MotionStartedMessage)
    assert started.replay
    assert started.data.kind is EventKind.COVERED
    assert isinstance(ended, MotionEndedMessage)
    assert ended.replay
    assert ended.data.kind is EventKind.COVERED


def test_motion_is_the_default_kind() -> None:
    message = replayed_message(EventRecord(event=replace(COVERED, kind=EventKind.MOTION)))

    assert message.model_dump(mode="json")["data"]["kind"] == "motion"
