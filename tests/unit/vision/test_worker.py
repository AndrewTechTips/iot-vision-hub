import itertools
import statistics
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import cv2
import numpy as np
import pytest

from vision_hub.core.metrics import Metrics
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import (
    CameraEvent,
    DeviceStatusChanged,
    IncidentEndedEvent,
    IncidentStartedEvent,
    MotionEndedEvent,
    MotionStartedEvent,
)
from vision_hub.domain.motion import BoundingBox, EventKind
from vision_hub.vision.bridge import FramePacket
from vision_hub.vision.clip import ClipRecorder, ClipSettings
from vision_hub.vision.config import DetectionConfig, Zone
from vision_hub.vision.demo_export import InlinePersonChecker
from vision_hub.vision.frame import Frame
from vision_hub.vision.persons import Person, PersonChecker
from vision_hub.vision.sources import Backoff, SourceError, SyntheticSource, SyntheticSourceConfig
from vision_hub.vision.worker import CameraWorker, EncodingSettings, encode_stream_frame

DETECTION = DetectionConfig(warmup_frames=3, motion_end_grace_seconds=0.5)


class SteppingClock:
    """Each call advances time by a fixed step: makes the worker deterministic."""

    def __init__(self, step: float) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.step = timedelta(seconds=step)
        self.lock = threading.Lock()

    def __call__(self) -> datetime:
        with self.lock:
            self.now += self.step
            return self.now


class RecordingSink:
    def __init__(self, *, viewers: bool = False, stop_after_ended: int | None = None) -> None:
        self.viewers = viewers
        self.frames: list[FramePacket] = []
        self.events: list[CameraEvent] = []
        self.exits: list[bool] = []
        self.done = threading.Event()
        self._stop_after_ended = stop_after_ended

    @property
    def has_viewers(self) -> bool:
        return self.viewers

    def publish_frame(self, packet: FramePacket) -> None:
        self.frames.append(packet)

    def publish_event(self, event: CameraEvent) -> None:
        self.events.append(event)
        ended = sum(isinstance(e, MotionEndedEvent) for e in self.events)
        if self._stop_after_ended is not None and ended >= self._stop_after_ended:
            self.done.set()

    def worker_exited(self, *, crashed: bool) -> None:
        self.exits.append(crashed)
        self.done.set()

    def statuses(self) -> list[DeviceStatus]:
        return [e.status for e in self.events if isinstance(e, DeviceStatusChanged)]


class JitteryClock:
    """Advances by each step in turn: a camera whose frames arrive slightly irregularly."""

    def __init__(self, steps: tuple[float, ...]) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.steps = itertools.cycle(timedelta(seconds=step) for step in steps)
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self) -> datetime:
        with self.lock:
            self.calls += 1
            self.now += next(self.steps)
            return self.now


def synthetic(**overrides: float) -> SyntheticSource:
    fields = {"fps": 10, "visit_every_seconds": 4, "visit_seconds": 1.5, "seed": 3}
    return SyntheticSource(SyntheticSourceConfig.model_validate(fields | overrides), paced=False)


def make_worker(
    sink: RecordingSink,
    *,
    source: object = None,
    step: float = 0.1,
    fps: float = 10,
    clips: ClipSettings | None = None,
    persons: PersonChecker | None = None,
    detection: DetectionConfig = DETECTION,
) -> CameraWorker:
    return CameraWorker(
        device_id="cam-1",
        source=source or synthetic(),  # type: ignore[arg-type]
        detection=detection,
        sink=sink,
        target_fps=fps,
        encoding=EncodingSettings(stream_max_width=320),
        backoff=Backoff(initial=0.001, maximum=0.001),
        clock=SteppingClock(step),
        clips=clips,
        persons=persons,
    )


def decode(jpeg: bytes) -> Frame:
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return np.asarray(image, dtype=np.uint8)


def run_until_done(worker: CameraWorker, sink: RecordingSink, timeout: float = 10) -> None:
    worker.start()
    assert sink.done.wait(timeout), "worker did not reach the expected point in time"
    assert worker.stop(timeout=5)


class TestLiveFrames:
    def test_frames_are_downscaled_to_the_stream_width(self) -> None:
        frame = np.zeros((1080, 1920, 3), np.uint8)

        jpeg, width, height = encode_stream_frame(frame, EncodingSettings())

        image = decode(jpeg)
        assert (width, height) == (960, 540) == (image.shape[1], image.shape[0])

    def test_frames_are_encoded_as_they_are(self) -> None:
        frame = np.full((480, 640, 3), 128, np.uint8)

        jpeg, width, _ = encode_stream_frame(frame, EncodingSettings())

        assert width == 640
        assert abs(int(decode(jpeg).mean()) - 128) <= 1


class TestLifecycle:
    def test_publishes_motion_events_with_jpeg_evidence(self) -> None:
        sink = RecordingSink(viewers=True, stop_after_ended=2)
        worker = make_worker(sink)

        run_until_done(worker, sink)

        started = [e for e in sink.events if isinstance(e, MotionStartedEvent)]
        ended = [e for e in sink.events if isinstance(e, MotionEndedEvent)]
        assert len(started) >= 2
        assert len(ended) >= 2
        clean, annotated = decode(ended[0].snapshot_jpeg), decode(ended[0].annotated_jpeg)
        assert clean.shape == (480, 640, 3)
        assert ended[0].boxes
        assert not np.array_equal(clean, annotated)  # boxes only on the annotated copy
        assert ended[0].event.device_id == "cam-1"

    def test_counts_frames_and_events_in_metrics(self) -> None:
        metrics = Metrics(process_metrics=False)
        sink = RecordingSink(viewers=True, stop_after_ended=1)
        worker = make_worker(sink)
        worker._metrics = metrics.camera("cam-1")

        run_until_done(worker, sink)

        def value(name: str) -> float:
            return metrics.registry.get_sample_value(name, {"device": "cam-1"}) or 0

        analysed = value("vision_hub_camera_frames_analysed_total")
        assert analysed >= 10
        assert value("vision_hub_camera_processing_seconds_count") == analysed
        assert value("vision_hub_camera_frames_streamed_total") == len(sink.frames)
        assert value("vision_hub_motion_events_total") >= 1

    def test_reports_status_through_its_life(self) -> None:
        sink = RecordingSink(stop_after_ended=1)
        worker = make_worker(sink)

        run_until_done(worker, sink)

        statuses = sink.statuses()
        assert statuses[:2] == [DeviceStatus.STARTING, DeviceStatus.ONLINE]
        assert statuses[-1] is DeviceStatus.STOPPED
        assert sink.exits == [False]
        assert worker.is_alive is False

    def test_live_frames_are_downscaled_jpegs_in_sequence(self) -> None:
        sink = RecordingSink(viewers=True, stop_after_ended=1)

        run_until_done(make_worker(sink), sink)

        sequences = [f.sequence for f in sink.frames]
        assert sequences == list(range(1, len(sequences) + 1))
        assert decode(sink.frames[0].jpeg).shape == (240, 320, 3)
        assert (sink.frames[0].width, sink.frames[0].height) == (320, 240)
        assert any(f.motion for f in sink.frames)

    def test_live_frames_carry_their_motion_boxes_in_stream_pixels(self) -> None:
        sink = RecordingSink(viewers=True, stop_after_ended=1)

        run_until_done(make_worker(sink), sink)

        moving = [f for f in sink.frames if f.motion]
        assert moving
        assert all(f.boxes for f in moving)
        assert not any(f.boxes for f in sink.frames if not f.motion)
        boxes = [box for f in moving for box in f.boxes]  # 640x480 source, 320 px stream
        assert min(min(box.x, box.y) for box in boxes) >= 0
        assert max(box.x + box.width for box in boxes) <= 320
        assert max(box.y + box.height for box in boxes) <= 240
        assert max(box.height for box in boxes) > 60  # the figure, halved

    def test_without_viewers_frames_are_encoded_about_once_a_second(self) -> None:
        sink = RecordingSink(viewers=False, stop_after_ended=2)

        run_until_done(make_worker(sink), sink)

        processed_seconds = (
            sink.frames[-1].captured_at - sink.frames[0].captured_at
        ).total_seconds()
        assert len(sink.frames) <= processed_seconds + 2  # ~1 per simulated second, not 10

    def test_frame_rate_is_limited_to_the_target(self) -> None:
        """Camera delivers 50 fps (0.02 s per frame); only 10 fps are analysed."""
        sink = RecordingSink(viewers=True, stop_after_ended=1)

        run_until_done(make_worker(sink, step=0.02, fps=10), sink)

        gaps = [
            (b.captured_at - a.captured_at).total_seconds()
            for a, b in itertools.pairwise(sink.frames)
        ]
        assert min(gaps) >= 0.075  # a quarter interval of tolerance
        assert statistics.mean(gaps) == pytest.approx(0.1, abs=0.01)

    def test_a_camera_at_the_target_rate_loses_no_frames_to_jitter(self) -> None:
        """Frames 99 and 101 ms apart at a 10 fps target: every one is analysed."""
        sink = RecordingSink(viewers=True, stop_after_ended=1)
        worker = make_worker(sink, fps=10)
        jittery = JitteryClock((0.099, 0.101))
        worker._clock = jittery

        run_until_done(worker, sink)

        assert len(sink.frames) >= jittery.calls * 0.9

    def test_stopping_mid_event_still_delivers_it(self) -> None:
        sink = RecordingSink()
        worker = make_worker(sink, source=synthetic(visit_every_seconds=40, visit_seconds=30))
        worker.start()
        deadline = threading.Event()
        for _ in range(200):
            if any(isinstance(e, MotionStartedEvent) for e in sink.events):
                break
            deadline.wait(0.02)

        assert worker.stop(timeout=5)

        assert any(isinstance(e, MotionEndedEvent) for e in sink.events)

    def test_cannot_start_twice(self) -> None:
        sink = RecordingSink(stop_after_ended=1)
        worker = make_worker(sink)
        worker.start()
        try:
            with pytest.raises(RuntimeError, match="already started"):
                worker.start()
        finally:
            worker.stop()

    def test_stop_before_start_is_a_no_op(self) -> None:
        assert make_worker(RecordingSink()).stop() is True


class TestClips:
    def test_each_event_carries_a_clip_starting_before_the_detection(self) -> None:
        sink = RecordingSink(stop_after_ended=2)
        worker = make_worker(sink, clips=ClipSettings(pre_roll_seconds=1, width=320))

        run_until_done(worker, sink)

        ended = [e for e in sink.events if isinstance(e, MotionEndedEvent)]
        for event in ended[:2]:
            assert event.clip is not None
            assert event.clip.content_type == "video/webm"
            assert event.clip.width == 320
            motion = event.event
            assert motion.ended_at is not None
            lasted = (motion.ended_at - motion.started_at).total_seconds()
            # The pre-roll, the motion, then the quiet period that closed the event.
            expected = 1 + lasted + DETECTION.motion_end_grace_seconds
            assert event.clip.duration_seconds == pytest.approx(expected, abs=0.3)
            # Its first frame is the pre-roll's: a player can place the detection on it.
            assert event.clip.started_at is not None
            lead = (motion.started_at - event.clip.started_at).total_seconds()
            assert lead == pytest.approx(1, abs=0.15)
            # The snapshot's frame lies within the event.
            assert event.snapshot_at is not None
            assert motion.started_at <= event.snapshot_at <= motion.ended_at

    def test_without_clip_settings_events_have_none(self) -> None:
        sink = RecordingSink(stop_after_ended=1)

        run_until_done(make_worker(sink), sink)

        assert all(e.clip is None for e in sink.events if isinstance(e, MotionEndedEvent))

    def test_a_failing_recorder_costs_the_clip_not_the_alert(
        self, monkeypatch: pytest.MonkeyPatch, log_records: Callable[[], list[dict[str, Any]]]
    ) -> None:
        def broken(self: ClipRecorder, *_: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ClipRecorder, "add", broken)
        sink = RecordingSink(stop_after_ended=2)

        run_until_done(make_worker(sink, clips=ClipSettings()), sink)

        ended = [e for e in sink.events if isinstance(e, MotionEndedEvent)]
        assert len(ended) >= 2
        assert all(e.clip is None and e.annotated_jpeg for e in ended)
        assert any(r["event"] == "clip_failed" for r in log_records())

    def test_a_clip_that_cannot_be_finished_is_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(self: ClipRecorder) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ClipRecorder, "finish", broken)
        sink = RecordingSink(stop_after_ended=1)

        run_until_done(make_worker(sink, clips=ClipSettings()), sink)

        ended = next(e for e in sink.events if isinstance(e, MotionEndedEvent))
        assert ended.clip is None
        assert ended.annotated_jpeg

    def test_stopping_mid_event_keeps_the_clip(self) -> None:
        sink = RecordingSink()
        worker = make_worker(
            sink,
            source=synthetic(visit_every_seconds=40, visit_seconds=30),
            clips=ClipSettings(width=320),
        )
        worker.start()
        deadline = threading.Event()
        for _ in range(200):
            if any(isinstance(e, MotionStartedEvent) for e in sink.events):
                break
            deadline.wait(0.02)

        assert worker.stop(timeout=5)

        [ended] = [e for e in sink.events if isinstance(e, MotionEndedEvent)]
        assert ended.clip is not None


class ScriptedDetector:
    """Answers each check with the next score (0 means nobody); records what it was shown."""

    def __init__(self, *scores: float, fail: bool = False) -> None:
        self.scores = list(scores)
        self.fail = fail
        self.calls = 0

    def detect(self, frame: Frame) -> list[Person]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("model crashed")
        score = self.scores.pop(0) if self.scores else 0.0
        box = BoundingBox(x=0, y=0, width=10, height=20)
        return [Person(box=box, confidence=score)] if score else []


PEOPLE_ONLY = DETECTION.model_copy(update={"alert_on": "person"})


def first_ended(
    detector: ScriptedDetector | None, *, detection: DetectionConfig = PEOPLE_ONLY
) -> MotionEndedEvent:
    sink = RecordingSink(stop_after_ended=1)
    checker = (
        PersonChecker(detector, threshold=0.5, check_interval_seconds=1, max_checks=5)
        if detector
        else None
    )
    run_until_done(make_worker(sink, persons=checker, detection=detection), sink)
    if checker:
        checker.close()
    return next(e for e in sink.events if isinstance(e, MotionEndedEvent))


class TestPersons:
    def test_a_person_makes_the_event_alert_and_stops_the_checks(self) -> None:
        detector = ScriptedDetector(0.2, 0.9)

        ended = first_ended(detector)

        assert ended.event.person is True
        assert ended.event.person_confidence == 0.9
        assert ended.event.alert is True
        # Found on the second check; tracking may look again afterwards (for objects nobody
        # has looked at, or to confirm a still person), at most once per interval.
        assert 2 <= detector.calls <= 4
        # Found on the second check, one interval (1 s) after the event started.
        assert ended.event.person_at is not None
        found_after = (ended.event.person_at - ended.event.started_at).total_seconds()
        assert found_after == pytest.approx(1, abs=0.15)

    def test_motion_without_a_person_stays_quiet_on_a_people_only_camera(self) -> None:
        detector = ScriptedDetector(0.3)

        ended = first_ended(detector)

        assert ended.event.person is False
        assert ended.event.person_confidence == 0.3
        assert ended.event.alert is False
        # About one check a second during the ~1.5 s event, plus the last look at the frame
        # with the most motion.
        assert 2 <= detector.calls <= 4

    def test_any_motion_cameras_alert_whatever_was_seen(self) -> None:
        ended = first_ended(ScriptedDetector(), detection=DETECTION)

        assert ended.event.person is False
        assert ended.event.alert is True

    def test_a_failing_model_alerts_anyway_and_stops_trying(
        self, log_records: Callable[[], list[dict[str, Any]]]
    ) -> None:
        detector = ScriptedDetector(fail=True)

        ended = first_ended(detector)

        assert ended.event.person is None
        assert ended.event.person_confidence is None
        assert ended.event.alert is True  # nobody could check: fail open
        assert detector.calls == 1
        assert sum(r["event"] == "person_check_failed" for r in log_records()) == 1

    def test_without_person_detection_people_only_cameras_alert(self) -> None:
        ended = first_ended(None)

        assert (ended.event.person, ended.event.alert) == (None, True)

    def test_live_frames_show_the_person_once_found(self) -> None:
        # Paced: the event lasts about a second of real time, so the check (on its own thread)
        # finishes while the event is still open.
        source = SyntheticSource(
            SyntheticSourceConfig(fps=50, visit_every_seconds=2, visit_seconds=1, seed=3)
        )
        checker = PersonChecker(
            ScriptedDetector(0.9), threshold=0.5, check_interval_seconds=1, max_checks=5
        )
        sink = RecordingSink(viewers=True, stop_after_ended=1)

        run_until_done(make_worker(sink, source=source, persons=checker), sink)
        checker.close()

        scores = [f.person for f in sink.frames]
        assert 0.9 in scores
        first = scores.index(0.9)
        assert all(score is None for score in scores[: first - 50])  # before the event
        assert scores[-1] is None  # the event ended


class ExplodingSource:
    name, fps, resolution = "exploding", 10.0, (4, 4)

    def __init__(self, error: Exception) -> None:
        self.error = error

    def open(self) -> None:
        pass

    def read(self) -> Frame:
        raise self.error

    def close(self) -> None:
        pass


class TestFailures:
    def test_stopping_while_reconnecting_exits_promptly(self) -> None:
        sink = RecordingSink()
        worker = CameraWorker(
            device_id="cam-1",
            source=ExplodingSource(SourceError("camera unplugged")),
            detection=DETECTION,
            sink=sink,
            target_fps=10,
            backoff=Backoff(initial=30, maximum=30),  # stuck in a long backoff wait
        )
        worker.start()
        for _ in range(100):
            if DeviceStatus.RECONNECTING in sink.statuses():
                break
            threading.Event().wait(0.01)

        assert worker.stop(timeout=2)
        assert sink.statuses()[-1] is DeviceStatus.STOPPED
        assert sink.exits == [False]

    def test_cleanup_errors_do_not_prevent_shutdown(self) -> None:
        sink = RecordingSink(stop_after_ended=1)
        worker = make_worker(sink)

        def broken_close() -> None:
            raise RuntimeError("tracker bug")

        worker._tracker.close = broken_close  # type: ignore[method-assign]

        run_until_done(worker, sink)

        assert sink.statuses()[-1] is DeviceStatus.STOPPED

    def test_unexpected_errors_crash_the_worker_and_report_failed(self) -> None:
        sink = RecordingSink()

        run_until_done(make_worker(sink, source=ExplodingSource(ZeroDivisionError("bug"))), sink)

        assert sink.statuses()[-1] is DeviceStatus.FAILED
        assert sink.exits == [True]

    def test_a_source_that_gives_up_counts_as_a_crash(self) -> None:
        class GivingUp(ExplodingSource):
            def open(self) -> None:
                raise SourceError("gone")

        sink = RecordingSink()
        worker = make_worker(sink, source=GivingUp(SourceError("gone")))
        worker._source._max_attempts = 1

        run_until_done(worker, sink)

        assert sink.statuses()[-1] is DeviceStatus.FAILED
        assert sink.exits == [True]


class CoveredSource:
    """The synthetic room, covered by a hand between two read counts."""

    def __init__(self, covered: range) -> None:
        self.inner = synthetic(visit_every_seconds=60, visit_seconds=1)
        self.covered = covered
        self.reads = 0
        self.name, self.fps, self.resolution = "covered", 10.0, (640, 480)

    def open(self) -> None:
        self.inner.open()

    def read(self) -> Frame:
        frame = self.inner.read()
        self.reads += 1
        return np.full_like(frame, 12) if self.reads in self.covered else frame

    def close(self) -> None:
        self.inner.close()


class TamperSink(RecordingSink):
    def publish_event(self, event: CameraEvent) -> None:
        super().publish_event(event)
        if isinstance(event, IncidentEndedEvent):
            self.done.set()


class TestTampering:
    def test_a_covered_camera_raises_a_tamper_event_and_its_end(self) -> None:
        sink = TamperSink()
        worker = make_worker(sink, source=CoveredSource(range(150, 300)))

        run_until_done(worker, sink, timeout=20)

        [tampered] = [e for e in sink.events if isinstance(e, IncidentStartedEvent)]
        [restored] = [e for e in sink.events if isinstance(e, IncidentEndedEvent)]
        assert tampered.event.kind is EventKind.COVERED
        assert tampered.event.alert is True
        assert decode(tampered.snapshot_jpeg).max() < 40  # the picture as it is: dark
        assert tampered.thumbnail_jpeg
        assert restored.event.id == tampered.event.id
        assert restored.event.ended_at is not None
        covered_for = (restored.event.ended_at - tampered.event.started_at).total_seconds()
        assert 15 <= covered_for <= 20  # covered 15 s, then normal for a few seconds

    def test_cameras_can_turn_tamper_alerts_off(self) -> None:
        sink = RecordingSink(stop_after_ended=99)
        detection = DETECTION.model_copy(update={"tamper_alerts": False})
        worker = make_worker(sink, source=CoveredSource(range(150, 400)), detection=detection)
        worker.start()
        deadline = threading.Event()
        deadline.wait(3)
        assert worker.stop(timeout=5)

        assert not any(isinstance(e, IncidentStartedEvent) for e in sink.events)

    def test_stopping_a_tampered_camera_ends_its_tamper_event(self) -> None:
        sink = TamperSink()
        # Covered from the 15th second on, and never uncovered: the stop ends it.
        source = CoveredSource(range(150, 10_000))
        worker = make_worker(sink, source=source)
        worker.start()
        while not any(isinstance(e, IncidentStartedEvent) for e in sink.events):
            threading.Event().wait(0.05)
        assert worker.stop(timeout=5)

        assert [type(e) for e in sink.events if isinstance(e, IncidentEndedEvent)] == [
            IncidentEndedEvent
        ]


class StageSource:
    """A grey room and one bright figure, placed by ``where(read count) -> x or None``."""

    name, fps, resolution = "stage", 10.0, (640, 480)

    def __init__(self, where: Callable[[int], int | None]) -> None:
        self.where = where
        self.reads = 0
        self.rng = np.random.default_rng(5)

    def open(self) -> None:
        pass

    def read(self) -> Frame:
        frame = np.clip(90 + self.rng.normal(0, 3, (480, 640, 3)), 0, 255).astype(np.uint8)
        self.reads += 1
        if (x := self.where(self.reads)) is not None:
            cv2.rectangle(frame, (x - 25, 200), (x + 25, 400), (230, 230, 230), -1)
        return frame

    def close(self) -> None:
        pass


class BrightPerson:
    """A person detector that sees the bright figure (where it is, as the model would)."""

    def detect(self, frame: Frame) -> list[Person]:
        ys, xs = np.nonzero(frame[:, :, 0] > 200)
        if len(xs) < 50:
            return []
        box = BoundingBox(int(xs.min()), int(ys.min()), int(np.ptp(xs)) + 1, int(np.ptp(ys)) + 1)
        return [Person(box=box, confidence=0.9)]


class ZoneSink(RecordingSink):
    def __init__(self, ends_needed: int) -> None:
        super().__init__()
        self.ends_needed = ends_needed

    def publish_event(self, event: CameraEvent) -> None:
        super().publish_event(event)
        ended = [e for e in self.events if isinstance(e, IncidentEndedEvent)]
        if len(ended) >= self.ends_needed:
            self.done.set()


def run_stage(where: Callable[[int], int | None], zones: tuple[Zone, ...]) -> ZoneSink:
    sink = ZoneSink(ends_needed=1)
    checker = InlinePersonChecker(BrightPerson(), threshold=0.5, check_interval_seconds=1)
    detection = DETECTION.model_copy(update={"zones": zones, "tamper_alerts": False})
    worker = make_worker(sink, source=StageSource(where), persons=checker, detection=detection)
    run_until_done(worker, sink, timeout=20)
    checker.close()
    return sink


class TestZones:
    def test_crossing_a_line_is_an_incident_with_its_picture(self) -> None:
        gate = Zone(name="Gate", kind="line", points=((0.5, 0.2), (0.5, 1.0)))
        # Walks in from the left after 2 s, 8 px a frame, across the middle.
        sink = run_stage(lambda n: 60 + (n - 20) * 8 if 20 <= n <= 80 else None, (gate,))

        [started] = [e for e in sink.events if isinstance(e, IncidentStartedEvent)]
        [ended] = [e for e in sink.events if isinstance(e, IncidentEndedEvent)]
        assert (started.event.kind, started.event.zone) == (EventKind.CROSSED, "Gate")
        assert started.event.person is True
        assert ended.event.ended_at == started.event.started_at  # a moment, not a stay
        assert started.annotated_jpeg != started.snapshot_jpeg  # the zone and person drawn
        assert sink.frames[-1].tracks or any(f.tracks for f in sink.frames)

    def test_someone_staying_in_an_area_is_lingering_until_they_leave(self) -> None:
        doorstep = Zone(
            name="Doorstep",
            kind="area",
            points=((0.6, 0.5), (1.0, 0.5), (1.0, 1.0), (0.6, 1.0)),
            dwell_seconds=4,
        )

        def where(n: int) -> int | None:
            if n < 20:
                return None
            if n < 40:
                return 100 + (n - 20) * 22  # walks up to x 540 in 2 s
            if n < 140:
                return 540  # and stands there for 10 s, without moving
            return 540 - (n - 140) * 25 if n < 160 else None  # then walks off

        sink = run_stage(where, (doorstep,))

        [started] = [e for e in sink.events if isinstance(e, IncidentStartedEvent)]
        [ended] = [e for e in sink.events if isinstance(e, IncidentEndedEvent)]
        assert (started.event.kind, started.event.zone) == (EventKind.LINGERING, "Doorstep")
        assert ended.event.ended_at is not None
        stayed = (ended.event.ended_at - started.event.started_at).total_seconds()
        # From arriving (3.3 s) to leaving (14.7 s), though standing still, plus a few seconds:
        # the spot they stood on reads as motion until the background catches up.
        assert 11 <= stayed <= 17
        # While standing still, tracking kept them as a person (checks confirmed it).
        assert any(t.person and t.still for f in sink.frames for t in f.tracks)
