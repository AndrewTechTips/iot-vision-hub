import { describe, expect, it } from "vitest";

import { layout } from "./detection-overlay.js";

describe("layout", () => {
  it("places boxes as percentages of the picture", () => {
    expect(layout({ boxes: [[0.5, 0.25, 0.1, 0.5]], person: null, tracks: [] })).toEqual([
      {
        place: { left: "50.00%", top: "25.00%", width: "10.00%", height: "50.00%" },
        person: null,
        top: false,
        right: false,
      },
    ]);
  });

  it("clips boxes that reach past the picture", () => {
    expect(layout({ boxes: [[-0.1, 0.9, 0.3, 0.3]], person: null, tracks: [] })).toEqual([
      {
        place: { left: "0.00%", top: "90.00%", width: "20.00%", height: "10.00%" },
        person: null,
        top: false,
        right: false,
      },
    ]);
  });

  it("keeps the largest boxes when there are too many", () => {
    const small = /** @type {[number, number, number, number]} */ ([0, 0, 0.01, 0.01]);
    const large = /** @type {[number, number, number, number]} */ ([0.2, 0.2, 0.5, 0.5]);
    const placed = layout({ boxes: [...Array(30).fill(small), large], person: null, tracks: [] });

    expect(placed).toHaveLength(24);
    expect(placed[0].place).toEqual({
      left: "20.00%",
      top: "20.00%",
      width: "50.00%",
      height: "50.00%",
    });
  });

  it("draws tracked objects instead of motion boxes when there are any", () => {
    const placed = layout({
      boxes: [[0.4, 0.4, 0.05, 0.05]],
      person: null,
      tracks: [
        [0.1, 0.1, 0.2, 0.5, 7, 0.91],
        [0.6, 0.6, 0.1, 0.1, 8, null],
      ],
    });

    expect(placed.map((item) => item.person)).toEqual([0.91, null]);
    expect(placed[0].place).toEqual({
      left: "10.00%",
      top: "10.00%",
      width: "20.00%",
      height: "50.00%",
    });
  });

  it("marks boxes at the top of the picture, whose label goes inside them", () => {
    const placed = layout({ boxes: [], person: null, tracks: [[0.2, -0.1, 0.3, 1, 3, 0.87]] });

    expect(placed[0].top).toBe(true);
    expect(placed[0].right).toBe(false);
    expect(placed[0].place.top).toBe("0.00%");
  });

  it("marks boxes at the right edge, whose label is aligned to their right side", () => {
    const placed = layout({ boxes: [], person: null, tracks: [[0.9, 0.2, 0.4, 0.5, 3, 0.87]] });

    expect(placed[0].right).toBe(true);
    expect(placed[0].place.width).toBe("10.00%");
  });

  it("has nothing to place without detections", () => {
    expect(layout(null)).toEqual([]);
  });
});
