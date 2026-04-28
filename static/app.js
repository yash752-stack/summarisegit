const state = {
  reportId: null,
  report: null,
  graphKind: 'file',
  architectureAudience: 'engineer',
  lastDiff: '',
};

const analyzeForm = document.getElementById('analyze-form');
const impactForm = document.getElementById('impact-form');
const flowForm = document.getElementById('flow-form');
const diffForm = document.getElementById('diff-form');
const askForm = document.getElementById('ask-form');
const statusText = document.getElementById('status-text');

boot();

function boot() {
  analyzeForm.addEventListener('submit', handleAnalyze);
  impactForm.addEventListener('submit', handleImpact);
  flowForm.addEventListener('submit', handleFlow);
  diffForm.addEventListener('submit', handleDiff);
  askForm.addEventListener('submit', handleAskRepo);

  document.querySelectorAll('[data-tab]').forEach((button) => {
    button.addEventListener('click', () => setActiveTab(button.dataset.tab));
  });

  document.querySelectorAll('.graph-kind').forEach((button) => {
    button.addEventListener('click', async () => {
      document.querySelectorAll('.graph-kind').forEach((item) => item.classList.remove('active'));
      button.classList.add('active');
      state.graphKind = button.dataset.graphKind;
      if (state.reportId) {
        await drawGraph();
      }
    });
  });

  document.getElementById('engineer-view').addEventListener('click', async () => {
    state.architectureAudience = 'engineer';
    if (state.reportId) {
      await renderArchitecture();
    }
  });

  document.getElementById('newcomer-view').addEventListener('click', async () => {
    state.architectureAudience = 'newcomer';
    if (state.reportId) {
      await renderArchitecture();
    }
  });

  document.querySelectorAll('.export-trigger').forEach((button) => {
    button.addEventListener('click', () => handleExport(button.dataset.exportKind));
  });
}

async function handleAnalyze(event) {
  event.preventDefault();
  statusText.textContent = 'Analyzing repository...';

  const formData = new FormData();
  appendIfPresent(formData, 'local_path', document.getElementById('local-path').value.trim());
  appendIfPresent(formData, 'repo_url', document.getElementById('repo-url').value.trim());
  appendIfPresent(formData, 'branch', document.getElementById('branch').value.trim());
  appendIfPresent(formData, 'extensions', document.getElementById('extensions').value.trim());
  appendIfPresent(formData, 'exclude_dirs', document.getElementById('exclude-dirs').value.trim());

  const zipFile = document.getElementById('repo-zip').files[0];
  if (zipFile) {
    formData.append('upload', zipFile);
  }

  try {
    const response = await fetch('/api/analyze', { method: 'POST', body: formData });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || 'Analysis failed');
    }

    state.reportId = payload.report_id;
    state.architectureAudience = 'engineer';
    await loadReport();
    statusText.textContent = `Ready: ${payload.repo_name}`;
    setActiveTab('summary');
  } catch (error) {
    statusText.textContent = `Error: ${error.message}`;
  }
}

async function loadReport() {
  const response = await fetch(`/api/reports/${state.reportId}`);
  state.report = await response.json();
  renderOverview();
  renderRepoMap();
  await renderArchitecture();
  renderFunctionCards();
  renderImprovements();
  renderContextModes();
  await drawGraph();
}

function renderOverview() {
  const summary = state.report.summary;
  const health = state.report.health;

  document.getElementById('summary-cards').innerHTML = [
    ['Files', summary.total_files],
    ['Functions', summary.total_functions],
    ['Classes', summary.total_classes],
    ['Chunks', summary.total_chunks],
  ].map(renderStatCard).join('');

  document.getElementById('health-cards').innerHTML = [
    ['Architecture Health', `${health.architecture_health}/100`],
    ['Maintainability', health.maintainability],
    ['Risk Hotspots', health.risk_hotspots],
    ['Missing Tests', health.missing_tests],
  ].map(renderStatCard).join('');

  document.getElementById('tech-stack').innerHTML = (summary.tech_stack || [])
    .map((item) => `<span class="pill">${escapeHtml(item)}</span>`)
    .join('');

  document.getElementById('metrics-list').innerHTML = createListMarkup(
    Object.entries(state.report.metrics || {}).map(([key, value]) => ({
      title: titleCase(key.replaceAll('_', ' ')),
      meta: String(value),
    }))
  );

  document.getElementById('folder-summaries').innerHTML = createListMarkup(
    (state.report.folder_summaries || []).map((item) => ({ title: item.folder, meta: item.summary }))
  );

  document.getElementById('biggest-files').innerHTML = createListMarkup(
    (summary.biggest_files || []).map((item) => ({
      title: item.path,
      meta: `${item.line_count} lines · ${item.imports} imports`,
    }))
  );

  document.getElementById('most-used-functions').innerHTML = createListMarkup(
    (summary.most_used_functions || []).map((item) => ({
      title: item.name,
      meta: `${item.file_path} · ${item.call_count} inbound calls`,
    }))
  );

  document.getElementById('block-diagram').textContent = state.report.diagrams.block_diagram || '';
  document.getElementById('mermaid-diagram').textContent = state.report.diagrams.pipeline_mermaid || '';
  document.getElementById('claude-mode').textContent = state.report.context_modes.claude || '';
  document.getElementById('suggested-structure').textContent = state.report.improvements.suggested_structure || '';
}

function renderRepoMap() {
  const root = document.getElementById('repo-map');
  root.innerHTML = '';

  (state.report.repo_map || []).forEach((category) => {
    const block = document.createElement('div');
    block.className = 'repo-category';
    block.innerHTML = `<div class="repo-category-title">${escapeHtml(category.name)}</div>`;

    category.files.forEach((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'repo-item';
      button.innerHTML = `
        <span class="repo-item-path">${escapeHtml(item.path)}</span>
        <span class="repo-item-meta">${escapeHtml(item.responsibilities.join(' · '))}</span>
      `;
      button.addEventListener('click', () => showFileDetails(item.path));
      block.appendChild(button);
    });

    root.appendChild(block);
  });
}

async function renderArchitecture() {
  const mode = state.architectureAudience === 'newcomer' ? 'newcomer' : 'architecture';
  const response = await fetch(`/api/reports/${state.reportId}/explain?mode=${mode}&audience=${state.architectureAudience}`);
  const architecture = await response.json();
  document.getElementById('architecture').innerHTML = `
    <p>${escapeHtml(architecture.headline || '')}</p>
    <p class="meta">${escapeHtml(architecture.narrative || '')}</p>
    <div class="pill-row">${(architecture.directories || [])
      .map((item) => `<span class="pill">${escapeHtml(item.directory)}: ${item.files}</span>`)
      .join('')}</div>
    <div class="list compact-list">${(architecture.central_files || [])
      .map(
        (item) => `
          <div class="list-item clickable" data-file-target="${escapeAttr(item.path)}">
            <div class="item-title">${escapeHtml(item.path)}</div>
            <div class="meta">fan-in ${item.fan_in} · fan-out ${item.fan_out}</div>
          </div>
        `
      )
      .join('')}</div>
  `;

  document.querySelectorAll('[data-file-target]').forEach((node) => {
    node.addEventListener('click', () => showFileDetails(node.dataset.fileTarget));
  });
}

function renderFunctionCards() {
  const root = document.getElementById('function-cards');
  root.innerHTML = '';
  (state.report.function_cards || []).forEach((card) => {
    const element = document.createElement('button');
    element.type = 'button';
    element.className = 'function-card';
    element.innerHTML = `
      <div class="function-card-top">
        <div>
          <div class="item-title">${escapeHtml(card.function)}</div>
          <div class="meta">${escapeHtml(card.file_path)}</div>
        </div>
        <span class="risk-badge risk-${card.risk_label.toLowerCase()}">${escapeHtml(card.risk_label)} · ${card.risk_score}</span>
      </div>
      <div class="meta">${escapeHtml(card.purpose)}</div>
      <div class="meta">Inputs: ${escapeHtml(card.inputs.join(', '))}</div>
      <div class="meta">Calls: ${escapeHtml((card.calls || []).join(', ') || 'No resolved callees')}</div>
    `;
    element.addEventListener('click', () => showSymbolDetails(card.function));
    root.appendChild(element);
  });
}

function renderImprovements() {
  const suggestionsRoot = document.getElementById('architecture-suggestions');
  suggestionsRoot.innerHTML = createListMarkup(
    (state.report.improvements.architecture_suggestions || []).map((item) => ({
      title: item.current_issue,
      meta: item.suggestion,
    }))
  );

  const sections = state.report.improvements.sections || {};
  const root = document.getElementById('improvement-buckets');
  root.innerHTML = '';

  Object.entries(sections).forEach(([key, items]) => {
    if (!items.length) return;
    const section = document.createElement('div');
    section.className = 'subpanel';
    section.innerHTML = `
      <h3>${escapeHtml(titleCase(key.replaceAll('_', ' ')))}</h3>
      ${createListMarkup(
        items.slice(0, 6).map((item) => ({
          title: item.target || item.name || 'Finding',
          meta: item.reason || item.explanation || JSON.stringify(item),
        }))
      )}
    `;
    root.appendChild(section);
  });
}

function renderContextModes() {
  const root = document.getElementById('context-modes');
  root.innerHTML = '';
  Object.entries(state.report.context_modes || {}).forEach(([key, value]) => {
    const section = document.createElement('div');
    section.className = 'subpanel';
    section.innerHTML = `
      <h3>${escapeHtml(titleCase(key))}</h3>
      <pre class="code-view">${escapeHtml(value)}</pre>
    `;
    root.appendChild(section);
  });
}

async function handleImpact(event) {
  event.preventDefault();
  if (!state.reportId) return;

  const target = document.getElementById('impact-target').value.trim();
  const response = await fetch(`/api/reports/${state.reportId}/impact?target=${encodeURIComponent(target)}`);
  const payload = await response.json();
  const root = document.getElementById('impact-output');
  if (!response.ok) {
    root.innerHTML = `<div class="impact-card warning">${escapeHtml(payload.detail)}</div>`;
    return;
  }

  root.innerHTML = `
    <div class="impact-card">
      <div class="item-title">${escapeHtml(payload.target)}</div>
      <div class="risk-score">${payload.risk_score}</div>
      <div class="meta">${escapeHtml(payload.explanation)}</div>
      <div class="meta">Affected files: ${escapeHtml((payload.affected_files || []).join(', ') || 'None')}</div>
      <div class="meta">Suggested tests: ${escapeHtml((payload.suggested_tests || []).join(', ') || 'No matching tests found')}</div>
    </div>
  `;
}

async function handleFlow(event) {
  event.preventDefault();
  if (!state.reportId) return;

  const target = document.getElementById('flow-target').value.trim();
  const response = await fetch(`/api/reports/${state.reportId}/flow?target=${encodeURIComponent(target)}`);
  const payload = await response.json();
  const root = document.getElementById('flow-output');
  if (!response.ok) {
    root.innerHTML = `<div class="impact-card warning">${escapeHtml(payload.detail)}</div>`;
    return;
  }

  root.innerHTML = `
    <div class="impact-card">
      <div class="item-title">${escapeHtml(payload.target)}</div>
      <div class="meta">${escapeHtml(payload.narrative)}</div>
      <pre class="code-view">${escapeHtml(payload.mermaid || '')}</pre>
      <div class="list">${(payload.steps || [])
        .map(
          (step) => `
            <div class="list-item">
              <div class="item-title">Depth ${step.depth}: ${escapeHtml(step.symbol)}</div>
              <div class="meta">${escapeHtml(step.file_path)} · calls ${escapeHtml((step.calls || []).join(', ') || 'none')}</div>
            </div>
          `
        )
        .join('')}</div>
    </div>
  `;
}

async function handleDiff(event) {
  event.preventDefault();
  if (!state.reportId) return;

  const diffText = document.getElementById('diff-text').value;
  state.lastDiff = diffText;
  const response = await fetch(`/api/reports/${state.reportId}/review-diff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ diff_text: diffText }),
  });
  const payload = await response.json();
  const root = document.getElementById('diff-output');
  if (!response.ok) {
    root.innerHTML = `<div class="impact-card warning">${escapeHtml(payload.detail)}</div>`;
    return;
  }

  root.innerHTML = `
    <div class="impact-card">
      <div class="item-title">Diff Summary</div>
      <div class="meta">${escapeHtml(payload.summary)}</div>
      <div class="meta">Touched files: ${escapeHtml((payload.touched_files || []).join(', ') || 'None detected')}</div>
    </div>
    <div class="list">${(payload.risk_areas || [])
      .map(
        (item) => `
          <div class="list-item">
            <div class="item-title">${escapeHtml(item.target)}</div>
            <div class="meta">risk ${item.risk_score} · ${escapeHtml(item.explanation)}</div>
          </div>
        `
      )
      .join('')}</div>
  `;
}

async function handleAskRepo(event) {
  event.preventDefault();
  if (!state.reportId) return;

  const question = document.getElementById('ask-query').value.trim();
  const response = await fetch(`/api/reports/${state.reportId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  const payload = await response.json();
  const root = document.getElementById('ask-output');
  if (!response.ok) {
    root.innerHTML = `<div class="impact-card warning">${escapeHtml(payload.detail)}</div>`;
    return;
  }

  root.innerHTML = `
    <div class="impact-card">
      <div class="item-title">${escapeHtml(titleCase(payload.mode || 'answer'))}</div>
      <div class="meta">${escapeHtml(payload.answer || '')}</div>
      ${payload.mermaid ? `<pre class="code-view">${escapeHtml(payload.mermaid)}</pre>` : ''}
      <div class="subpanel">
        <h3>Evidence</h3>
        ${createListMarkup((payload.evidence || []).map((item) => ({ title: item, meta: '' })))}
      </div>
      <div class="subpanel">
        <h3>Next Steps</h3>
        ${createListMarkup((payload.next_steps || []).map((item) => ({ title: item, meta: '' })))}
      </div>
    </div>
  `;
}

async function handleExport(kind) {
  if (!state.reportId) {
    statusText.textContent = 'Analyze a repo before exporting.';
    return;
  }

  statusText.textContent = `Preparing ${kind} export...`;
  try {
    const response = await fetch(`/api/reports/${state.reportId}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, diff_text: state.lastDiff || '' }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || 'Export failed');
    }

    (payload.files || []).forEach((file) => downloadTextFile(file.name, file.content, file.content_type));
    statusText.textContent = `Downloaded ${payload.files.length} file(s) for ${kind}.`;
  } catch (error) {
    statusText.textContent = `Export error: ${error.message}`;
  }
}

async function drawGraph() {
  const response = await fetch(`/api/reports/${state.reportId}/graph?kind=${state.graphKind}`);
  const graph = await response.json();
  const svg = d3.select('#graph-canvas');
  svg.selectAll('*').remove();

  const width = 900;
  const height = 500;
  const simulation = d3
    .forceSimulation(graph.nodes)
    .force('link', d3.forceLink(graph.links).id((d) => d.id).distance(state.graphKind === 'file' ? 110 : 70))
    .force('charge', d3.forceManyBody().strength(-160))
    .force('center', d3.forceCenter(width / 2, height / 2));

  const links = svg
    .append('g')
    .attr('stroke', 'rgba(255,255,255,0.18)')
    .selectAll('line')
    .data(graph.links)
    .join('line');

  const nodes = svg
    .append('g')
    .selectAll('circle')
    .data(graph.nodes)
    .join('circle')
    .attr('r', (d) => Math.max(5, Math.min(22, d.size / 2.1)))
    .attr('fill', (d) => (d.kind === 'file' ? '#8b7dff' : '#4de2c5'))
    .call(drag(simulation))
    .on('click', (_, node) => {
      if (state.graphKind === 'file') {
        showFileDetails(node.id);
      } else {
        showSymbolDetails(node.label);
      }
    });

  const labels = svg
    .append('g')
    .selectAll('text')
    .data(graph.nodes)
    .join('text')
    .text((d) => truncate(d.label, state.graphKind === 'file' ? 34 : 28))
    .attr('font-size', 11)
    .attr('fill', '#d8e1ff');

  simulation.on('tick', () => {
    links.attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y).attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
    nodes.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
    labels.attr('x', (d) => d.x + 10).attr('y', (d) => d.y + 4);
  });
}

async function showFileDetails(filePath) {
  if (!state.reportId) return;

  const explainResponse = await fetch(`/api/reports/${state.reportId}/explain?mode=file&target=${encodeURIComponent(filePath)}`);
  const explainPayload = await explainResponse.json();
  const sourceResponse = await fetch(`/api/reports/${state.reportId}/source?path=${encodeURIComponent(filePath)}`);
  const sourcePayload = await sourceResponse.json();

  document.getElementById('node-details').innerHTML = `
    <div class="item-title">${escapeHtml(explainPayload.target)}</div>
    <div class="meta">${escapeHtml(explainPayload.summary)}</div>
    <div class="meta">${escapeHtml((explainPayload.responsibilities || []).join(' · ') || '')}</div>
  `;

  renderConnections([
    { title: 'Imports', items: explainPayload.imports || [] },
    { title: 'Dependents', items: explainPayload.dependents || [] },
  ]);

  document.getElementById('source-viewer').textContent = sourcePayload.source || '';
}

async function showSymbolDetails(symbolName) {
  if (!state.reportId) return;

  const response = await fetch(`/api/reports/${state.reportId}/explain?mode=symbol&target=${encodeURIComponent(symbolName)}`);
  const payload = await response.json();
  document.getElementById('node-details').innerHTML = `
    <div class="item-title">${escapeHtml(payload.target)}</div>
    <div class="meta">${escapeHtml(payload.summary)}</div>
    ${payload.card ? `
      <div class="meta">Inputs: ${escapeHtml((payload.card.inputs || []).join(', '))}</div>
      <div class="meta">Output: ${escapeHtml(payload.card.output || '')}</div>
      <div class="meta">Risk: ${escapeHtml(payload.card.risk_label)} · ${payload.card.risk_score}</div>
      <div class="meta">${escapeHtml(payload.card.why_risky || '')}</div>
    ` : ''}
  `;

  renderConnections([
    { title: 'Calls', items: payload.calls || [] },
    { title: 'Callers', items: payload.callers || [] },
  ]);

  document.getElementById('source-viewer').textContent = payload.snippet || '';
}

function renderConnections(groups) {
  const root = document.getElementById('node-connections');
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
          <div class="pill-row">${group.items.map((item) => `<span class="pill">${escapeHtml(item)}</span>`).join('')}</div>
        </div>
      `
    )
    .join('');
}

function setActiveTab(tabName) {
  document.querySelectorAll('[data-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === tabName);
  });
  document.querySelectorAll('[data-panel]').forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.panel === tabName);
  });
}

function renderStatCard([label, value]) {
  return `<div class="summary-card"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(String(value))}</div></div>`;
}

function createListMarkup(items) {
  if (!items.length) {
    return `<div class="meta">Nothing notable here yet.</div>`;
  }
  return `<div class="list">${items
    .map(
      (item) => `
        <div class="list-item">
          <div class="item-title">${escapeHtml(item.title || '')}</div>
          ${item.meta ? `<div class="meta">${escapeHtml(item.meta)}</div>` : ''}
        </div>
      `
    )
    .join('')}</div>`;
}

function appendIfPresent(formData, key, value) {
  if (value) {
    formData.append(key, value);
  }
}

function titleCase(text) {
  return String(text)
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function truncate(text, maxLength) {
  return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function escapeAttr(text) {
  return escapeHtml(text).replaceAll('"', '&quot;');
}

function downloadTextFile(filename, content, contentType) {
  const blob = new Blob([content], { type: contentType || 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
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

  return d3.drag().on('start', dragstarted).on('drag', dragged).on('end', dragended);
}
