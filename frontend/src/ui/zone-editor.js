// Zone rules over the live picture (vision/zones.py on the hub): areas where someone lingering
// raises an alert, lines where someone crossing does. Always drawn on the camera page, named,
// and lit up while their rule is firing. In edit mode: add an area (tap its corners, then tap the
// first one or Finish) or a line (tap its two ends), drag corners to adjust, and name each zone
// and set how long someone must stay in an area, in the list below the picture.

import { h, svg } from "./dom.js";

/** @typedef {import("../api/types.js").DetectionConfig} DetectionConfig */
/** @typedef {NonNullable<DetectionConfig["zones"]>[number]} Zone */
/** @typedef {[number, number]} Point */

const MAX_ZONES = 8;
const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * @param {{ surface: HTMLElement, zones: Zone[], onChange: (zones: Zone[]) => void }} options
 * surface: the positioned element the zones cover (the live picture).
 */
export function zoneEditor({ surface, zones: initial, onChange }) {
  /** @type {Zone[]} */
  let zones = clone(initial);
  let editing = false;
  /** @type {{ kind: "area" | "line", points: Point[] } | null} the zone being drawn */
  let draft = null;
  /** @type {{ zone: number, point: number, pointer: number } | null} */
  let dragging = null;
  /** @type {Set<string>} zones whose rule is firing now */
  const active = new Set();

  const layer = svg(
    `<svg class="zone-layer" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true"></svg>`,
  );
  const labels = h("div", { class: "zone-labels", attrs: { "aria-hidden": "true" } });
  const handles = h("div", { class: "roi-handles", attrs: { "aria-hidden": "true" } });
  surface.append(layer, labels, handles);

  const hint = h("p", { class: "hint" });
  const addArea = h("button", {
    class: "btn btn-secondary btn-sm",
    attrs: { type: "button" },
    text: "Add area",
    on: { click: () => startDraft("area") },
  });
  const addLine = h("button", {
    class: "btn btn-secondary btn-sm",
    attrs: { type: "button" },
    text: "Add line",
    on: { click: () => startDraft("line") },
  });
  const finish = h("button", {
    class: "btn btn-secondary btn-sm",
    attrs: { type: "button" },
    text: "Finish area",
    on: { click: closeDraft },
  });
  const list = h("ul", { class: "zone-list" });
  const toolbar = h(
    "div",
    { class: "roi-toolbar", attrs: { hidden: true } },
    hint,
    h("div", { class: "flex flex-wrap gap-2" }, addArea, addLine, finish),
    list,
  );

  /** @param {Zone[]} next */
  function commit(next) {
    zones = next;
    render();
    renderList();
    onChange(clone(zones));
  }

  /** @param {"area" | "line"} kind */
  function startDraft(kind) {
    if (zones.length >= MAX_ZONES) return;
    draft = { kind, points: [] };
    render();
  }

  function closeDraft() {
    if (!draft || draft.points.length < (draft.kind === "area" ? 3 : 2)) return;
    const zone = {
      name: freeName(draft.kind === "area" ? "Area" : "Line"),
      kind: draft.kind,
      points: draft.points,
      dwell_seconds: 10,
    };
    draft = null;
    commit([...zones, zone]);
    // Straight to naming it.
    /** @type {HTMLInputElement | null} */ (list.querySelector("li:last-child input"))?.select();
  }

  /** @param {string} base */
  function freeName(base) {
    const taken = new Set(zones.map((zone) => zone.name.toLowerCase()));
    let n = 1;
    while (taken.has(`${base} ${n}`.toLowerCase())) n += 1;
    return `${base} ${n}`;
  }

  /** @param {PointerEvent} event @returns {Point} */
  function toPoint(event) {
    const rect = surface.getBoundingClientRect();
    return [
      round(clamp((event.clientX - rect.left) / rect.width)),
      round(clamp((event.clientY - rect.top) / rect.height)),
    ];
  }

  function render() {
    const parts = zones.map((zone) => {
      const lit = active.has(zone.name) ? " is-active" : "";
      return zone.kind === "area"
        ? `<path class="zone-area${lit}" d="${pathOf(zone.points)}" vector-effect="non-scaling-stroke"></path>`
        : `<line class="zone-line${lit}" ${lineOf(zone.points)} vector-effect="non-scaling-stroke"></line>`;
    });
    if (draft?.points.length) {
      parts.push(
        `<polyline class="roi-draft" points="${draft.points.map(([x, y]) => `${num(x)},${num(y)}`).join(" ")}" vector-effect="non-scaling-stroke"></polyline>`,
      );
    }
    layer.replaceChildren(...svg(`<svg xmlns="${SVG_NS}">${parts.join("")}</svg>`).childNodes);

    labels.replaceChildren(
      ...zones.map((zone) => {
        const label = h("span", {
          class: `zone-label${active.has(zone.name) ? " is-active" : ""}`,
          text: zone.name,
        });
        const x = Math.min(...zone.points.map(([px]) => px));
        const y = Math.min(...zone.points.map(([, py]) => py));
        label.style.left = `${x * 100}%`;
        label.style.top = `${y * 100}%`;
        return label;
      }),
    );

    handles.replaceChildren();
    if (editing) {
      zones.forEach((zone, z) =>
        zone.points.forEach(([x, y], p) => handles.append(handle(x, y, { zone: z, point: p }))),
      );
      draft?.points.forEach(([x, y], p) =>
        handles.append(
          handle(x, y, {
            draft: true,
            first: p === 0 && draft?.kind === "area" && draft.points.length >= 3,
          }),
        ),
      );
    }

    surface.toggleAttribute("data-zone-editing", editing);
    finish.hidden = draft?.kind !== "area";
    finish.disabled = !draft || draft.points.length < 3;
    addArea.disabled = addLine.disabled = Boolean(draft) || zones.length >= MAX_ZONES;
    hint.textContent = draft
      ? draft.kind === "line"
        ? draft.points.length
          ? "Now tap the line's other end."
          : "Tap one end of the line."
        : draft.points.length < 3
          ? "Tap the area's corners: at least three."
          : "Tap the first corner, or press Finish area, to close it."
      : zones.length >= MAX_ZONES
        ? `That's the most zones a camera can have (${MAX_ZONES}).`
        : "An area alerts when someone stays in it; a line, when someone crosses it. They watch people.";
  }

  function renderList() {
    list.replaceChildren(
      ...zones.map((zone, z) => {
        const name = h("input", {
          class: "input",
          attrs: { type: "text", maxlength: "40", "aria-label": `Name of ${zone.kind} ${z + 1}` },
          on: {
            change: () => {
              const value = name.value.trim();
              const clash = zones.some(
                (other, o) => o !== z && other.name.toLowerCase() === value.toLowerCase(),
              );
              if (!value || clash) {
                name.value = zone.name; // names must be there and unique
                return;
              }
              commit(zones.map((item, i) => (i === z ? { ...item, name: value } : item)));
            },
          },
        });
        name.value = zone.name;
        const dwell = h("input", {
          class: "input w-20",
          attrs: {
            type: "number",
            min: "1",
            max: "600",
            step: "1",
            "aria-label": `Seconds someone must stay in ${zone.name}`,
          },
          on: {
            change: () => {
              const seconds = Math.min(600, Math.max(1, Math.round(Number(dwell.value) || 10)));
              commit(
                zones.map((item, i) => (i === z ? { ...item, dwell_seconds: seconds } : item)),
              );
            },
          },
        });
        dwell.value = String(zone.dwell_seconds ?? 10);
        return h(
          "li",
          { class: "zone-item" },
          h("span", { class: "badge", text: zone.kind === "area" ? "Area" : "Line" }),
          name,
          zone.kind === "area"
            ? h(
                "label",
                { class: "flex items-center gap-2 text-sm text-haze" },
                "stays",
                dwell,
                "s",
              )
            : h("span", { class: "text-sm text-haze", text: "crossed" }),
          h("button", {
            class: "btn btn-ghost btn-sm",
            attrs: { type: "button", "aria-label": `Delete ${zone.name}` },
            text: "Delete",
            on: { click: () => commit(zones.filter((_, i) => i !== z)) },
          }),
        );
      }),
    );
  }

  /**
   * @param {number} x
   * @param {number} y
   * @param {{ zone?: number, point?: number, draft?: boolean, first?: boolean }} role
   */
  function handle(x, y, role) {
    const element = h("span", {
      class: `roi-handle${role.draft ? " is-draft" : ""}${role.first ? " is-first" : ""}`,
    });
    element.style.left = `${x * 100}%`;
    element.style.top = `${y * 100}%`;
    element.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      event.preventDefault();
      if (role.first) {
        closeDraft();
        return;
      }
      if (role.zone !== undefined && role.point !== undefined) {
        dragging = { zone: role.zone, point: role.point, pointer: event.pointerId };
        surface.setPointerCapture(event.pointerId);
      }
    });
    return element;
  }

  function onPointerDown(/** @type {PointerEvent} */ event) {
    if (!editing || !draft || event.button !== 0) return;
    draft = { ...draft, points: [...draft.points, toPoint(event)] };
    if (draft.kind === "line" && draft.points.length === 2) closeDraft();
    else render();
  }
  function onPointerMove(/** @type {PointerEvent} */ event) {
    if (!dragging || event.pointerId !== dragging.pointer) return;
    const { zone, point } = dragging;
    zones = zones.map((item, z) =>
      z === zone
        ? { ...item, points: item.points.map((p, i) => (i === point ? toPoint(event) : p)) }
        : item,
    );
    render();
  }
  function onPointerUp(/** @type {PointerEvent} */ event) {
    if (!dragging || event.pointerId !== dragging.pointer) return;
    dragging = null;
    commit(zones);
  }
  function onKey(/** @type {KeyboardEvent} */ event) {
    if (!editing || !draft) return;
    if (event.key === "Escape") {
      draft = null;
      render();
    } else if (event.key === "Enter") {
      closeDraft();
    }
  }
  surface.addEventListener("pointerdown", onPointerDown);
  surface.addEventListener("pointermove", onPointerMove);
  surface.addEventListener("pointerup", onPointerUp);
  surface.addEventListener("pointercancel", onPointerUp);
  document.addEventListener("keydown", onKey);

  render();
  renderList();

  return {
    toolbar,
    get editing() {
      return editing;
    },
    /** @param {boolean} on */
    setEditing(on) {
      editing = on;
      draft = null;
      toolbar.hidden = !on;
      render();
    },
    /** Replaces the zones without reporting a change (after saving or resetting).
     * @param {Zone[]} next */
    set(next) {
      zones = clone(next);
      draft = null;
      render();
      renderList();
    },
    /**
     * Lights a zone up while its rule fires.
     * @param {string} name
     * @param {boolean} on
     */
    setActive(name, on) {
      if (on) active.add(name);
      else active.delete(name);
      render();
    },
    destroy() {
      document.removeEventListener("keydown", onKey);
      surface.removeEventListener("pointerdown", onPointerDown);
      surface.removeEventListener("pointermove", onPointerMove);
      surface.removeEventListener("pointerup", onPointerUp);
      surface.removeEventListener("pointercancel", onPointerUp);
      layer.remove();
      labels.remove();
      handles.remove();
    },
  };
}

/** @param {Zone["points"]} points */
function pathOf(points) {
  return `M${points.map(([x, y]) => `${num(x)} ${num(y)}`).join("L")}Z`;
}

/** @param {Zone["points"]} points */
function lineOf([[x1, y1], [x2, y2]]) {
  return `x1="${num(x1)}" y1="${num(y1)}" x2="${num(x2)}" y2="${num(y2)}"`;
}

/** Coordinates go into SVG markup: only ever as numbers, whatever the data says.
 * @param {unknown} value */
function num(value) {
  return String(Number(value));
}

/** @param {Zone[]} zones @returns {Zone[]} */
function clone(zones) {
  return zones.map((zone) => ({
    ...zone,
    points: zone.points.map(([x, y]) => /** @type {Point} */ ([x, y])),
  }));
}

/** @param {number} value */
function clamp(value) {
  return Math.min(1, Math.max(0, value));
}

/** @param {number} value */
function round(value) {
  return Math.round(value * 10_000) / 10_000;
}

/**
 * How the zones read in words.
 * @param {Zone[]} zones
 */
export function describeZones(zones) {
  if (zones.length === 0) return "No zone rules.";
  const areas = zones.filter((zone) => zone.kind === "area").length;
  const lines = zones.length - areas;
  const words = [
    areas && `${areas} ${areas === 1 ? "area" : "areas"}`,
    lines && `${lines} ${lines === 1 ? "line" : "lines"}`,
  ].filter(Boolean);
  return `Zone rules: ${words.join(" and ")}.`;
}
