// A camera in the live grid: its live view, a link to the camera page, when it last saw motion
// and how much it saw this week.

import { href } from "../paths.js";
import { sparkline } from "./activity-chart.js";
import { liveView } from "./live-view.js";
import { h } from "./dom.js";
import { timeAgo } from "./time.js";

/** @typedef {import("../api/types.js").Device} Device */

/** Grid tiles are small: a lower frame rate saves bandwidth on phones and work on the hub. */
const GRID_FPS = 8;

/**
 * @param {Device} initial
 */
export function cameraTile(initial) {
  let device = initial;
  let motion = false;
  /** @type {string | null} */
  let lastMotionAt = null;

  const name = h("a", {
    class: "camera-link",
    attrs: { href: href(`/devices/${encodeURIComponent(device.id)}`) },
  });
  const detail = h("p", { class: "text-sm text-haze" });
  /** @type {number[] | null} events per day, today last (null until known) */
  let week = null;
  const weekTotal = h("span");
  const weekChart = h("p", { class: "camera-week", attrs: { hidden: true } }, weekTotal);
  const element = h("article", { class: "camera-tile card" });
  const view = liveView({ device, host: element, fps: GRID_FPS });
  element.append(
    view.element,
    h(
      "div",
      { class: "camera-meta" },
      h("div", { class: "min-w-0" }, h("h2", { class: "font-medium" }, name), detail),
      weekChart,
    ),
  );

  function renderWeek() {
    weekChart.hidden = !week;
    if (!week) return;
    const total = week.reduce((sum, count) => sum + count, 0);
    weekTotal.textContent = `${total} this week`;
    weekChart.replaceChildren(sparkline(week), weekTotal);
  }

  function renderDetail() {
    detail.textContent = motion
      ? "Motion now"
      : lastMotionAt
        ? `Motion ${timeAgo(lastMotionAt)}`
        : "No motion yet";
  }

  function fromDevice() {
    const last = device.last_event;
    motion = Boolean(last && !last.ended_at);
    lastMotionAt = last ? (last.ended_at ?? last.started_at) : null;
    name.textContent = device.name;
    view.setMotion(motion);
    renderDetail();
  }

  fromDevice();

  return {
    element,
    get id() {
      return device.id;
    },
    /** @param {Device} next */
    update(next) {
      device = next;
      fromDevice();
      view.update(next);
    },
    /** @param {Device["status"]} status */
    setStatus(status) {
      device = { ...device, status };
      view.setStatus(status);
    },
    /**
     * @param {boolean} active
     * @param {string} at when it started (active) or ended
     */
    setMotion(active, at) {
      motion = active;
      lastMotionAt = at;
      view.setMotion(active);
      renderDetail();
    },
    /** @param {boolean} onScreen */
    setVisible(onScreen) {
      view.setVisible(onScreen);
    },
    /** @param {number[]} counts events per day, today last */
    setWeek(counts) {
      week = [...counts];
      renderWeek();
    },
    /** One more event today. */
    countEvent() {
      if (!week) return;
      week[week.length - 1] += 1;
      renderWeek();
    },
    sync: view.sync,
    /** Refreshes "Motion 4 minutes ago". */
    tick: renderDetail,
    destroy: view.destroy,
  };
}
