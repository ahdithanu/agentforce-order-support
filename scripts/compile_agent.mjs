// Compile an Agent Script file with the public AgentScript SDK and fail on errors.
// Usage: node scripts/compile_agent.mjs <file.agent>   (SDK installed via `npm install --prefix ci`)
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const file = process.argv[2];
if (!file) {
  console.error("usage: node scripts/compile_agent.mjs <file.agent>");
  process.exit(2);
}
// The package is ESM-only (exports["."].import), so resolve its entry from package.json.
const pkgRoot = path.resolve("ci/node_modules/@sf-agentscript/agentforce");
const manifest = JSON.parse(fs.readFileSync(path.join(pkgRoot, "package.json"), "utf8"));
const exp = manifest.exports?.["."];
const entry = path.join(pkgRoot, (typeof exp === "string" ? exp : exp?.import || exp?.default) || manifest.main);
const { compileSource } = await import(pathToFileURL(entry).href);

const result = compileSource(fs.readFileSync(file, "utf8"));
const label = { 1: "error", 2: "warning", 3: "info", 4: "hint" };
for (const d of result.diagnostics) {
  const where = `${file}:${d.range.start.line + 1}:${d.range.start.character + 1}`;
  const line = `${where} ${label[d.severity] || d.severity} ${d.code}: ${d.message}`;
  if (d.severity === 1) console.log(`::error file=${file},line=${d.range.start.line + 1}::${d.code}: ${d.message}`);
  console.log(line);
}
const errors = result.diagnostics.filter((d) => d.severity === 1).length;
console.log(`${path.basename(file)}: ${errors} error(s), ${result.diagnostics.length} diagnostic(s)`);
process.exit(errors ? 1 : 0);
