// Words for zone rules on people (vision/zones.py on the hub).

/** @typedef {import("../api/types.js").MotionEvent["kind"]} EventKind */
/** @typedef {Extract<EventKind, "lingering" | "crossed">} ZoneRule */

/** @type {Record<ZoneRule, { tag: string }>} */
export const ZONE_RULE = {
  lingering: { tag: "Lingering" },
  crossed: { tag: "Crossed" },
};

/**
 * The zone rule of an event, or null for anything else.
 * @param {EventKind | null | undefined} kind
 * @returns {ZoneRule | null}
 */
export function zoneRuleOf(kind) {
  return kind === "lingering" || kind === "crossed" ? kind : null;
}
