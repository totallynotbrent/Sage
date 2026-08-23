import * as dompurify_ns from "dompurify";

function stub_purify(target) {
  for (const method of ["addHook", "removeHook", "removeHooks", "sanitize"]) {
    if (typeof target[method] !== "function") {
      target[method] = (...args) => (method === "sanitize" ? args[0] : target);
    }
  }
}

try {
  stub_purify(dompurify_ns.default ?? dompurify_ns);
} catch {}

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);

try {
  const input = JSON.parse(chunks.join(""));
  const source = String(input.source || "");
  const { default: mermaid } = await import("mermaid");
  const diagram_type = await mermaid.parse(source, { suppressErrors: false });
  process.stdout.write(JSON.stringify({ ok: true, diagram_type }));
} catch (error) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(error?.message || error) }));
  process.exitCode = 0;
}
