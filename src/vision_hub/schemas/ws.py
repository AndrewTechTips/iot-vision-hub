"""WebSocket protocol (``/api/v1/ws/events``), version 1.

Server -> client messages share an envelope: ``{"type", "v", "ts", "device_id"?, "data"?}``.
Client -> server messages: ``subscribe``, ``unsubscribe`` and ``pong``.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, assert_never
from uuid import UUID

from pydantic import Field, TypeAdapter

from vision_hub.core.security import utc_now
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import (
    CameraEvent,
    CameraRestoredEvent,
    CameraTamperedEvent,
    DeviceStatusChanged,
    MotionEndedEvent,
    MotionStartedEvent,
)
from vision_hub.domain.history import EventRecord
from vision_hub.domain.motion import BoundingBox, EventKind, MotionEvent
from vision_hub.schemas.base import ApiSchema, RequestSchema, UtcDateTime

PROTOCOL_VERSION: Literal[1] = 1


class _ServerMessage(ApiSchema):
    v: Literal[1] = PROTOCOL_VERSION
    ts: UtcDateTime = Field(default_factory=utc_now)


class MotionStartedData(ApiSchema):
    event_id: str
    started_at: datetime
    kind: EventKind = Field(
        default=EventKind.MOTION,
        description='"motion", or the camera being tampered with: "covered", "blurred", "moved"',
    )


class MotionEndedData(MotionStartedData):
    ended_at: datetime | None
    peak_area_ratio: float
    motion_frames: int
    boxes: list[BoundingBox]
    person: bool | None = Field(description="A person was seen; null when nobody could check")
    person_confidence: float | None
    alert: bool = Field(description="Show an alert (false: people only, and none was seen)")


class DeviceStatusData(ApiSchema):
    status: DeviceStatus


class SubscriptionData(ApiSchema):
    devices: list[str] | None = Field(description="Subscribed devices; null means all")
    excluded: list[str] = Field(description="Devices excluded while subscribed to all")


class ReplayDoneData(ApiSchema):
    count: int
    truncated: bool = Field(description="More events were missed than replayed; use the API")


class ErrorData(ApiSchema):
    code: str
    message: str


class MotionStartedMessage(_ServerMessage):
    type: Literal["motion.started"] = "motion.started"
    device_id: str
    replay: bool = False  # true when re-sent from history after a "resume"
    data: MotionStartedData


class MotionEndedMessage(_ServerMessage):
    type: Literal["motion.ended"] = "motion.ended"
    device_id: str
    replay: bool = False
    data: MotionEndedData


class DeviceStatusMessage(_ServerMessage):
    type: Literal["device.status"] = "device.status"
    device_id: str
    data: DeviceStatusData


class SubscriptionMessage(_ServerMessage):
    type: Literal["subscription"] = "subscription"
    data: SubscriptionData


class ReplayDoneMessage(_ServerMessage):
    type: Literal["replay.done"] = "replay.done"
    data: ReplayDoneData


class PingMessage(_ServerMessage):
    type: Literal["ping"] = "ping"


class ErrorMessage(_ServerMessage):
    type: Literal["error"] = "error"
    data: ErrorData


type ServerMessage = Annotated[
    MotionStartedMessage
    | MotionEndedMessage
    | DeviceStatusMessage
    | SubscriptionMessage
    | ReplayDoneMessage
    | PingMessage
    | ErrorMessage,
    Field(discriminator="type"),
]


class SubscribeMessage(RequestSchema):
    """Replace the subscription. ``devices: null`` (or omitted) means every device."""

    type: Literal["subscribe"]
    devices: list[str] | None = Field(default=None, max_length=256)


class UnsubscribeMessage(RequestSchema):
    type: Literal["unsubscribe"]
    devices: list[str] = Field(min_length=1, max_length=256)


class PongMessage(RequestSchema):
    type: Literal["pong"]


class ResumeMessage(RequestSchema):
    """After reconnecting: replay events newer than the last one the client saw."""

    type: Literal["resume"]
    after: UUID


type ClientMessage = Annotated[
    SubscribeMessage | UnsubscribeMessage | PongMessage | ResumeMessage,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def to_message(event: CameraEvent) -> ServerMessage:
    match event:
        case DeviceStatusChanged():
            return DeviceStatusMessage(
                device_id=event.device_id, data=DeviceStatusData(status=event.status)
            )
        # Tampering travels as an event of its kind: clients that only know motion still see
        # it start and end, and replay after a reconnect works the same.
        case MotionStartedEvent(event=motion) | CameraTamperedEvent(event=motion):
            return MotionStartedMessage(
                device_id=motion.device_id,
                data=MotionStartedData(
                    event_id=motion.id, started_at=motion.started_at, kind=motion.kind
                ),
            )
        case MotionEndedEvent(event=motion):
            return _ended(motion, event.boxes)
        case CameraRestoredEvent(event=motion):
            return _ended(motion, ())
        case _:  # pragma: no cover - mypy proves the match is exhaustive
            assert_never(event)


def _ended(
    motion: MotionEvent, boxes: Sequence[BoundingBox], *, replay: bool = False
) -> MotionEndedMessage:
    return MotionEndedMessage(
        device_id=motion.device_id,
        replay=replay,
        data=MotionEndedData(
            event_id=motion.id,
            started_at=motion.started_at,
            ended_at=motion.ended_at,
            peak_area_ratio=motion.peak_area_ratio,
            motion_frames=motion.motion_frames,
            boxes=list(boxes),
            person=motion.person,
            person_confidence=motion.person_confidence,
            alert=motion.alert,
            kind=motion.kind,
        ),
    )


def replayed_message(record: EventRecord) -> ServerMessage:
    """A stored event as it would have been sent live, flagged as a replay."""
    motion = record.event
    if not record.complete:
        return MotionStartedMessage(
            device_id=motion.device_id,
            replay=True,
            data=MotionStartedData(
                event_id=motion.id, started_at=motion.started_at, kind=motion.kind
            ),
        )
    return _ended(motion, record.boxes, replay=True)
