from datetime import UTC, datetime, timedelta

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.persons import Person
from vision_hub.vision.tracking import Tracker, TrackView, merge

T0 = datetime(2026, 10, 11, 12, tzinfo=UTC)
WIDTH = 640


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def walker(x: int) -> list[BoundingBox]:
    """A walking person's motion: legs and head move apart, a few pixels from each other."""
    return [BoundingBox(x, 200, 30, 80), BoundingBox(x + 5, 172, 20, 25)]


def walk(
    tracker: Tracker, start: float, frames: int, x0: int = 100, step: int = 8
) -> list[TrackView]:
    views: list[TrackView] = []
    for i in range(frames):
        views = tracker.update(walker(x0 + i * step), at(start + i / 10))
    return views


def person(box: BoundingBox, score: float = 0.9) -> Person:
    return Person(box=box, confidence=score)


BODY = BoundingBox(125, 100, 50, 190)


class TestObjects:
    def test_pieces_of_one_mover_are_one_object(self) -> None:
        assert len(merge(walker(100), margin=19)) == 1
        assert len(merge([BoundingBox(0, 0, 10, 10), BoundingBox(300, 0, 10, 10)], 19)) == 2

    def test_a_walker_keeps_its_id_across_frames(self) -> None:
        tracker = Tracker(WIDTH)

        views = walk(tracker, 0, 20)

        [view] = views
        assert view.id == 1
        assert view.box.x > 200  # it moved along
        assert view.person is None

    def test_two_movers_get_two_tracks(self) -> None:
        tracker = Tracker(WIDTH)
        views: list[TrackView] = []
        for i in range(5):
            views = tracker.update([*walker(50 + i * 5), *walker(450 - i * 5)], at(i / 10))

        assert sorted(v.id for v in views) == [1, 2]

    def test_a_blip_is_not_shown_and_a_lost_object_ends(self) -> None:
        tracker = Tracker(WIDTH)

        blip = tracker.update(walker(100), at(0))
        walk(tracker, 0.1, 3, x0=100)
        brief_gap = tracker.update([], at(0.8))  # under a second without motion: still there
        long_gap = tracker.update([], at(2))

        assert blip == []
        assert [v.id for v in brief_gap] == [1]
        assert long_gap == []


class TestPeople:
    def test_a_person_check_labels_the_track_and_gives_it_a_whole_body(self) -> None:
        tracker = Tracker(WIDTH)
        walk(tracker, 0, 5, x0=100)  # motion around x 132..162 at t=0.4

        tracker.people_seen([person(BODY, 0.88)], at(0.4))
        [view] = walk(tracker, 0.5, 5, x0=140)  # 40 px further on

        assert view.person == 0.88
        assert (view.box.width, view.box.height) == (50, 190)  # the body, not the legs
        assert view.box.x > BODY.x  # moved along with the motion
        assert view.feet[1] == view.box.y + 190

    def test_a_late_check_is_matched_where_the_track_was_then(self) -> None:
        tracker = Tracker(WIDTH)
        walk(tracker, 0, 20, x0=100, step=10)  # at t=0.5 the walker was around x 150

        tracker.people_seen([person(BoundingBox(140, 100, 50, 190))], at(0.5))

        [view] = tracker.views(at(1.9))
        assert view.person == 0.9

    def test_someone_standing_still_is_held_and_looked_at_again(self) -> None:
        tracker = Tracker(WIDTH)
        walk(tracker, 0, 5)
        tracker.people_seen([person(BODY)], at(0.4))

        # They stop: no more motion.
        still = tracker.update([], at(2.0))
        due = tracker.needs_check(at(2.5))
        tracker.people_seen([person(BODY, 0.85)], at(2.5))  # still there
        later = tracker.update([], at(5.0))

        assert [(v.id, v.still) for v in still] == [(1, True)]
        assert due
        assert not tracker.needs_check(at(3.0))
        assert [v.id for v in later] == [1]

    def test_a_still_person_who_is_gone_ends(self) -> None:
        tracker = Tracker(WIDTH)
        walk(tracker, 0, 5)
        tracker.people_seen([person(BODY)], at(0.4))
        tracker.update([], at(2.0))

        tracker.people_seen([], at(2.5))  # looked again: nobody

        assert tracker.views(at(2.6)) == []

    def test_unconfirmed_still_people_are_let_go(self) -> None:
        tracker = Tracker(WIDTH)
        walk(tracker, 0, 5)
        tracker.people_seen([person(BODY)], at(0.4))

        assert tracker.update([], at(4.0)) != []  # held
        assert tracker.update([], at(5.0)) == []  # nobody looked again: gone

    def test_someone_there_since_the_start_becomes_a_track(self) -> None:
        tracker = Tracker(WIDTH)
        tracker.update([], at(0))

        tracker.people_seen([person(BoundingBox(300, 100, 50, 190), 0.8)], at(1))
        [view] = tracker.views(at(1))

        assert view.person == 0.8
        assert view.box == BoundingBox(300, 100, 50, 190)
