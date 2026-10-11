from datetime import UTC, datetime, timedelta

from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.motion import EventKind, MotionEvent
from vision_hub.schemas.devices import DeviceOut
from vision_hub.services.devices import DeviceView
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.sources import SyntheticSourceConfig

T0 = datetime(2026, 10, 11, 12, tzinfo=UTC)


def test_a_device_says_what_is_going_on_at_it_right_now() -> None:
    covered = MotionEvent(id="t1", device_id="porch", started_at=T0, kind=EventKind.COVERED)
    door = MotionEvent(
        id="l1",
        device_id="porch",
        started_at=T0 + timedelta(seconds=3),
        kind=EventKind.LINGERING,
        zone="Door",
    )
    view = DeviceView(
        spec=DeviceSpec(id="porch", name="Porch", source=SyntheticSourceConfig()),
        status=DeviceStatus.ONLINE,
        running=True,
        latest_frame=None,
        last_event=None,
        tampering=covered,
        lingering=(door,),
    )

    out = DeviceOut.from_view(view).model_dump(mode="json")

    assert out["tampering"] == {
        "event_id": "t1",
        "kind": "covered",
        "since": "2026-10-11T12:00:00Z",
    }
    assert out["lingering"] == [{"event_id": "l1", "zone": "Door", "since": "2026-10-11T12:00:03Z"}]
