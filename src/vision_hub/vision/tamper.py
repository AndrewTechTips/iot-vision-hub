"""Tamper detection: a camera covered, knocked out of focus or turned away.

Pure and cheap: a few checks a second on a 160 px grayscale copy, in the camera thread. Each
check compares the picture with what the camera normally sees, learned when it starts and
adapted slowly while nothing is wrong:

* **covered** — the picture goes flat: its contrast collapses within seconds (a hand, tape, a
  cloth, spray paint; dark or bright). A room whose only light is switched off looks the same,
  which is why cameras can turn tamper alerts off;
* **blurred** — its fine detail, relative to its contrast, falls far below normal (smeared lens,
  focus ring turned);
* **moved** — its edges no longer line up with the usual scene (the camera was turned).

Blur and movement are judged whatever the light: dimming a picture scales its detail and edges
together. A picture too dark to have any detail left is only judged for being covered. Measured
on the demo footage: no false alarm over several loops of each scene, while a hand on the lens,
a smeared lens and a camera turned over are caught.

A suspicion has to last ``hold_seconds`` before it counts, and normal has to last
``clear_seconds`` before it is over, so someone walking close past the lens or a headlight
sweep does not raise anything. The slow adaptation (minutes) follows dusk and dawn, which change
the picture gradually; tampering changes it within a second.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import cv2
import numpy as np
import numpy.typing as npt

from vision_hub.vision.frame import Frame

type Gray = npt.NDArray[np.float32]

WIDTH = 160


class TamperKind(StrEnum):
    COVERED = "covered"
    BLURRED = "blurred"
    MOVED = "moved"


@dataclass(frozen=True, slots=True)
class TamperSettings:
    checks_per_second: float = 2.0
    learn_seconds: float = 10.0  # what normal looks like, from the start
    hold_seconds: float = 8.0  # a suspicion must last this long
    clear_seconds: float = 4.0  # and normal this long to end it
    covered_contrast: float = 0.25  # contrast below this share of normal: covered
    blurred_sharpness: float = 0.3  # sharpness below this share of normal: blurred
    moved_similarity: float = 0.35  # edge similarity below this: moved
    min_detail: float = 8.0  # contrast below this: too dark to judge blur or movement
    adapt_minutes: float = 10.0  # how slowly normal follows the light
    accept_moved_minutes: float = 5.0  # a camera moved for this long: its new view is normal


@dataclass(frozen=True, slots=True)
class Measure:
    contrast: float  # standard deviation of the brightness (0-255)
    texture: float  # contrast relative to brightness: low for a uniform picture, dim or not
    sharpness: float  # fine detail (Laplacian variance) relative to contrast: low when blurred
    edges: Gray  # gradient magnitude, slightly blurred: where the scene's edges are


@dataclass(frozen=True, slots=True)
class TamperStarted:
    kind: TamperKind
    at: datetime


@dataclass(frozen=True, slots=True)
class TamperEnded:
    kind: TamperKind
    at: datetime
    accepted: bool = False  # the camera stayed moved: its new view became the normal one


type TamperChange = TamperStarted | TamperEnded


def measure(frame: Frame) -> Measure:
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height = max(1, round(gray.shape[0] * WIDTH / gray.shape[1]))
    small = cv2.resize(gray, (WIDTH, height), interpolation=cv2.INTER_AREA).astype(np.float32)
    dx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
    dy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
    edges = cv2.GaussianBlur(cv2.magnitude(dx, dy), (5, 5), 0)
    contrast = float(small.std())
    return Measure(
        contrast=contrast,
        # +16: the very darkest pictures are mostly sensor noise, not texture
        texture=contrast / (float(small.mean()) + 16),
        sharpness=float(cv2.Laplacian(small, cv2.CV_32F).var()) / (contrast * contrast + 1),
        edges=np.asarray(edges, np.float32),
    )


def similarity(a: Gray, b: Gray) -> float:
    """Pearson correlation of two edge maps: 1 for the same scene, near 0 for unrelated ones."""
    a0, b0 = a - a.mean(), b - b.mean()
    denominator = float(np.sqrt((a0 * a0).sum() * (b0 * b0).sum()))
    return float((a0 * b0).sum() / denominator) if denominator > 1e-6 else 0.0


class TamperMonitor:
    """One per camera. ``update`` with analysed frames; it returns a change when tampering
    starts or ends, else None."""

    def __init__(self, settings: TamperSettings | None = None) -> None:
        self._settings = settings or TamperSettings()
        self._interval = timedelta(seconds=1 / self._settings.checks_per_second)
        self._next_check: datetime | None = None
        self._learning: list[Measure] = []
        self._learn_until: datetime | None = None
        self._normal: Measure | None = None
        self._suspect: TamperKind | None = None
        self._suspect_since: datetime | None = None
        self._active: TamperKind | None = None
        self._active_since: datetime | None = None
        self._clean_since: datetime | None = None

    @property
    def active(self) -> TamperKind | None:
        return self._active

    def update(self, frame: Frame, at: datetime) -> TamperChange | None:
        if self._next_check is not None and at < self._next_check:
            return None
        self._next_check = at + self._interval
        current = measure(frame)
        if self._normal is None:
            self._learn(current, at)
            return None
        kind = self._classify(current)
        if self._active is None:
            return self._watch(kind, current, at)
        return self._while_tampered(kind, current, at)

    def _learn(self, current: Measure, at: datetime) -> None:
        if self._learn_until is None:
            self._learn_until = at + timedelta(seconds=self._settings.learn_seconds)
        self._learning.append(current)
        if at >= self._learn_until:
            samples = self._learning
            self._normal = Measure(
                contrast=float(np.median([m.contrast for m in samples])),
                texture=float(np.median([m.texture for m in samples])),
                sharpness=float(np.median([m.sharpness for m in samples])),
                edges=np.asarray(np.mean([m.edges for m in samples], axis=0), np.float32),
            )
            self._learning = []

    def _classify(self, current: Measure) -> TamperKind | None:
        normal = self._normal
        assert normal is not None  # noqa: S101 - only called once learned
        settings = self._settings
        if current.contrast < normal.contrast * settings.covered_contrast:
            return TamperKind.COVERED
        if current.contrast < settings.min_detail:
            return None  # too dark to tell blur or a new view from noise
        if current.sharpness < normal.sharpness * settings.blurred_sharpness:
            return TamperKind.BLURRED
        if similarity(current.edges, normal.edges) < settings.moved_similarity:
            return TamperKind.MOVED
        return None

    def _watch(
        self, kind: TamperKind | None, current: Measure, at: datetime
    ) -> TamperChange | None:
        if kind is None:
            self._suspect = self._suspect_since = None
            self._adapt(current)
            return None
        if kind is not self._suspect:
            self._suspect, self._suspect_since = kind, at
        if self._suspect_since is not None and at - self._suspect_since >= timedelta(
            seconds=self._settings.hold_seconds
        ):
            self._active, self._active_since = kind, self._suspect_since
            self._suspect = self._suspect_since = self._clean_since = None
            return TamperStarted(kind=kind, at=self._active_since)
        return None

    def _while_tampered(
        self, kind: TamperKind | None, current: Measure, at: datetime
    ) -> TamperChange | None:
        active, since = self._active, self._active_since
        assert active is not None  # noqa: S101 - set together with since
        assert since is not None  # noqa: S101
        if kind is None:
            self._clean_since = self._clean_since or at
            if at - self._clean_since >= timedelta(seconds=self._settings.clear_seconds):
                return self._end(at)
            return None
        self._clean_since = None
        if active is TamperKind.MOVED and at - since >= timedelta(
            minutes=self._settings.accept_moved_minutes
        ):
            # Turned for good: watch the new view from now on, after saying so.
            self._normal = current
            return self._end(at, accepted=True)
        return None

    def _end(self, at: datetime, *, accepted: bool = False) -> TamperEnded:
        kind = self._active
        assert kind is not None  # noqa: S101 - only called while tampered
        self._active = self._active_since = self._clean_since = None
        return TamperEnded(kind=kind, at=at, accepted=accepted)

    def _adapt(self, current: Measure) -> None:
        """Follow slow changes (the light through the day): an exponential average with a time
        constant of ``adapt_minutes``."""
        normal = self._normal
        assert normal is not None  # noqa: S101 - only called once learned
        checks = self._settings.adapt_minutes * 60 * self._settings.checks_per_second
        rate = 1 / max(checks, 1)
        self._normal = Measure(
            contrast=normal.contrast + (current.contrast - normal.contrast) * rate,
            texture=normal.texture + (current.texture - normal.texture) * rate,
            sharpness=normal.sharpness + (current.sharpness - normal.sharpness) * rate,
            edges=np.asarray(normal.edges + (current.edges - normal.edges) * rate, np.float32),
        )
