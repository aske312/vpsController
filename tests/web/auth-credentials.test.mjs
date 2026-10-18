import assert from "node:assert/strict";
import test from "node:test";
import { basicCredentials } from "../../src/features/auth/basic-credentials.ts";

test("login uses the server's Basic credential format including password colons", () => {
  assert.equal(atob(basicCredentials("admin", "Long:Password-123!")), "admin:Long:Password-123!");
});

test("unsupported login characters fail with a usable authentication error", () => {
  for (const [user, password] of [["admin", "пароль"], ["админ", "Password-123!"], ["admin", "pässword"], ["admin", "😀"], ["ad:min", "Password-123!"], ["admin", "a\nb"], ["", "Password-123!"], ["admin", ""]]) {
    assert.throws(() => basicCredentials(user, password), { message: "Неверный логин или пароль" });
  }
});
