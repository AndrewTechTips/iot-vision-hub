"""An alert in a few plain words, for channels that show text as it is (Telegram, webhooks).
The dashboard and the service worker word alerts themselves, in the viewer's language."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from vision_hub.domain.motion import EventKind
from vision_hub.domain.notifications import Alert

# What tampering means, in a sentence: (short word, explanation).
TAMPERING = {
    EventKind.COVERED: ("covered", "its picture went flat, as if something covers the lens"),
    EventKind.BLURRED: ("out of focus", "its picture went blurry: a smeared lens or turned focus"),
    EventKind.MOVED: ("moved", "it no longer sees its usual view, as if it was turned"),
}


@dataclass(frozen=True, slots=True)
class AlertText:
    title: str  # "Person at Front door"
    body: str  # "11 Oct 2026, 14:05:23 EEST. The visit lasted 21 s."
    link: str | None  # the event in the dashboard, when its public address is known


def describe(alert: Alert, *, zone: ZoneInfo, public_url: str | None = None) -> AlertText:
    event = alert.event
    device = alert.device_name
    when = clock(event.started_at, zone)
    where = event.zone or "a zone"
    if event.kind.tamper:
        word, why = TAMPERING[event.kind]
        title, body = f"{device} camera {word}", f"Since {when}: {why}."
    elif event.kind is EventKind.LINGERING:
        title, body = f"Someone lingering at {device}", f"In {where} since {when}."
    elif event.kind is EventKind.CROSSED:
        title, body = f"Someone crossed {where}", f"At {device}, {when}."
    else:
        title = f"{'Person' if event.person else 'Motion'} at {device}"
        body = f"{when}."
        if event.ended_at is not None:
            seconds = round((event.ended_at - event.started_at).total_seconds())
            body += f" It lasted {seconds} s."
    link = f"{public_url.rstrip('/')}/events?event={event.id}" if public_url else None
    return AlertText(title=title, body=body, link=link)


def clock(at: datetime, zone: ZoneInfo) -> str:
    """``11 Oct 2026, 14:05:23 EEST``: a delayed alert still says which day."""
    local = at.astimezone(zone)
    return f"{local.day} {local:%b %Y, %H:%M:%S %Z}"
