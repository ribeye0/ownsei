import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
const output = ts.transpileModule(
  readFileSync(new URL("../src/projectState.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const { projectNumber, nextProjectTitle, nextProjectSelection } = await import(
  `data:text/javascript;base64,${Buffer.from(output).toString("base64")}`
);
test("project numbering accepts old names and both interface languages without specializing custom titles", () => {
  assert.equal(projectNumber("專案01"), 1);
  assert.equal(projectNumber("项目 007"), 7);
  assert.equal(projectNumber("測試001"), 0);
  assert.equal(
    nextProjectTitle(["專案01", "專案 3", "nono"], "專案"),
    "專案 4",
  );
  assert.equal(nextProjectTitle(["專案01", "项目 9"], "项目"), "项目 10");
  assert.equal(nextProjectTitle([], "專案", 12), "專案 13");
  assert.equal(nextProjectTitle([], "專案", NaN), "專案 1");
});

test("deleting the current project selects its neighbour; deleting a background project preserves selection", () => {
  assert.equal(nextProjectSelection(["a", "b", "c"], ["a", "c"], "b"), "c");
  assert.equal(nextProjectSelection(["a", "b"], ["a"], "b"), "a");
  assert.equal(nextProjectSelection(["a"], [], "a"), "");
  assert.equal(nextProjectSelection(["a", "b"], ["a"], "a"), "a");
  assert.equal(nextProjectSelection(["a", "b"], ["a", "b"], ""), "");
});
