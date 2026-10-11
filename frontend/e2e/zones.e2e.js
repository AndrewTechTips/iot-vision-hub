import { expect, test } from "@playwright/test";

import { isPhone, signedIn } from "./fixtures.js";

// Zone rules drawn over the live picture, saved to the hub, and back after a reload.

test("an admin draws, names, saves and deletes zone rules", async ({ page }) => {
  // One browser at a time: both runs would edit the same camera.
  test.skip(isPhone(page), "covered on desktop");
  await signedIn(page, "/devices/e2e-porch");
  const stage = page.locator(".device-stage");
  await expect(stage).toHaveAttribute("data-state", "playing");

  await page.getByRole("button", { name: "Edit zones" }).click();
  const surface = page.locator("[data-zone-editing]");
  const box = await surface.boundingBox();
  if (!box) throw new Error("the picture has no size");
  /** Taps the picture at a share of its width and height. @param {number} x @param {number} y */
  const tap = (x, y) => page.mouse.click(box.x + box.width * x, box.y + box.height * y);

  // An area: three corners, then Finish. A line: its two ends.
  await page.getByRole("button", { name: "Add area" }).click();
  await tap(0.1, 0.6);
  await tap(0.4, 0.6);
  await tap(0.4, 0.95);
  await page.getByRole("button", { name: "Finish area" }).click();
  await page.getByRole("textbox", { name: "Name of area 1" }).fill("Porch steps");
  await page.getByRole("textbox", { name: "Name of area 1" }).press("Tab");
  await page
    .getByRole("spinbutton", { name: "Seconds someone must stay in Porch steps" })
    .fill("5");
  await page
    .getByRole("spinbutton", { name: "Seconds someone must stay in Porch steps" })
    .press("Tab");
  await page.getByRole("button", { name: "Add line" }).click();
  await tap(0.7, 0.1);
  await tap(0.7, 0.9);
  await expect(stage.locator(".zone-area")).toHaveCount(1);
  await expect(stage.locator(".zone-line")).toHaveCount(1);
  await expect(stage.locator(".zone-labels")).toHaveText(/Porch steps\s*Line 1/);
  await expect(page.getByText("Zone rules: 1 area and 1 line.")).toBeVisible();

  const request = page.waitForRequest(
    (sent) => sent.method() === "PUT" && sent.url().endsWith("/detection-config"),
  );
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.locator(".toast", { hasText: "Detection settings saved" })).toBeVisible();
  const { zones } = (await request).postDataJSON();
  expect(zones).toEqual([
    { name: "Porch steps", kind: "area", points: expect.any(Array), dwell_seconds: 5 },
    { name: "Line 1", kind: "line", points: expect.any(Array), dwell_seconds: 10 },
  ]);
  expect(zones[0].points).toHaveLength(3);
  expect(zones[1].points[0][0]).toBeCloseTo(0.7, 1);

  await page.reload(); // from the hub
  await expect(page.getByText("Zone rules: 1 area and 1 line.")).toBeVisible();
  await expect(stage.locator(".zone-labels")).toHaveText(/Porch steps\s*Line 1/);

  // Deleted again, so the other tests' camera has no zones.
  await page.getByRole("button", { name: "Edit zones" }).click();
  await page.getByRole("button", { name: "Delete Porch steps" }).click();
  await page.getByRole("button", { name: "Delete Line 1" }).click();
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("No zone rules.")).toBeVisible();
});
