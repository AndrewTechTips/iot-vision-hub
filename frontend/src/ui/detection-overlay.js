// Boxes over a live picture, drawn from the detections that come with each frame (AD-23), so
// they match the picture exactly and the video itself stays clean. Tracked objects are drawn
// when the hub sends them: people in red, with their whole body and score, other motion in
// amber; otherwise the raw motion boxes. The layer covers exactly the
// picture (the view takes the frame's aspect ratio), so fractions of the frame become
// percentages. Boxes jump with the picture; only their fading out is animated (AD-20).

import { boxesShown, onBoxesChange } from "./boxes.js";
import { h } from "./dom.js";

/** @typedef {import("../realtime/mjpeg.js").Detections} Detections */

/** More boxes than this is noise (rain, a lighting flicker): the largest are kept. */
const MAX_BOXES = 24;
/** A box starting this close to the top (share of the picture) has no room for a label above. */
const TOP_ROOM = 0.06;

export function detectionOverlay() {
  const element = h("div", {
    class: "detection-layer",
    attrs: { "aria-hidden": "true", "data-idle": "" },
  });
  /** @type {HTMLElement[]} */
  const pool = [];

  function apply(/** @type {boolean} */ shown) {
    element.hidden = !shown;
  }
  apply(boxesShown());
  const unsubscribe = onBoxesChange(apply);

  /** Fades the boxes out where they are; the next detections bring them back. */
  function idle() {
    element.toggleAttribute("data-idle", true);
  }

  return {
    element,
    /** @param {Detections | null} detections what was detected on the frame just painted */
    render(detections) {
      const boxes = layout(detections);
      if (boxes.length === 0) {
        idle();
        return;
      }
      element.toggleAttribute("data-idle", false);
      // Without tracks, the event's verdict colours every box (older hubs).
      const tracked = (detections?.tracks.length ?? 0) > 0;
      element.toggleAttribute("data-person", !tracked && (detections?.person ?? null) !== null);
      while (pool.length < boxes.length) {
        const box = h("div", { class: "detection-box" }, h("span", { class: "detection-label" }));
        pool.push(box);
        element.append(box);
      }
      pool.forEach((box, i) => {
        const item = boxes[i];
        box.hidden = !item;
        if (!item) return;
        Object.assign(box.style, item.place);
        box.toggleAttribute("data-person", item.person !== null);
        box.toggleAttribute("data-top", item.top);
        const label = /** @type {HTMLElement} */ (box.firstElementChild);
        label.textContent = item.person === null ? "" : `Person ${Math.round(item.person * 100)} %`;
      });
    },
    idle,
    destroy: unsubscribe,
  };
}

/**
 * @typedef {{ left: string, top: string, width: string, height: string }} Place
 * @typedef {{ place: Place, person: number | null, top: boolean }} Item
 * `top`: the box starts at the top of the picture, so its label goes inside it.
 */

/**
 * What to draw and where, as CSS percentages of the picture: the tracked objects if the hub
 * sent any, else the motion boxes; the largest first, at most MAX_BOXES, clipped to the picture.
 * @param {Detections | null} detections
 * @returns {Item[]}
 */
export function layout(detections) {
  /** @type {[number, number, number, number, number | null][]} */
  const items = detections?.tracks.length
    ? detections.tracks.map(([x, y, w, hh, , person]) => [x, y, w, hh, person])
    : (detections?.boxes ?? []).map(([x, y, w, hh]) => [x, y, w, hh, null]);
  return items
    .sort((a, b) => b[2] * b[3] - a[2] * a[3])
    .slice(0, MAX_BOXES)
    .map(([x, y, width, height, person]) => {
      const left = clamp(x);
      const top = clamp(y);
      return {
        place: {
          left: percent(left),
          top: percent(top),
          width: percent(clamp(x + width) - left),
          height: percent(clamp(y + height) - top),
        },
        person,
        top: top < TOP_ROOM,
      };
    });
}

/** @param {number} fraction */
const clamp = (fraction) => Math.min(Math.max(fraction, 0), 1);

/** @param {number} fraction */
const percent = (fraction) => `${(fraction * 100).toFixed(2)}%`;
