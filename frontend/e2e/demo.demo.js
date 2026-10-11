import { expect, test } from "@playwright/test";

import { audit } from "./axe.js";

// The demo opens signed in, on the live view, with a hub that only exists in the page.

test("opens signed in, with every camera live and no hub behind it", async ({ page }) => {
  /** @type {string[]} */
  const apiCalls = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiCalls.push(request.url());
  });
  await page.goto("./");

  await expect(page.getByRole("heading", { name: "Live" })).toBeVisible();
  await expect(page.locator(".camera-tile")).toHaveCount(4);
  for (const tile of await page.locator(".camera-tile").all()) {
    await tile.scrollIntoViewIfNeeded(); // streams only run on screen
    await expect(tile).toHaveAttribute("data-state", "playing");
  }
  const note = page.getByRole("complementary", { name: "About this demo" });
  await expect(note.getByRole("link", { name: "Run it yourself" })).toHaveAttribute(
    "href",
    "https://github.com/AndrewTechTips/iot-vision-hub",
  );
  expect(apiCalls, "every API call is answered in the page").toEqual([]);

  await note.getByRole("button", { name: "Hide this note" }).click();
  await expect(note).toBeHidden();
  await page.reload();
  await expect(page.locator(".camera-tile").first()).toBeVisible();
  await expect(note).toBeHidden(); // for the rest of the visit
});

test("a person at the front door raises an alert that opens the event", async ({ page }) => {
  await page.goto("./");

  // The demo starts the front door a moment before someone walks up; the visit lasts ~20 s.
  const alert = page.locator(".toast", { hasText: "Person at Front door" });
  await expect(alert).toBeVisible({ timeout: 45_000 });
  // Boxes turned red while he was in view, and the patio's shadows never alerted.
  await expect(page.locator(".toast", { hasText: "Patio" })).toHaveCount(0);

  await alert.getByRole("button", { name: "View" }).click();
  await expect(page).toHaveURL(/\/iot-vision-hub\/events\?event=/);
  const viewer = page.getByRole("dialog");
  await expect(viewer.getByRole("heading", { name: "Front door" })).toBeVisible();
  await expect(viewer).toContainText("a person was seen");
  const clip = await viewer.locator("video").getAttribute("src");
  expect(clip).toMatch(/\/iot-vision-hub\/demo\/front-door\/event-\d+\.webm$/);

  // The clip's moments jump the player: the person was found just after the motion began.
  const moments = viewer.getByRole("group", { name: "Jump to" });
  await expect(moments.getByRole("button")).toHaveText([/^Motion/, /^Person/, /^Most motion/]);
  await moments.getByRole("button", { name: /^Person/ }).click();
  const at = await viewer
    .locator("video")
    .evaluate((video) => /** @type {HTMLVideoElement} */ (video).currentTime);
  expect(at).toBeGreaterThan(2);
  expect(at).toBeLessThan(4.5);
});

test("the camera page draws the person's box from the recording", async ({ page }) => {
  await page.goto("./devices/front-door");

  const stage = page.locator(".device-stage");
  await expect(stage).toHaveAttribute("data-state", "playing");
  // His tracked body, labelled with the person check's score.
  const person = stage.locator(".detection-box[data-person]:not([hidden])");
  await expect(person.first()).toBeAttached({ timeout: 45_000 });
  await expect(person.first().locator(".detection-label")).toHaveText(/^Person \d+ %$/);
  await expect(stage.locator(".camera-motion")).toContainText(/Person \d+ %/);
});

test("zone rules alert: someone crossing a line, someone lingering at the door", async ({
  page,
}) => {
  await page.goto("./");

  // The lobby's visitor walks out past the shop front ~9 s in; at the front door, he has been
  // at the door 8 s (the Doorstep area's dwell) ~10 s in, and leaves ~25 s in.
  const crossed = page.locator(".toast", { hasText: "Someone crossed Shop front" });
  await expect(crossed).toBeVisible({ timeout: 30_000 });
  await expect(crossed).toContainText("At Lobby");
  const lingering = page.locator(".toast", { hasText: "Someone lingering at Front door" });
  await expect(lingering).toBeVisible({ timeout: 30_000 });
  await expect(lingering).toContainText("In Doorstep since");
  await expect(page.locator(".toast", { hasText: "Doorstep is clear" })).toBeVisible({
    timeout: 40_000,
  });
  await expect(lingering).toHaveCount(0); // replaced by the all-clear

  // Through the app: a reload would start a fresh demo hub.
  await page
    .getByRole("navigation", { name: "Main" })
    .first()
    .getByRole("link", { name: "Events" })
    .click();
  const stayed = page.locator(".event-row", { hasText: "Lingering · Doorstep" }).first();
  await expect(stayed).toContainText(/Someone stayed \d+ s in Doorstep/);
  await expect(
    page.locator(".event-row", { hasText: "Crossed · Shop front" }).first(),
  ).toContainText("Someone crossed Shop front");
});

test("the camera page shows its zones, lit while a rule fires", async ({ page }) => {
  await page.goto("./devices/front-door");

  const stage = page.locator(".device-stage");
  await expect(stage.locator(".zone-labels")).toHaveText("Doorstep");
  await expect(stage.locator(".zone-area")).toHaveCount(1);
  await expect(stage.locator(".zone-area.is-active")).toHaveCount(1, { timeout: 30_000 });
  await expect(page.getByText("Zone rules: 1 area.")).toBeVisible();
});

test("history, filters and deep links work without a server", async ({ page }) => {
  await page.goto("./events?people=1");

  const rows = page.locator(".event-row");
  await expect(rows.first()).toBeVisible();
  for (const name of await rows.locator(".font-medium").allTextContents()) {
    expect(["Front door", "Lobby"]).toContain(name.trim());
  }
  await page.reload(); // a deep link, as GitHub Pages serves it
  await expect(page.getByRole("heading", { name: "Events" })).toBeVisible();

  await page.goto("./activity");
  await expect(page.getByText("changed").first()).toBeVisible();
});

test("settings and camera controls work in memory", async ({ page }, { project }) => {
  test.skip(project.name === "phone", "covered on desktop");
  await page.goto("./devices/driveway");

  await page.getByRole("button", { name: "Stop camera" }).click();
  await expect(page.locator(".device-stage")).toHaveAttribute("data-state", "stopped");
  await page.getByRole("button", { name: "Start camera" }).click();
  await expect(page.locator(".device-stage")).toHaveAttribute("data-state", "playing");

  // Through the app: reloading would start a fresh demo hub, without this visit's changes.
  await page
    .getByRole("navigation", { name: "Main" })
    .first()
    .getByRole("link", { name: "Activity" })
    .click();
  await expect(page.getByText("started").first()).toBeVisible();
  await expect(page.getByText("stopped").first()).toBeVisible();
});

test("a covered camera raises a red alert until it is back", async ({ page }) => {
  await page.goto("./?tamper"); // covers the patio camera after 3 s instead of a minute

  const patio = page.locator(".camera-tile", { hasText: "Patio" });
  await patio.scrollIntoViewIfNeeded();
  const alert = page.locator(".toast", { hasText: "Patio camera covered" });
  await expect(alert).toBeVisible({ timeout: 20_000 }); // it has to last 8 s to count
  await expect(patio).toHaveAttribute("data-tamper", "");
  await expect(patio.locator(".camera-tamper")).toHaveText("Covered");
  await expect(patio).toContainText("Camera covered now");

  await expect(page.locator(".toast", { hasText: "Patio camera is back" })).toBeVisible({
    timeout: 30_000,
  });
  await expect(patio).not.toHaveAttribute("data-tamper");

  // Through the app: a reload would start a fresh demo hub.
  await page
    .getByRole("navigation", { name: "Main" })
    .first()
    .getByRole("link", { name: "Events" })
    .click();
  const row = page.locator(".event-row", { hasText: "Patio" }).filter({ hasText: "Covered" });
  await expect(row.first()).toContainText(/Camera covered for \d+ s/);
});

test("the demo passes the same accessibility audit", async ({ page }) => {
  await page.goto("./");
  await expect(page.locator(".camera-tile").first()).toHaveAttribute("data-state", "playing");
  await audit(page, "demo live view");
  await page.goto("./events");
  await expect(page.locator(".event-row").first()).toBeVisible();
  await audit(page, "demo events");
});
