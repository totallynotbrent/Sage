"use strict";

(() => {
  const el = (id) => document.getElementById(id);

  let render_counter = 0;
  let last_nodes = [];

  const session_path = () =>
    "/api/sessions/" + encodeURIComponent(window.Sage.state.currentSessionId);

  function status_chip(status) {
    const chip = document.createElement("span");
    chip.className = `status-chip ${status}`;
    chip.textContent = status;
    return chip;
  }

  function sanitize_key(key, used) {
    let base = String(key).replace(/[^A-Za-z0-9_]/g, "_") || "node";
    let out = base;
    let i = 1;
    while (used.has(out)) {
      out = `${base}_${i}`;
      i += 1;
    }
    used.add(out);
    return out;
  }

  function build_mermaid_source(nodes) {
    const used = new Set();
    const key_map = new Map();
    const lines = ["flowchart TD"];
    lines.push("classDef current fill:#dbeafe,stroke:#2563eb,stroke-width:2px");
    lines.push("classDef done fill:#dcfce7,stroke:#15803d");
    lines.push("classDef skipped fill:#f3f4f6,stroke:#6b7280,stroke-dasharray:4");
    for (const node of nodes) {
      const safe = sanitize_key(node.node_key, used);
      key_map.set(node.node_key, safe);
      const title = String(node.title).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      lines.push(`n${safe}["${title}"]`);
    }
    for (const node of nodes) {
      const safe = key_map.get(node.node_key);
      for (const dep of node.depends_on || []) {
        const dep_safe = key_map.get(dep);
        if (dep_safe) lines.push(`n${safe} --> n${dep_safe}`);
      }
    }
    for (const node of nodes) {
      if (node.status === "current" || node.status === "done" || node.status === "skipped") {
        lines.push(`class n${key_map.get(node.node_key)} ${node.status}`);
      }
    }
    return lines.join("\n");
  }

  async function render_graph(nodes, box) {
    const token = ++render_counter;
    box.replaceChildren();
    if (!window.mermaid) return;
    const source = build_mermaid_source(nodes);
    try {
      const { svg } = await window.mermaid.render(`plan_graph_${token}`, source);
      if (render_counter !== token) return;
      box.innerHTML = svg;
    } catch (err) {
      if (render_counter === token) box.replaceChildren();
    }
  }

  function render_outline(nodes, outline) {
    outline.replaceChildren();
    for (let index = 0; index < nodes.length; index += 1) {
      const node = nodes[index];
      const li = document.createElement("li");
      li.dataset.node_key = node.node_key;

      li.appendChild(status_chip(node.status));

      const title = document.createElement("span");
      title.className = "plan-node-title";
      title.textContent = node.title;
      if (node.description) title.title = node.description;
      title.addEventListener("click", () =>
        window.Study.run("Select node", session_path() + "/plan/select", { node_key: node.node_key })
      );
      li.appendChild(title);

      const actions = document.createElement("span");
      actions.className = "plan-node-actions";

      const up = document.createElement("button");
      up.type = "button";
      up.textContent = "↑";
      up.disabled = index === 0;
      up.addEventListener("click", () => reorder(node.node_key, -1));
      actions.appendChild(up);

      const down = document.createElement("button");
      down.type = "button";
      down.textContent = "↓";
      down.disabled = index === nodes.length - 1;
      down.addEventListener("click", () => reorder(node.node_key, 1));
      actions.appendChild(down);

      const skip = document.createElement("button");
      skip.type = "button";
      skip.textContent = "Skip";
      skip.addEventListener("click", () =>
        window.Study.run("Skip node", session_path() + "/plan/skip", { node_key: node.node_key })
      );
      actions.appendChild(skip);

      const expand = document.createElement("button");
      expand.type = "button";
      expand.textContent = "Expand";
      expand.addEventListener("click", () => expand_node(node.node_key));
      actions.appendChild(expand);

      li.appendChild(actions);
      outline.appendChild(li);
    }
  }

  function render_actions(full, nodes, box) {
    box.replaceChildren();
    const phase = full.session.phase;
    if (phase === "plan") {
      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "primary";
      approve.textContent = "Approve plan";
      approve.addEventListener("click", () =>
        window.Study.run("Approve plan", session_path() + "/plan/approve")
      );
      box.appendChild(approve);
    }
    const regenerate = document.createElement("button");
    regenerate.type = "button";
    regenerate.textContent = "Regenerate";
    regenerate.addEventListener("click", () =>
      window.Study.run("Regenerate plan", session_path() + "/plan/regenerate")
    );
    box.appendChild(regenerate);
  }

  function reorder(node_key, delta) {
    const nodes = last_nodes.slice();
    const index = nodes.findIndex((node) => node.node_key === node_key);
    if (index === -1) return;
    const target = index + delta;
    if (target < 0 || target >= nodes.length) return;
    const swapped = nodes[index];
    nodes[index] = nodes[target];
    nodes[target] = swapped;
    last_nodes = nodes;
    render_outline(nodes, el("plan-outline"));
    window.Study.run(
      "Reorder plan",
      session_path() + "/plan/reorder",
      { node_keys: nodes.map((node) => node.node_key) }
    );
  }

  function expand_node(node_key) {
    const detail = window.prompt("Optional detail for this plan node:", "");
    if (detail === null) return;
    window.Study.run("Expand node", session_path() + "/plan/expand", { node_key, detail });
  }

  async function render(full) {
    const panel = el("plan-panel");
    const mermaid_box = el("plan-mermaid");
    const outline = el("plan-outline");
    const actions = el("plan-actions");
    const nodes = (full.plan || []).slice().sort((a, b) => a.position - b.position);
    last_nodes = nodes;
    if (!nodes.length) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    render_graph(nodes, mermaid_box);
    render_outline(nodes, outline);
    render_actions(full, nodes, actions);
  }

  window.StudyPlan = { render };
})();
