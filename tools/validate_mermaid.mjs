import mermaid from "mermaid";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);

try {
  const input = JSON.parse(chunks.join(""));
  const source = String(input.source || "");
  const diagram_type = await mermaid.parse(source, { suppressErrors: false });
  process.stdout.write(JSON.stringify({ ok: true, diagram_type }));
} catch (error) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(error?.message || error) }));
  process.exitCode = 0;
}
