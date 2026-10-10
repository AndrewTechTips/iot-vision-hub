import { describe, expect, it } from "vitest";

import { busiest, foldDays, foldWeek, level } from "./activity-chart.js";

/**
 * A bucket for a local time: the hub sends UTC instants, the dashboard folds them back.
 * @param {Date} local
 * @param {number} events
 * @param {number} [people]
 */
function bucket(local, events, people = 0) {
  return { device_id: "porch", start: local.toISOString(), events, people, alerts: people };
}

// Friday 9 October 2026.
const FRIDAY_6PM = new Date(2026, 9, 9, 18);

describe("foldWeek", () => {
  it("counts per local weekday (Monday first) and hour", () => {
    const week = foldWeek(
      [
        bucket(FRIDAY_6PM, 3, 1),
        bucket(new Date(2026, 9, 2, 18), 2, 2),
        bucket(new Date(2026, 9, 5, 0), 1),
      ],
      "events",
    );

    expect(week).toHaveLength(7);
    expect(week[4][18]).toBe(5); // two Fridays at 6 PM
    expect(week[0][0]).toBe(1); // Monday at midnight
    expect(week.flat().reduce((a, b) => a + b, 0)).toBe(6);
  });

  it("can count people instead", () => {
    expect(foldWeek([bucket(FRIDAY_6PM, 3, 1)], "people")[4][18]).toBe(1);
  });
});

describe("foldDays", () => {
  it("lines up the last days, today last", () => {
    const now = new Date(2026, 9, 10, 9);
    const counts = foldDays(
      [
        bucket(new Date(2026, 9, 10, 8), 2), // today
        bucket(new Date(2026, 9, 9, 23), 3), // yesterday, late
        bucket(new Date(2026, 9, 4, 12), 1), // six days ago
        bucket(new Date(2026, 9, 3, 12), 9), // too old
      ],
      7,
      "events",
      now,
    );

    expect(counts).toEqual([1, 0, 0, 0, 0, 3, 2]);
  });
});

describe("busiest and level", () => {
  it("finds the busiest slot", () => {
    const week = foldWeek([bucket(FRIDAY_6PM, 3), bucket(new Date(2026, 9, 5, 7), 1)], "events");

    expect(busiest(week)).toEqual({ day: 4, hour: 18, count: 3 });
    expect(busiest(foldWeek([], "events"))).toBeNull();
  });

  it("shades counts in four steps relative to the busiest, on a square-root scale", () => {
    // Square-root steps: a quarter of the busiest hour is already half as dark.
    expect([0, 1, 2, 5, 16].map((count) => level(count, 16))).toEqual([0, 1, 2, 3, 4]);
    expect(level(3, 0)).toBe(0);
  });
});
