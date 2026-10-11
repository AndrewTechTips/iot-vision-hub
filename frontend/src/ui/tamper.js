// Words for a camera that was tampered with (vision/tamper.py on the hub).

/** @typedef {import("../api/types.js").MotionEvent["kind"]} EventKind */
/** @typedef {Extract<EventKind, "covered" | "blurred" | "moved">} TamperKind */

/** @type {Record<TamperKind, { chip: string, done: string, why: string }>} */
export const TAMPER = {
  covered: { chip: "Covered", done: "covered", why: "Its picture went flat." },
  blurred: { chip: "Out of focus", done: "out of focus", why: "Its picture went blurry." },
  moved: { chip: "Moved", done: "moved", why: "It no longer sees its usual view." },
};

/**
 * The tamper kind of an event, or null for motion.
 * @param {EventKind | null | undefined} kind
 * @returns {TamperKind | null}
 */
export function tamperOf(kind) {
  return kind === "covered" || kind === "blurred" || kind === "moved" ? kind : null;
}
