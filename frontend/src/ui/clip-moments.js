// Where an event's moments fall in its clip, for the viewer's "Jump to" buttons.

/** @typedef {import("../api/types.js").MotionEvent} MotionEvent */

/**
 * @typedef {{ kind: "motion" | "person" | "peak", label: string, seconds: number }} Moment
 */

/**
 * Where an event's moments fall in its clip, in seconds from the clip's start, in order: the
 * motion detected, the person found, the frame with the most motion (the snapshot). Nothing for
 * clips recorded before the hub kept these moments.
 * @param {Pick<MotionEvent, "started_at" | "person_at" | "snapshot_at" | "clip">} event
 * @returns {Moment[]}
 */
export function clipMoments(event) {
  const start = event.clip?.started_at ? Date.parse(event.clip.started_at) : NaN;
  if (Number.isNaN(start)) return [];
  /** @type {[Moment["kind"], string, string | null | undefined][]} */
  const marks = [
    ["motion", "Motion", event.started_at],
    ["person", "Person", event.person_at],
    ["peak", "Most motion", event.snapshot_at],
  ];
  return marks
    .filter(([, , at]) => at)
    .map(([kind, label, at]) => ({
      kind,
      label,
      seconds: (Date.parse(/** @type {string} */ (at)) - start) / 1000,
    }))
    .filter(({ seconds }) => seconds >= 0)
    .sort((a, b) => a.seconds - b.seconds);
}

/** "0:04", like a video player shows time. @param {number} seconds */
export function clipTime(seconds) {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
