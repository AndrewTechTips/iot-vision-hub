import { describe, expect, it } from "vitest";

import { clipMoments, clipTime } from "./clip-moments.js";

const EVENT = {
  started_at: "2026-10-10T18:00:03.000Z",
  person_at: "2026-10-10T18:00:05.500Z",
  snapshot_at: "2026-10-10T18:00:04.000Z",
  clip: {
    url: "/clip.webm",
    content_type: "video/webm",
    size_bytes: 1,
    started_at: "2026-10-10T18:00:00.000Z",
  },
};

describe("clipMoments", () => {
  it("places the motion, the most motion and the person in the clip, in order", () => {
    expect(clipMoments(EVENT)).toEqual([
      { kind: "motion", label: "Motion", seconds: 3 },
      { kind: "peak", label: "Most motion", seconds: 4 },
      { kind: "person", label: "Person", seconds: 5.5 },
    ]);
  });

  it("leaves out what didn't happen", () => {
    expect(clipMoments({ ...EVENT, person_at: null }).map((m) => m.kind)).toEqual([
      "motion",
      "peak",
    ]);
  });

  it("has nothing to say for clips without a start time (older events)", () => {
    expect(clipMoments({ ...EVENT, clip: { ...EVENT.clip, started_at: null } })).toEqual([]);
    expect(clipMoments({ ...EVENT, clip: null })).toEqual([]);
  });
});

describe("clipTime", () => {
  it("reads like a player's clock", () => {
    expect([0, 4.9, 65, 600].map(clipTime)).toEqual(["0:00", "0:04", "1:05", "10:00"]);
  });
});
