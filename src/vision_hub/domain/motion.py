"""Motion detection results and events. Pure data: no OpenCV or numpy types."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned box in pixel coordinates of the original (full-resolution) frame."""

    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height

    def scaled(self, factor: float) -> BoundingBox:
        """The same box on an image resized by ``factor`` (e.g. 0.5 for half the width)."""
        return BoundingBox(
            x=round(self.x * factor),
            y=round(self.y * factor),
            width=max(1, round(self.width * factor)),
            height=max(1, round(self.height * factor)),
        )


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Outcome of analysing one frame against the background model."""

    motion: bool
    boxes: tuple[BoundingBox, ...] = ()
    largest_area_ratio: float = 0.0  # biggest moving region, as a fraction of the frame
    changed_ratio: float = 0.0  # fraction of (ROI) pixels that differ from the background
    lighting_change: bool = False  # whole-scene change; background was reset instead
    warming_up: bool = False  # background still settling after start or reset


NO_MOTION = DetectionResult(motion=False)


class EventKind(StrEnum):
    """What an event is: motion, or the camera being tampered with (vision/tamper.py)."""

    MOTION = "motion"
    # The camera tampered with (vision/tamper.py)
    COVERED = "covered"
    BLURRED = "blurred"
    MOVED = "moved"
    # Zone rules on people (vision/zones.py)
    LINGERING = "lingering"
    CROSSED = "crossed"

    @property
    def tamper(self) -> bool:
        return self in {EventKind.COVERED, EventKind.BLURRED, EventKind.MOVED}


@dataclass(frozen=True, slots=True)
class MotionEvent:
    """One continuous period of motion on one device, or of the camera being tampered with
    (``kind``): both are events in the history, with a snapshot and an alert."""

    id: str
    device_id: str
    started_at: datetime
    ended_at: datetime | None = None
    peak_area_ratio: float = 0.0
    motion_frames: int = 0
    # Person detection: whether a person was seen (None when nobody could check: detection
    # off, no model, or the event never finished) and the highest score of the checks.
    person: bool | None = None
    person_confidence: float | None = None
    person_at: datetime | None = None  # when the frame the person was found on was captured
    alert: bool = True  # False when the camera alerts on people only and none was seen
    kind: EventKind = EventKind.MOTION
    zone: str | None = None  # the zone a zone rule fired in
