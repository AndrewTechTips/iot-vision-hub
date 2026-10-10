// Patterns in the event history, from the hub's hourly counts (GET /events/activity). Hours come
// as UTC instants and are folded into this browser's time zone here, so a Friday evening is a
// Friday evening wherever you are (half-hour zones land in the hour they start in).

import { h } from "./dom.js";

/** @typedef {import("../api/types.js").ActivityBucket} ActivityBucket */
/** @typedef {"events" | "people"} Metric */

const SVG = "http://www.w3.org/2000/svg";
/** Monday first, as most of the world reads a week. */
const WEEKDAYS = Array.from({ length: 7 }, (_, i) =>
  new Intl.DateTimeFormat("en", { weekday: "long" }).format(new Date(2024, 0, 1 + i)),
);
const hourLabel = new Intl.DateTimeFormat(undefined, { hour: "numeric" });

/**
 * Counts per local weekday (Monday = 0) and hour.
 * @param {ActivityBucket[]} buckets
 * @param {Metric} metric
 * @returns {number[][]} 7 rows of 24
 */
export function foldWeek(buckets, metric) {
  const week = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 0));
  for (const bucket of buckets) {
    const at = new Date(bucket.start);
    week[(at.getDay() + 6) % 7][at.getHours()] += bucket[metric];
  }
  return week;
}

/**
 * Counts per local day for the last `days` days, oldest first, the last one being today.
 * @param {ActivityBucket[]} buckets
 * @param {number} days
 * @param {Metric} [metric]
 * @param {Date} [now]
 */
export function foldDays(buckets, days, metric = "events", now = new Date()) {
  const today = startOfDay(now).getTime();
  const counts = Array.from({ length: days }, () => 0);
  for (const bucket of buckets) {
    const ago = Math.round((today - startOfDay(new Date(bucket.start)).getTime()) / 86_400_000);
    if (ago >= 0 && ago < days) counts[days - 1 - ago] += bucket[metric];
  }
  return counts;
}

/**
 * The busiest weekday and hour, or null for an empty week.
 * @param {number[][]} week
 */
export function busiest(week) {
  let best = null;
  for (const [day, hours] of week.entries()) {
    for (const [hour, count] of hours.entries()) {
      if (count > (best?.count ?? 0)) best = { day, hour, count };
    }
  }
  return best;
}

/**
 * Shade steps for a count: 0 for none, then 1..4 relative to the busiest cell, on a square-root
 * scale so one unusual hour doesn't wash out the everyday pattern.
 * @param {number} count
 * @param {number} max
 */
export function level(count, max) {
  if (count <= 0 || max <= 0) return 0;
  return Math.max(1, Math.ceil(Math.sqrt(count / max) * 4));
}

/**
 * A week heatmap (weekday × hour) with a choice of what to count and a sentence saying what
 * stands out. Screen readers get the sentence; the grid itself is a picture of it.
 * @param {{ id: string, weeks: number }} options id: unique on the page (names the radio group)
 */
export function weekHeatmap({ id, weeks }) {
  /** @type {Metric} */
  let metric = "events";
  /** @type {ActivityBucket[]} */
  let buckets = [];

  const choices = h(
    "div",
    { class: "segmented", attrs: { role: "radiogroup", "aria-label": "Count" } },
    /** @type {[Metric, string][]} */ ([
      ["events", "Any motion"],
      ["people", "People"],
    ]).map(([value, label]) =>
      h(
        "label",
        {},
        h("input", {
          attrs: { type: "radio", name: `${id}-metric`, value, checked: value === metric },
          on: {
            change: () => {
              metric = value;
              render();
            },
          },
        }),
        label,
      ),
    ),
  );
  const summary = h("p", { class: "text-sm text-haze" });
  const grid = h("div", { class: "heatmap", attrs: { role: "img" } });
  const element = h("div", { class: "grid gap-3" }, choices, grid, summary);

  function render() {
    const week = foldWeek(buckets, metric);
    const max = Math.max(...week.flat());
    const total = week.flat().reduce((sum, count) => sum + count, 0);
    grid.dataset.metric = metric;
    grid.replaceChildren(
      ...week.map((hours, day) =>
        h(
          "div",
          { class: "heatmap-row" },
          h("span", { class: "heatmap-day", text: WEEKDAYS[day].slice(0, 3) }),
          ...hours.map((count, hour) =>
            h("span", {
              class: "heatmap-cell",
              data: { level: String(level(count, max)) },
              attrs: { title: `${WEEKDAYS[day]}, ${hourLabel.format(at(hour))}: ${count}` },
            }),
          ),
        ),
      ),
      h(
        "div",
        { class: "heatmap-row heatmap-hours", attrs: { "aria-hidden": "true" } },
        h("span"),
        ...Array.from({ length: 24 }, (_, hour) =>
          h("span", { text: hour % 6 === 0 ? hourLabel.format(at(hour)) : "" }),
        ),
      ),
    );
    const span = weeks === 1 ? "the last week" : `the last ${weeks} weeks`;
    const what = metric === "people" ? "with people" : "";
    const peak = busiest(week);
    const text = peak
      ? `${total} ${plural(total)} ${what} in ${span}, most often on ${WEEKDAYS[peak.day]}s around ${hourLabel.format(at(peak.hour))}.`
      : `No events ${what} in ${span}.`;
    summary.textContent = text.replace(/\s+/g, " ");
    grid.setAttribute("aria-label", summary.textContent);
  }

  render();
  return {
    element,
    /** @param {ActivityBucket[]} next */
    update(next) {
      buckets = next;
      render();
    },
  };
}

/**
 * Tiny bars, one per day, today last.
 * @param {number[]} counts
 */
export function sparkline(counts) {
  const max = Math.max(1, ...counts);
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", "sparkline");
  svg.setAttribute("viewBox", `0 0 ${counts.length * 6} 16`);
  svg.setAttribute("aria-hidden", "true");
  for (const [index, count] of counts.entries()) {
    const height = count ? Math.max(2, (count / max) * 16) : 1;
    const bar = document.createElementNS(SVG, "rect");
    bar.setAttribute("x", String(index * 6));
    bar.setAttribute("y", String(16 - height));
    bar.setAttribute("width", "4");
    bar.setAttribute("height", String(height));
    bar.setAttribute("rx", "1");
    if (index === counts.length - 1) bar.setAttribute("class", "today");
    svg.append(bar);
  }
  return svg;
}

/** @param {number} count */
function plural(count) {
  return count === 1 ? "event" : "events";
}

/** @param {number} hour */
function at(hour) {
  return new Date(2024, 0, 1, hour);
}

/** @param {Date} date */
function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}
