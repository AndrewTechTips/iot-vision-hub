// The demo hub's hourly counts (GET /events/activity), made up: the recording holds one pass of
// each loop, and replaying it brings someone to the door every half minute, which no real door
// sees. Instead each camera follows a plausible pattern in the visitor's local time, with the
// same numbers on every visit. Events during the visit still add to the tiles' totals live.

/** @typedef {import("../api/types.js").ActivityBucket} ActivityBucket */

const HOUR = 3_600_000;

/**
 * Expected events per hour by local hour (0-23), and the share with a person.
 * @type {Record<string, { rate: (hour: number, weekend: boolean) => number, people: number }>}
 */
const PATTERNS = {
  // Comings and goings: mornings and evenings, deliveries in the day.
  "front-door": {
    rate: (hour, weekend) =>
      hour < 6
        ? 0.05
        : hour < 7
          ? 0.5
          : hour < 9
            ? weekend
              ? 0.8
              : 2.5
            : hour < 17
              ? 0.9
              : hour < 22
                ? 2.2
                : 0.4,
    people: 1,
  },
  // A lobby is busy in the day, quiet at night and on Sundays.
  lobby: {
    rate: (hour, weekend) => (hour >= 8 && hour < 20 ? (weekend ? 1.2 : 3.5) : 0.15),
    people: 0.85,
  },
  // Cars leave and come back; nobody walks past the camera.
  driveway: {
    rate: (hour, weekend) =>
      weekend
        ? hour >= 10 && hour < 19
          ? 0.7
          : 0.05
        : hour === 7 || hour === 8
          ? 1.6
          : hour >= 17 && hour < 20
            ? 1.4
            : hour >= 9 && hour < 17
              ? 0.3
              : 0.05,
    people: 0,
  },
  // Sun on the wall in the afternoon: the palm's shadow moves.
  patio: {
    rate: (hour) => (hour >= 11 && hour < 18 ? 2.8 : hour >= 9 && hour < 20 ? 0.6 : 0),
    people: 0,
  },
};

/**
 * Hourly counts for [since, until), oldest first.
 * @param {{ devices: string[], since: Date, until: Date }} options
 * @returns {ActivityBucket[]}
 */
export function activity({ devices, since, until }) {
  /** @type {ActivityBucket[]} */
  const buckets = [];
  const first = Math.floor(since.getTime() / HOUR);
  const last = Math.ceil(until.getTime() / HOUR);
  for (let hour = first; hour < last; hour += 1) {
    for (const device of devices) {
      const counts = madeUp(device, hour);
      if (counts?.events) {
        buckets.push({ device_id: device, start: new Date(hour * HOUR).toISOString(), ...counts });
      }
    }
  }
  return buckets;
}

/**
 * @param {string} device
 * @param {number} hour hours since the epoch
 */
function madeUp(device, hour) {
  const pattern = PATTERNS[device];
  if (!pattern) return null;
  const local = new Date(hour * HOUR);
  const weekend = local.getDay() === 0 || local.getDay() === 6;
  const random = mulberry32(hour * 31 + device.length * 7919);
  const events = Math.floor(
    pattern.rate(local.getHours(), weekend) * (0.4 + random() * 1.2) + random() * 0.6,
  );
  const people = Math.round(events * pattern.people);
  return { events, people, alerts: people };
}

/**
 * A small seeded random number generator: the same hour gives the same counts on every visit.
 * @param {number} seed
 */
function mulberry32(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
