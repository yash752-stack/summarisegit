const state = {
  reportId: null,
  report: null,
  graphKind: "file",
  architectureAudience: "engineer",
};

const analyzeForm = document.getElementById("analyze-form");
const impactForm = document.getElementById("impact-form");
const searchForm = document.getElementById("search-form");
const flowForm = document.getElementById("flow-form");
const diffForm = document.getElementById("diff-form");
const statusText = document.getElementById("status-text");

analyzeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusText.textContent = "Analyzing repository...";

  const formData = new FormData();
  appendIfPresent(formData, "local_path", document.getElementById("local-path").value.trim());
  appendIfPresent(formData, "repo_url", document.getElementById("repo-url").value.trim());
  appendIfPresent(formData, "branch", document.getElementById("branch").value.trim());
  appendIfPresent(formData, "extensions", document.getElementById("extensions").value.trim());
  appendIfPresent(formData, "exclude_dirs", document.getElementById("exclude-dirs").value.trim());

  const zipFile = document.getElementById("repo-zip").files[0];
  if (zipFile) formData.append("upload", zipFile);

  try {
    const response = await fetch("/api/analyze", { method: "POST", body: formData });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Analysis failed");

    state.reportId = payload.report_id;
    state.architectureAudience = "engineer";
    await loadReport();
    statusText.textContent = `Ready: ${payload.repo_name}`;
  } catch (error) {
    statusText.textContent = `Error: ${error.message}`;
  }
});

impactForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.reportId) return;

  const target = document.getElementById("impact-target").value.trim();
  const response = await fetch(`/api/reports/${state.reportId}/impact?target=${encodeURIComponent(target)}`);
  const payload = await response.json();
  const root = document.getElementById("impact-output");
  if (!response.ok) {
    root.innerHTML = `<div class="impact-card warning">${escapeHtml(payload.detail)}</div>`;
    return;
  }

  root.innerHTML = `
    <div class="impact-card">
      <div class="item-title">${escapeHtml(payload.target)}</div>
      <div class="risk-score">${payload.risk_score}</div>
      <div class="meta">${escapeHtml(payload.explanation)}</div>
      <div class="meta">Affected files: ${escapeHtml(payload.affected_files.join(", ") || "None")}</div>
      <div class="meta">Suggested tests: ${escapeHtml(payload.suggested_tests.join(", ") || "No matching tests found")}</div>
    </div>
  `;
});

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.reportId) return;

  const query = document.getElementById("search-query").value.trim();
  const response = await fetch(`/api/reports/${state.reportId}/search?q=${encodeURIComponent(query)}`);
  const payload = await response.json();
  const root = document.getElementById("search-results");
  root.innerHTML = "";

  for (const item of payload.results || []) {
    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = `
      <div class="item-title">${escapeHtml(item.name)}</div>
      <div class="meta">${escapeHtml(item.file_path)} · ${escapeHtml(item.level)} · score ${item.score}</div>
      <div class="meta">${escapeHtml(item.explanation)}</div>
    `;
    card.addEventListener("click", () => showSearchItem(item));
    root.appendChild(card);
  }
});

flowForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.reportId) return;

  const target = document.getElementById("flow-target").value.trim();
  const response = await fetch(`/api/reports/${state.reportId}/flow?target=${encodeURIComponent(target)}`);
  const payload = await response.json();
  const root = document.getElementById("flow-output");
  if (!response.ok) {
    root.innerHTML = `<div class="impact-card warning">${escapeHtml(payload.detail)}</div>`;
    return;
  }

  root.innerHTML = `
    <div class="impact-card">
      <div class="item-title">${escapeHtml(payload.target)}</div>
      <div class="meta">${escapeHtml(payload.narrative)}</div>
      <div class="list">
        ${(payload.steps || [])
          .map(
            (step) => `
              <div class="list-item">
                <div class="item-title">Depth ${step.depth}: ${escapeHtml(step.symbol)}</div>
                <div class="meta">${escapeHtml(step.file_path)} · calls ${escapeHtml((step.calls || []).join(", ") || "none")}</div>
              </div>
            `
          )
          .join("")}
      </div>
    </div>
  `;
});

diffForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.reportId) return;

  const diffText = document.getElementById("diff-text").value;
  const response = await fetch(`/api/reports/${state.reportId}/review-diff`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ diff_text: diffText }),
  });
  const payload = await response.json();
  const root = document.getElementById("diff-output");
  if (!response.ok) {
    root.innerHTML = `<div class="impact-card warning">${escapeHtml(payload.detail)}</div>`;
    return;
  }

  root.innerHTML = `
    <div class="impact-card">
      <div class="item-title">Diff Summary</div>
      <div class="meta">${escapeHtml(payload.summary)}</div>
      <div class="meta">Touched files: ${escapeHtml((payload.touched_files || []).join(", ") || "None detected")}</div>
    </div>
    <div class="list">
      ${(payload.risk_areas || [])
        .map(
          (item) => `
            <div class="list-item">
              <div class="item-title">${escapeHtml(item.target)}</div>
              <div class="meta">risk ${item.risk_score} · ${escapeHtml(item.explanation)}</div>
            </div>
          `
        )
        .join("")}
    </div>
  `;
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", async () => {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
    button.classList.add("active");
    state.graphKind = button.dataset.graphKind;
    if (state.reportId) await drawGraph();
  });
});

document.getElementById("engineer-view").addEventListener("click", async () => {
  state.architectureAudience = "engineer";
  if (state.reportId) await renderArchitecture();
});

document.getElementById("newcomer-view").addEventListener("click", async () => {
  state.architectureAudience = "newcomer";
  if (state.reportId) await renderArchitecture();
});

async function loadReport() {
  const response = await fetch(`/api/reports/${state.reportId}`);
  state.report = await response.json();
  renderSummary();
  await renderArchitecture();
  renderFolders();
  renderRefactors();
  renderDeadCode();
  await drawGraph();
}

function renderSummary() {
  const summary = state.report.summary;
  document.getElementById("summary-cards").innerHTML = [
    ["Files", summary.total_files],
    ["Functions", summary.total_functions],
    ["Classes", summary.total_classes],
    ["Chunks", summary.total_chunks],
  ]
    .map(([label, value]) => `<div class="summary-card"><div class="label">${label}</div><div class="value">${value}</div></div>`)
    .join("");

  document.getElementById("tech-stack").innerHTML = (summary.tech_stack || [])
    .map((item) => `<span class="pill">${escapeHtml(item)}</span>`)
    .join("");

  document.getElementById("metrics-list").innerHTML = createListMarkup(
    Object.entries(state.report.metrics || {}).map(([key, value]) => ({
      title: key.replaceAll("_", " "),
      meta: String(value),
    }))
  );

  document.getElementById("biggest-files").innerHTML = createListMarkup(
    (summary.biggest_files || []).map((item) => ({
      title: item.path,
      meta: `${item.line_count} lines · ${item.imports} imports`,
    }))
  );

  document.getElementById("most-used-functions").innerHTML = createListMarkup(
    (summary.most_used_functions || []).map((item) => ({
      title: item.name,
      meta: `${item.file_path} · ${item.call_count} inbound calls`,
    }))
  );
}

async function renderArchitecture() {
  const mode = state.architectureAudience === "newcomer" ? "newcomer" : "architecture";
  const audience = state.architectureAudience;
  const response = await fetch(`/api/reports/${state.reportId}/explain?mode=${mode}&audience=${audience}`);
  const architecture = await response.json();
  document.getElementById("architecture").innerHTML = `
    <p>${escapeHtml(architecture.headline || "")}</p>
    <p class="meta">${escapeHtml(architecture.narrative || "")}</p>
    <div>${(architecture.directories || [])
      .map((item) => `<span class="pill">${escapeHtml(item.directory)}: ${item.files}</span>`)
      .join("")}</div>
  `;
}

function renderFolders() {
  document.getElementById("folder-summaries").innerHTML = createListMarkup(
    (state.report.folder_summaries || []).map((item) => ({
      title: item.folder,
      meta: item.summary,
    }))
  );
}

function renderRefactors() {
  document.getElementById("refactor-list").innerHTML = createListMarkup(
    (state.report.refactor_suggestions || []).map((item) => ({
      title: item.target,
      meta: item.reason,
    }))
  );
}

function renderDeadCode() {
  document.getElementById("dead-code-list").innerHTML = createListMarkup(
    (state.report.dead_code || []).map((item) => ({
      title: item.name,
      meta: `${item.file_path} · ${item.reason}`,
    }))
  );
}

function createListMarkup(items) {
  if (!items.length) return `<div class="meta">Nothing notable here yet.</div>`;
  return `<div class="list">${items
    .map(
      (item) => `<div class="list-item"><div class="item-title">${escapeHtml(item.title)}</div><div class="meta">${escapeHtml(item.meta)}</div></div>`
    )
    .join("")}</div>`;
}

async function drawGraph() {
  const response = await fetch(`/api/reports/${state.reportId}/graph?kind=${state.graphKind}`);
  const graph = await response.json();
  const svg = d3.select("#graph-canvas");
  svg.selectAll("*").remove();

  const width = 900;
  const height = 500;
  const simulation = d3
    .forceSimulation(graph.nodes)
    .force("link", d3.forceLink(graph.links).id((d) => d.id).distance(state.graphKind === "file" ? 110 : 70))
    .force("charge", d3.forceManyBody().strength(-150))
    .force("center", d3.forceCenter(width / 2, height / 2));

  const links = svg
    .append("g")
    .attr("stroke", "rgba(255,255,255,0.18)")
    .selectAll("line")
    .data(graph.links)
    .join("line");

  const nodes = svg
    .append("g")
    .selectAll("circle")
    .data(graph.nodes)
    .join("circle")
    .attr("r", (d) => Math.max(5, Math.min(20, d.size / 2.2)))
    .attr("fill", (d) => (d.kind === "file" ? "#8b7dff" : "#4de2c5"))
    .call(drag(simulation))
    .on("click", (_, node) => showGraphNode(node));

  const labels = svg
    .append("g")
    .selectAll("text")
    .data(graph.nodes)
    .join("text")
    .text((d) => truncate(d.label, state.graphKind === "file" ? 34 : 28))
    .attr("font-size", 11)
    .attr("fill", "#d8e1ff");

  simulation.on("tick", () => {
    links.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y).attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
    nodes.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
    labels.attr("x", (d) => d.x + 10).attr("y", (d) => d.y + 4);
  });
}

async function showGraphNode(node) {
  if (state.graphKind === "file") {
    const response = await fetch(`/api/reports/${state.reportId}/explain?mode=file&target=${encodeURIComponent(node.id)}`);
    const payload = await response.json();
    document.getElementById("node-details").innerHTML = `
      <div class="item-title">${escapeHtml(payload.target)}</div>
      <div class="meta">${escapeHtml(payload.summary)}</div>
    `;
    renderConnections([
      { title: "Imports", items: payload.imports || [] },
      { title: "Dependents", items: payload.dependents || [] },
    ]);
    const sourceResponse = await fetch(`/api/reports/${state.reportId}/source?path=${encodeURIComponent(node.id)}`);
    const sourcePayload = await sourceResponse.json();
    document.getElementById("source-viewer").textContent = sourcePayload.source || "";
    return;
  }

  const response = await fetch(`/api/reports/${state.reportId}/explain?mode=symbol&target=${encodeURIComponent(node.label)}`);
  const payload = await response.json();
  document.getElementById("node-details").innerHTML = `
    <div class="item-title">${escapeHtml(payload.target)}</div>
    <div class="meta">${escapeHtml(payload.summary)}</div>
  `;
  renderConnections([
    { title: "Calls", items: payload.calls || [] },
    { title: "Callers", items: payload.callers || [] },
  ]);
  document.getElementById("source-viewer").textContent = payload.snippet || "";
}

async function showSearchItem(item) {
  if (item.kind === "file") {
    await showGraphNode({ id: item.file_path, label: item.name });
    return;
  }
  await showGraphNode({ id: item.id, label: item.name });
}

function renderConnections(groups) {
  const root = document.getElementById("node-connections");
  const filtered = groups.filter((group) => group.items && group.items.length);
  if (!filtered.length) {
    root.innerHTML = `<div class="meta">No connected nodes were found for this selection.</div>`;
    return;
  }

  root.innerHTML = filtered
    .map(
      (group) => `
        <div class="subpanel compact-subpanel">
          <h3>${escapeHtml(group.title)}</h3>
          <div>${group.items.map((item) => `<span class="pill">${escapeHtml(item)}</span>`).join("")}</div>
        </div>
      `
    )
    .join("");
}

function appendIfPresent(formData, key, value) {
  if (value) formData.append(key, value);
}

function truncate(text, maxLength) {
  return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function drag(simulation) {
  function dragstarted(event) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    event.subject.fx = event.subject.x;
    event.subject.fy = event.subject.y;
  }

  function dragged(event) {
    event.subject.fx = event.x;
    event.subject.fy = event.y;
  }

  function dragended(event) {
    if (!event.active) simulation.alphaTarget(0);
    event.subject.fx = null;
    event.subject.fy = null;
  }

  return d3.drag().on("start", dragstarted).on("drag", dragged).on("end", dragended);
}
