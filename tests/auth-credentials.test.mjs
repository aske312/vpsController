import assert from "node:assert/strict";
import test from "node:test";
import { basicCredentials } from "../src/auth-credentials.ts";

test("Basic credentials preserve valid passwords including colons", () => {
  assert.equal(atob(basicCredentials("admin", "Password:123!")), "admin:Password:123!");
});

test("unsupported credentials produce a readable validation error", () => {
  for (const [username, password] of [["админ", "Password123!"], ["admin", "Пароль123!"], ["ad:min", "secret"], ["admin", "contains space"], ["", "secret"]]) {
    assert.throws(() => basicCredentials(username, password), /Логин|Пароль/);
  }
});
