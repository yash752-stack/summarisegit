const state = {
  reportId: null,
  report: null,
  graphKind: "file",
};

const analyzeForm = document.getElementById("analyze-form");
const impactForm = document.getElementById("impact-form");
const searchForm = document.getElementById("search-form");
const statusText = document.getElementById("status-text");

analyzeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusText.textContent = "Analyzing repository...";

  const formData = new FormData();
  const localPath = document.getElementById("local-path").value.trim();
  const repoUrl = document.getElementById("repo-url").value.trim();
  const zipFile = document.getElementById("repo-zip").files[0];

  if (localPath) formData.append("local_path", localPath);
  if (repoUrl) formData.append("repo_url", repoUrl);
  if (zipFile) formData.append("upload", zipFile);

  try {
    const response = await fetch("/api/analyze", { method: "POST", body: formData });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Analysis failed");

    state.reportId = payload.report_id;
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
  const impactOutput = document.getElementById("impact-output");

  if (!response.ok) {
    impactOutput.innerHTML = `<div class="impact-card warning">${payload.detail}</div>`;
    return;
  }

  impactOutput.innerHTML = `
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
  const resultsRoot = document.getElementById("search-results");
  resultsRoot.innerHTML = "";

  for (const item of payload.results || []) {
    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = `
      <div class="item-title">${escapeHtml(item.name)}</div>
      <div class="meta">${escapeHtml(item.file_path)} · ${escapeHtml(item.kind)} · score ${item.score}</div>
      <div class="meta">${escapeHtml(item.explanation)}</div>
    `;
    card.addEventListener("click", () => showNodeDetails(item));
    resultsRoot.appendChild(card);
  }
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", async () => {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
    button.classList.add("active");
    state.graphKind = button.dataset.graphKind;
    if (state.reportId) await drawGraph();
  });
});

async function loadReport() {
  const response = await fetch(`/api/reports/${state.reportId}`);
  state.report = await response.json();
  renderSummary();
  renderArchitecture();
  renderRefactors();
  renderDeadCode();
  await drawGraph();
}

function renderSummary() {
  const summary = state.report.summary;
  const summaryCards = document.getElementById("summary-cards");
  const biggestFiles = document.getElementById("biggest-files");
  const usedFunctions = document.getElementById("used-functions");

  summaryCards.innerHTML = "";
  [
    ["Files", summary.total_files],
    ["Functions", summary.total_functions],
    ["Classes", summary.total_classes],
    ["Languages", Object.keys(summary.languages).length],
  ].forEach(([label, value]) => {
    const card = document.createElement("div");
    card.className = "summary-card";
    card.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
    summaryCards.appendChild(card);
  });

  biggestFiles.innerHTML = createListMarkup(summary.biggest_files.map((item) => ({
    title: item.path,
    meta: `${item.line_count} lines · ${item.imports} imports`,
  })));

  usedFunctions.innerHTML = createListMarkup((summary.most_used_functions || []).map((item) => ({
    title: item.name,
    meta: `${item.file_path} · ${item.call_count} callers`,
  })));
}

function renderArchitecture() {
  const architecture = state.report.architecture;
  const root = document.getElementById("architecture");
  root.innerHTML = `
    <p>${escapeHtml(architecture.headline)}</p>
    <p class="meta">${escapeHtml(architecture.narrative)}</p>
    <div>${architecture.directories.map((item) => `<span class="pill">${escapeHtml(item.directory)}: ${item.files}</span>`).join("")}</div>
  `;
}

function renderRefactors() {
  const root = document.getElementById("refactor-list");
  root.innerHTML = createListMarkup((state.report.refactor_suggestions || []).map((item) => ({
    title: item.target,
    meta: item.reason,
  })));
}

function renderDeadCode() {
  const root = document.getElementById("dead-code-list");
  root.innerHTML = createListMarkup((state.report.dead_code || []).map((item) => ({
    title: item.name,
    meta: `${item.file_path} · ${item.reason}`,
  })));
}

function createListMarkup(items) {
  if (!items.length) {
    return `<div class="meta">Nothing notable here yet.</div>`;
  }
  return `<div class="list">${items
    .map((item) => `<div class="list-item"><div class="item-title">${escapeHtml(item.title)}</div><div class="meta">${escapeHtml(item.meta)}</div></div>`)
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
    .force("link", d3.forceLink(graph.links).id((d) => d.id).distance(state.graphKind === "file" ? 110 : 60))
    .force("charge", d3.forceManyBody().strength(-140))
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
    .text((d) => truncate(d.label, state.graphKind === "file" ? 34 : 24))
    .attr("font-size", 11)
    .attr("fill", "#d8e1ff");

  simulation.on("tick", () => {
    links
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);

    nodes.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
    labels.attr("x", (d) => d.x + 10).attr("y", (d) => d.y + 4);
  });
}

async function showGraphNode(node) {
  const detailRoot = document.getElementById("node-details");
  if (state.graphKind === "file") {
    const file = state.report.files.find((item) => item.path === node.id);
    if (!file) return;
    detailRoot.innerHTML = `<div class="item-title">${escapeHtml(file.path)}</div><div class="meta">${escapeHtml(file.language)} · ${file.line_count} lines · ${file.imports.length} imports</div>`;
    const response = await fetch(`/api/reports/${state.reportId}/source?path=${encodeURIComponent(file.path)}`);
    const payload = await response.json();
    document.getElementById("source-viewer").textContent = payload.source || "";
    return;
  }

  const symbol = state.report.symbols.find((item) => item.id === node.id);
  if (!symbol) return;
  detailRoot.innerHTML = `<div class="item-title">${escapeHtml(symbol.qualified_name)}</div><div class="meta">${escapeHtml(symbol.kind)} · ${escapeHtml(symbol.file_path)}</div>`;
  document.getElementById("source-viewer").textContent = symbol.snippet || "";
}

function showNodeDetails(item) {
  document.getElementById("node-details").innerHTML = `<div class="item-title">${escapeHtml(item.name)}</div><div class="meta">${escapeHtml(item.explanation)}</div>`;
  document.getElementById("source-viewer").textContent = item.snippet || "";
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
