from datetime import datetime
from typing import Literal, cast

from pydantic import Field

from vision_hub.core.security import UrlSigner
from vision_hub.domain.history import ActivityBucket, EventRecord
from vision_hub.domain.motion import BoundingBox, EventKind
from vision_hub.domain.storage import IMAGE_KINDS, SnapshotKind
from vision_hub.schemas.base import ApiSchema

type ImageKind = Literal["clean", "annotated", "thumbnail"]


class SnapshotLink(ApiSchema):
    kind: ImageKind
    url: str = Field(description="Signed, expiring link: usable directly in an <img> tag")
    size_bytes: int


class ClipLink(ApiSchema):
    url: str = Field(description="Signed, expiring link: usable directly in a <video> tag")
    content_type: str = Field(description='"video/webm" (VP8)')
    size_bytes: int
    started_at: datetime | None = Field(
        description="When the clip's first frame was captured (its 0:00); null for older events"
    )


class EventOut(ApiSchema):
    id: str
    device_id: str
    kind: EventKind = Field(
        description='"motion", or the camera being tampered with: "covered", "blurred", "moved"'
    )
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    complete: bool = Field(description="True once the event has ended")
    interrupted: bool = Field(description="The hub stopped abruptly before the event ended")
    peak_area_ratio: float = Field(description="Largest moving region, as a fraction of the frame")
    motion_frames: int
    person: bool | None = Field(
        description="A person was seen; null when nobody could check (detection off, no model)"
    )
    person_confidence: float | None = Field(description="Best person score of the checks")
    person_at: datetime | None = Field(
        description="When the frame a person was found on was captured; null without a person"
    )
    snapshot_at: datetime | None = Field(
        description="When the snapshot's frame, the one with the most motion, was captured"
    )
    alert: bool = Field(
        description="Raised an alert (false: the camera alerts on people only and none was seen)"
    )
    boxes: list[BoundingBox]
    snapshots: list[SnapshotLink]
    clip: ClipLink | None = Field(
        description="Video of the event, from a few seconds before it was detected"
    )

    @classmethod
    def from_record(cls, record: EventRecord, signer: UrlSigner, base_path: str) -> EventOut:
        event = record.event
        links = []
        for kind, snapshot in sorted(record.snapshots.items()):
            if kind not in IMAGE_KINDS:
                continue
            resource = snapshot_resource(base_path, event.id, kind)
            expires, signature = signer.sign(resource)
            links.append(
                SnapshotLink(
                    kind=cast(ImageKind, kind.value),
                    url=f"{resource}&expires={expires}&signature={signature}",
                    size_bytes=snapshot.size_bytes,
                )
            )
        clip = None
        if stored := record.snapshots.get(SnapshotKind.CLIP):
            resource = clip_resource(base_path, event.id)
            expires, signature = signer.sign(resource)
            clip = ClipLink(
                url=f"{resource}?expires={expires}&signature={signature}",
                content_type=SnapshotKind.CLIP.content_type,
                size_bytes=stored.size_bytes,
                started_at=record.clip_started_at,
            )
        duration = (event.ended_at - event.started_at).total_seconds() if event.ended_at else None
        return cls(
            id=event.id,
            device_id=event.device_id,
            kind=event.kind,
            started_at=event.started_at,
            ended_at=event.ended_at,
            duration_seconds=duration,
            complete=record.complete,
            interrupted=record.interrupted,
            peak_area_ratio=event.peak_area_ratio,
            motion_frames=event.motion_frames,
            person=event.person,
            person_confidence=event.person_confidence,
            person_at=event.person_at,
            snapshot_at=record.snapshot_at,
            alert=event.alert,
            boxes=list(record.boxes),
            snapshots=links,
            clip=clip,
        )


def snapshot_resource(base_path: str, event_id: str, kind: SnapshotKind) -> str:
    """The signed part of a snapshot URL: path plus the kind it is bound to."""
    return f"{base_path}/events/{event_id}/snapshot?kind={kind}"


def clip_resource(base_path: str, event_id: str) -> str:
    """The signed part of a clip URL."""
    return f"{base_path}/events/{event_id}/clip"


class ActivityBucketOut(ApiSchema):
    device_id: str
    start: datetime = Field(description="The hour (UTC) these events started in")
    events: int
    people: int = Field(description="Events with a person seen")
    alerts: int = Field(description="Events that raised an alert")

    @classmethod
    def from_bucket(cls, bucket: ActivityBucket) -> ActivityBucketOut:
        return cls(
            device_id=bucket.device_id,
            start=bucket.start,
            events=bucket.events,
            people=bucket.people,
            alerts=bucket.alerts,
        )


class ActivityOut(ApiSchema):
    since: datetime
    until: datetime
    buckets: list[ActivityBucketOut] = Field(
        description="Per camera and hour, oldest first; hours without events are left out"
    )
