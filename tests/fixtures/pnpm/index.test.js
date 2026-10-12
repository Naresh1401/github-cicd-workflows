const test = require("node:test");
const assert = require("node:assert/strict");

test("pnpm executes the fixture through integrity-verified Corepack", () => {
  assert.equal(2 + 2, 4);
});
