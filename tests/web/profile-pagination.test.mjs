import assert from "node:assert/strict";
import test from "node:test";
import { profilePage, PROFILES_PER_PAGE } from "../../src/features/mihomo/profile-pagination.ts";

test("profile pages cover every profile once including the final partial page", () => {
  const profiles = Array.from({ length: 23 }, (_, id) => id);
  const visited = [];
  for (let requested = 1; requested <= 3; requested++) {
    const page = profilePage(profiles.length, requested);
    const visible = profiles.slice(page.offset, page.offset + PROFILES_PER_PAGE);
    assert.equal(visible.length, page.end - page.start + 1);
    visited.push(...visible);
  }
  assert.deepEqual(visited, profiles);
});

test("refresh preserves a valid page and deletion moves an empty last page back", () => {
  assert.equal(profilePage(23, 2).page, 2);
  const afterDeletion = profilePage(20, 3);
  assert.deepEqual(afterDeletion, { page: 2, pages: 2, offset: 10, start: 11, end: 20 });
  assert.equal(profilePage(21, afterDeletion.page).page, 2);
  assert.deepEqual(profilePage(0, 3), { page: 1, pages: 1, offset: 0, start: 0, end: 0 });
  assert.equal(profilePage(10, 1).pages, 1);
});
