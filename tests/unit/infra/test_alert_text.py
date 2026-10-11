from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from vision_hub.domain.motion import EventKind, MotionEvent
from vision_hub.domain.notifications import Alert
from vision_hub.infra.notifiers.text import describe

T0 = datetime(2026, 10, 11, 11, 5, 23, tzinfo=UTC)
BUCHAREST = ZoneInfo("Europe/Bucharest")


def alert(**fields: object) -> Alert:
    event = MotionEvent(id="evt-1", device_id="door", started_at=T0, **fields)  # type: ignore[arg-type]
    return Alert(device_id="door", device_name="Front door", event=event, image_jpeg=b"")


def test_a_person_with_how_long_it_lasted_in_local_time() -> None:
    text = describe(
        alert(person=True, ended_at=T0 + timedelta(seconds=21)),
        zone=BUCHAREST,
        public_url="https://hub.example.com/",
    )

    assert text.title == "Person at Front door"
    assert text.body == "11 Oct 2026, 14:05:23 EEST. It lasted 21 s."
    assert text.link == "https://hub.example.com/events?event=evt-1"


def test_motion_without_a_person_or_a_link() -> None:
    text = describe(alert(person=False), zone=ZoneInfo("UTC"))

    assert (text.title, text.body, text.link) == (
        "Motion at Front door",
        "11 Oct 2026, 11:05:23 UTC.",
        None,
    )


def test_zone_rules_and_tampering() -> None:
    utc = ZoneInfo("UTC")

    lingering = describe(alert(kind=EventKind.LINGERING, zone="Doorstep"), zone=utc)
    crossed = describe(alert(kind=EventKind.CROSSED, zone="Gate"), zone=utc)
    covered = describe(alert(kind=EventKind.COVERED), zone=utc)

    assert (lingering.title, lingering.body) == (
        "Someone lingering at Front door",
        "In Doorstep since 11 Oct 2026, 11:05:23 UTC.",
    )
    assert (crossed.title, crossed.body) == (
        "Someone crossed Gate",
        "At Front door, 11 Oct 2026, 11:05:23 UTC.",
    )
    assert covered.title == "Front door camera covered"
    assert covered.body.startswith("Since 11 Oct 2026, 11:05:23 UTC: its picture went flat")
