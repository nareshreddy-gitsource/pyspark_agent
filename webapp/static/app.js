// ================= Element refs =================
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const browseBtn = document.getElementById('browseBtn');
const queue = document.getElementById('queue');
const queueEmpty = document.getElementById('queueEmpty');
const jobTemplate = document.getElementById('jobTemplate');
const historyItemTemplate = document.getElementById('historyItemTemplate');
const toastStack = document.getElementById('toastStack');

const modalOverlay = document.getElementById('modalOverlay');
const modalClose = document.getElementById('modalClose');
const addScriptBtn = document.getElementById('addScriptBtn');
const addScriptBtnTop = document.getElementById('addScriptBtnTop');

const sidebar = document.getElementById('sidebar');
const sidebarScrim = document.getElementById('sidebarScrim');
const menuBtn = document.getElementById('menuBtn');
const sidebarClose = document.getElementById('sidebarClose');

const attemptsDown = document.getElementById('attemptsDown');
const attemptsUp = document.getElementById('attemptsUp');
const attemptsValue = document.getElementById('attemptsValue');

const historyList = document.getElementById('historyList');
const historyEmpty = document.getElementById('historyEmpty');
const historyCount = document.getElementById('historyCount');

const jobElements = {};
const jobTimers = {};
const historyElements = {};
const knownJobIds = new Set();

const TERMINAL_STATUSES = ['done', 'failed', 'stopped', 'needs_repair'];

// ================= Modal =================

function openModal() {
  modalOverlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeModal() {
  modalOverlay.classList.remove('open');
  document.body.style.overflow = '';
}
[addScriptBtn, addScriptBtnTop].forEach(btn => btn && btn.addEventListener('click', openModal));
modalClose.addEventListener('click', closeModal);
modalOverlay.addEventListener('click', (e) => { if (e.target === modalOverlay) closeModal(); });
window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && modalOverlay.classList.contains('open')) closeModal(); });

// ================= Sidebar (mobile) =================

function openSidebar() {
  sidebar.classList.add('open');
  sidebarScrim.classList.add('show');
}
function closeSidebar() {
  sidebar.classList.remove('open');
  sidebarScrim.classList.remove('show');
}
menuBtn.addEventListener('click', openSidebar);
sidebarClose.addEventListener('click', closeSidebar);
sidebarScrim.addEventListener('click', closeSidebar);

// ================= Attempts stepper =================

let currentMaxAttempts = parseInt(attemptsValue.textContent, 10) || 3;

function updateMaxAttempts(delta) {
  const next = Math.max(1, Math.min(10, currentMaxAttempts + delta));
  if (next === currentMaxAttempts) return;
  currentMaxAttempts = next;
  attemptsValue.textContent = next;
  fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_attempts: next }),
  }).catch(() => showToast('error', 'Could not save', 'Setting change did not reach the server.'));
}
attemptsDown.addEventListener('click', () => updateMaxAttempts(-1));
attemptsUp.addEventListener('click', () => updateMaxAttempts(1));

// ================= Toasts =================

const TOAST_ICONS = {
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01" stroke-linecap="round"/></svg>',
  success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12l2 2 4-4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="9"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 16v-5M12 8h.01" stroke-linecap="round"/></svg>',
};

function showToast(type, title, message, duration = 5000) {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${TOAST_ICONS[type] || TOAST_ICONS.info}</span>
    <div class="toast-body">
      <div class="toast-title"></div>
      <div class="toast-msg"></div>
    </div>
    <button class="toast-close" type="button" aria-label="Dismiss">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12" stroke-linecap="round"/></svg>
    </button>`;
  toast.querySelector('.toast-title').textContent = title;
  toast.querySelector('.toast-msg').textContent = message;

  const dismiss = () => { toast.classList.add('leaving'); setTimeout(() => toast.remove(), 200); };
  toast.querySelector('.toast-close').addEventListener('click', dismiss);

  toastStack.appendChild(toast);
  if (duration) setTimeout(dismiss, duration);
  return toast;
}

// ================= Drag & drop =================

['dragenter', 'dragover'].forEach(evt => {
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
});
['dragleave', 'drop'].forEach(evt => {
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove('dragover'); });
});
dropzone.addEventListener('drop', (e) => handleFiles(e.dataTransfer.files));
dropzone.addEventListener('click', (e) => { if (e.target !== browseBtn) fileInput.click(); });
browseBtn.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
fileInput.addEventListener('change', () => { handleFiles(fileInput.files); fileInput.value = ''; });

window.addEventListener('paste', (e) => {
  const files = e.clipboardData && e.clipboardData.files;
  if (files && files.length) handleFiles(files);
});

// ================= Upload flow =================

function handleFiles(fileList) {
  const allowed = ['.py', '.sql', '.r'];
  const files = Array.from(fileList);
  if (!files.length) return;

  closeModal();

  let rejected = 0;
  files.forEach(file => {
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!allowed.includes(ext)) { rejected++; return; }
    uploadFile(file);
  });

  if (rejected) {
    showToast('error', 'Unsupported file type', `Only .py, .sql, and .r files are supported. ${rejected} file(s) skipped.`);
  }
}

function uploadFile(file) {
  const formData = new FormData();
  formData.append('file', file);

  const tempId = 'pending-' + Math.random().toString(36).slice(2);
  const el = createJobCard(tempId, file.name);
  showQueue();
  queue.prepend(el);
  jobElements[tempId] = el;
  applyStatusClass(tempId, 'queued');
  setDetailText(tempId, 'Uploading…');
  addHistoryItem(tempId, file.name, 'queued');

  fetch('/upload', { method: 'POST', body: formData })
    .then(res => res.json().then(data => ({ ok: res.ok, data })))
    .then(({ ok, data }) => {
      if (!ok || data.error) {
        applyStatusClass(tempId, 'failed');
        setDetailText(tempId, data.error || 'Upload failed');
        showToast('error', 'Upload failed', data.error || `Could not upload ${file.name}`);
        return;
      }
      const realId = data.job_id;
      el.dataset.jobId = realId;
      jobElements[realId] = el;
      knownJobIds.add(realId);
      delete jobElements[tempId];
      renameHistoryItem(tempId, realId);
      wireJobActions(realId, el);
      startTimer(realId, el);
      pollStatus(realId, file.name);
    })
    .catch(err => {
      applyStatusClass(tempId, 'failed');
      setDetailText(tempId, 'Upload failed: ' + err.message);
      showToast('error', 'Connection problem', `Could not reach the server. Is it still running?`);
    });
}

function pollStatus(jobId, filename) {
  let consecutiveErrors = 0;
  const interval = setInterval(() => {
    fetch(`/status/${jobId}`)
      .then(res => { if (!res.ok) throw new Error(`Server responded ${res.status}`); return res.json(); })
      .then(job => {
        consecutiveErrors = 0;
        if (!job || (job.error && !job.status)) { clearInterval(interval); return; }
        const prevStatus = jobElements[jobId] ? jobElements[jobId].dataset.lastStatus : undefined;
        applyJobState(jobId, job);
        updateHistoryStatus(jobId, job.status);
        if (jobElements[jobId]) jobElements[jobId].dataset.lastStatus = job.status;

        if (job.status !== prevStatus) {
          if (job.status === 'done') {
            showToast('success', 'Conversion complete', `${filename} passed validation${job.attempts_used > 1 ? ` after ${job.attempts_used} attempts` : ''}.`);
          } else if (job.status === 'needs_repair') {
            showToast('error', 'Needs review', `${filename} didn't pass validation after ${job.max_attempts} attempts. You can still download and inspect it.`);
          } else if (job.status === 'stopped') {
            showToast('info', 'Stopped', `${filename} conversion was cancelled.`);
          }
        }

        if (TERMINAL_STATUSES.includes(job.status)) {
          clearInterval(interval);
          stopTimer(jobId);
        }
      })
      .catch(() => {
        consecutiveErrors++;
        if (consecutiveErrors >= 3) {
          clearInterval(interval);
          stopTimer(jobId);
          applyStatusClass(jobId, 'failed');
          setDetailText(jobId, 'Lost connection to server');
          showToast('error', 'Connection lost', `Stopped tracking ${filename} — the server may have stopped.`);
        }
      });
  }, 500);
}

// ================= DOM construction =================

function createJobCard(jobId, filename) {
  const frag = jobTemplate.content.cloneNode(true);
  const article = frag.querySelector('.job');
  article.dataset.jobId = jobId;
  article.querySelector('.job-filename').textContent = filename;
  article.querySelector('.job-filename').title = filename;
  const wrapper = document.createElement('div');
  wrapper.appendChild(frag);
  return wrapper.firstElementChild;
}

function wireJobActions(jobId, el) {
  const previewBtn = el.querySelector('.job-preview-btn');
  const previewPane = el.querySelector('.job-preview-pane');
  previewBtn.addEventListener('click', () => {
    previewPane.classList.toggle('open');
    previewBtn.textContent = previewPane.classList.contains('open') ? 'Hide code' : 'View code';
  });

  const stopBtn = el.querySelector('.job-stop-btn');
  stopBtn.addEventListener('click', () => {
    stopBtn.disabled = true;
    fetch(`/cancel/${jobId}`, { method: 'POST' })
      .catch(() => showToast('error', 'Could not stop', 'The stop request failed to reach the server.'))
      .finally(() => { stopBtn.disabled = false; });
  });

  const repairSubmit = el.querySelector('.job-repair-submit');
  const repairInput = el.querySelector('.job-repair-input');
  repairSubmit.addEventListener('click', () => {
    const errorText = repairInput.value.trim();
    if (!errorText) { repairInput.focus(); return; }
    repairSubmit.disabled = true;
    repairSubmit.textContent = 'Fixing…';

    const filename = el.querySelector('.job-filename').textContent;

    // Start polling immediately -- the POST below doesn't resolve until the
    // whole repair (generate + validate) finishes server-side, so waiting
    // for its response before polling would mean the graph never animates
    // during the fix, only jumping to the final state at the end.
    pollStatus(jobId, filename);

    fetch(`/report_error/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: errorText }),
    })
      .then(res => { if (!res.ok) throw new Error('Server error'); return res.json(); })
      .then(() => {
        repairSubmit.disabled = false;
        repairSubmit.textContent = 'Fix with this error';
        repairInput.value = '';
      })
      .catch(() => {
        repairSubmit.disabled = false;
        repairSubmit.textContent = 'Fix with this error';
        showToast('error', 'Repair failed', 'Could not send the error to the agent. Try again.');
      });
  });
}

// ================= Timer =================

function startTimer(jobId, el) {
  const elapsedEl = el.querySelector('.job-elapsed');
  const start = Date.now();
  const id = setInterval(() => {
    const secs = Math.floor((Date.now() - start) / 1000);
    const mins = Math.floor(secs / 60);
    const rem = secs % 60;
    elapsedEl.textContent = mins > 0 ? `${mins}m ${rem}s` : `${rem}s`;
  }, 1000);
  jobTimers[jobId] = { start, id };
}

function stopTimer(jobId) {
  const t = jobTimers[jobId];
  if (t) { clearInterval(t.id); delete jobTimers[jobId]; }
}

// ================= State application =================

const STATUS_TEXT_FALLBACK = {
  queued: 'Waiting in queue…',
  generating: 'Generating PySpark code…',
  validating: 'Checking generated code…',
  done: 'Conversion complete',
  failed: 'Conversion failed',
  needs_repair: 'Needs manual review',
  stopped: 'Stopped',
};

const STATUS_ICONS = {
  generating: '<svg class="spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a9 9 0 1 0 9 9" stroke-linecap="round"/></svg>',
  validating: '<svg class="spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a9 9 0 1 0 9 9" stroke-linecap="round"/></svg>',
  done: '<svg class="check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M9 12l2 2 4-4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="9"/></svg>',
  failed: '<svg class="cross" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6" stroke-linecap="round"/></svg>',
  needs_repair: '<svg class="cross" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01" stroke-linecap="round"/></svg>',
};

function applyJobState(jobId, job) {
  const el = jobElements[jobId];
  if (!el) return;

  applyStatusClass(jobId, job.status);

  const statusIconEl = el.querySelector('.job-status-icon');
  statusIconEl.innerHTML = STATUS_ICONS[job.status] || '';

  const detailText = el.querySelector('.job-detail-text');
  detailText.textContent = job.stage_detail || STATUS_TEXT_FALLBACK[job.status] || job.status;
  detailText.classList.remove('done', 'failed', 'stopped', 'needs_repair');
  if (TERMINAL_STATUSES.includes(job.status)) detailText.classList.add(job.status);

  if (job.source === 'inbox') {
    const badge = el.querySelector('.job-source-badge');
    badge.textContent = 'inbox';
    badge.classList.add('show');
  }

  if (job.max_attempts) {
    renderPipelineGraph(el, jobId, job);
    const badge = el.querySelector('.job-attempt-badge');
    if (job.attempt && job.max_attempts > 1) {
      badge.textContent = `attempt ${job.attempt}/${job.max_attempts}`;
      badge.classList.add('show');
    }
  }

  const livePreview = el.querySelector('.job-live-preview');
  if ((job.status === 'generating' || job.status === 'validating') && job.live_preview) {
    livePreview.textContent = job.live_preview;
    livePreview.scrollTop = livePreview.scrollHeight;
  }

  if (TERMINAL_STATUSES.includes(job.status) && job.output_code_filename) {
    el.querySelector('.job-download-btn').href = `/download/${jobId}`;
    el.querySelector('.job-download-nb-btn').href = `/download_notebook/${jobId}`;
    el.querySelector('.job-download-html-btn').href = `/download_html/${jobId}`;
    el.querySelector('.job-preview-pane').textContent = job.preview || '';
  }
}

// ================= Pipeline graph (Databricks-lineage style) =================
//
// Renders 4 nodes -- Read, Generate, Validate, Output -- as an SVG DAG.
// When a repair attempt is in progress (attempt > 1), a dashed red edge
// loops back from Validate to Generate to visualize the retry.

const NODE_ICONS = {
  read: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  generate: '<path d="M12 3a9 9 0 1 0 9 9"/>', // spinner arc reused as a "working" glyph; static when not active
  validate: '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  output: '<path d="M12 3v12m0 0l-4-4m4 4l4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
};

function nodeState(nodeKey, job) {
  const status = job.status;
  const attempt = job.attempt || 1;

  // "read" completes almost instantly once any attempt has started
  if (nodeKey === 'read') {
    return (status && status !== 'queued') ? 'passed' : 'pending';
  }

  if (nodeKey === 'generate') {
    if (status === 'generating') return 'active';
    if (status === 'validating' || status === 'done') return 'passed';
    if (status === 'needs_repair' || status === 'failed') return attempt > 1 ? 'passed' : 'failed';
    return 'pending';
  }

  if (nodeKey === 'validate') {
    if (status === 'validating') return 'active';
    if (status === 'done') return 'passed';
    if (status === 'needs_repair' || status === 'failed') return 'failed';
    return 'pending';
  }

  if (nodeKey === 'output') {
    if (status === 'done') return 'passed';
    return 'pending';
  }

  return 'pending';
}

function renderPipelineGraph(el, jobId, job) {
  const container = el.querySelector('.job-graph');
  const nodes = [
    { key: 'read', title: 'Read', subtitle: 'source file', icon: NODE_ICONS.read },
    { key: 'generate', title: 'Generate', subtitle: job.attempt ? `attempt ${job.attempt}` : 'pyspark code', icon: NODE_ICONS.generate },
    { key: 'validate', title: 'Validate', subtitle: 'ast.parse()', icon: NODE_ICONS.validate },
    { key: 'output', title: 'Output', subtitle: '.ipynb / .py', icon: NODE_ICONS.output },
  ];

  const states = nodes.map(n => nodeState(n.key, job));
  const showRetryLoop = (job.attempt || 1) > 1 && ['generating', 'validating', 'needs_repair'].includes(job.status);

  const boxW = 108, boxH = 54, gapX = 34, topPad = showRetryLoop ? 30 : 6;
  const svgW = nodes.length * boxW + (nodes.length - 1) * gapX + 4;
  const svgH = boxH + topPad + 6;

  let edges = '';
  let nodesHtml = '';

  nodes.forEach((n, i) => {
    const x = 2 + i * (boxW + gapX);
    const y = topPad;
    const state = states[i];
    const iconIsSpinning = state === 'active' && n.key === 'generate';

    nodesHtml += `
      <g class="graph-node ${state}" transform="translate(${x},${y})">
        <rect class="graph-node-box" width="${boxW}" height="${boxH}" rx="8"/>
        <rect class="graph-node-topbar" x="0" y="0" width="${boxW}" height="3" rx="1.5"/>
        <g class="graph-node-icon" transform="translate(10,12)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <g class="${iconIsSpinning ? 'spinner' : ''}" style="transform-origin:12px 12px;">${n.icon}</g>
          </svg>
        </g>
        <text class="graph-node-title" x="30" y="20">${n.title}</text>
        <text class="graph-node-subtitle" x="10" y="40">${n.subtitle}</text>
        <circle class="graph-dot d1" cx="12" cy="46" r="2.5"/>
      </g>`;

    if (i < nodes.length - 1) {
      const x1 = x + boxW, x2 = x + boxW + gapX;
      const yMid = y + boxH / 2;
      const edgeClass = (states[i] === 'passed') ? 'passed' : '';
      edges += `<path class="graph-edge ${edgeClass}" d="M${x1},${yMid} C${x1 + gapX / 2},${yMid} ${x2 - gapX / 2},${yMid} ${x2},${yMid}"/>`;
    }
  });

  if (showRetryLoop) {
    const genX = 2 + 1 * (boxW + gapX) + boxW / 2;
    const valX = 2 + 2 * (boxW + gapX) + boxW / 2;
    const topY = topPad - 14;
    edges += `<path class="graph-edge retry" d="M${valX},${topPad} C${valX},${topY} ${genX},${topY} ${genX},${topPad}" marker-end="url(#retryArrow)"/>`;
  }

  container.innerHTML = `
    <svg viewBox="0 0 ${svgW} ${svgH}" width="${svgW}" height="${svgH}">
      <defs>
        <marker id="retryArrow" markerWidth="7" markerHeight="7" refX="3.5" refY="3.5" orient="auto">
          <path d="M0,0 L7,3.5 L0,7 Z" fill="var(--err)"/>
        </marker>
      </defs>
      ${edges}
      ${nodesHtml}
    </svg>`;
}

function applyStatusClass(jobId, status) {
  const el = jobElements[jobId];
  if (!el || !status) return;
  el.classList.remove('queued', 'generating', 'validating', 'done', 'failed', 'stopped', 'needs_repair');
  el.classList.add(status);
}

function setDetailText(jobId, text) {
  const el = jobElements[jobId];
  if (!el) return;
  el.querySelector('.job-detail-text').textContent = text;
}

// ================= History (sidebar) =================

function addHistoryItem(jobId, filename, status) {
  historyEmpty.style.display = 'none';
  const frag = historyItemTemplate.content.cloneNode(true);
  const btn = frag.querySelector('.history-item');
  btn.querySelector('.history-item-name').textContent = filename;
  btn.querySelector('.history-item-name').title = filename;
  btn.querySelector('.history-item-icon').classList.add(status === 'queued' ? 'active' : status);
  btn.dataset.jobId = jobId;
  btn.addEventListener('click', () => {
    const target = jobElements[btn.dataset.jobId];
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.style.outline = '2px solid var(--accent)';
      setTimeout(() => { target.style.outline = ''; }, 1200);
    }
    closeSidebar();
  });
  historyList.prepend(btn);
  historyElements[jobId] = btn;
  updateHistoryCount();
}

function renameHistoryItem(oldId, newId) {
  const el = historyElements[oldId];
  if (!el) return;
  el.dataset.jobId = newId;
  historyElements[newId] = el;
  delete historyElements[oldId];
}

function updateHistoryStatus(jobId, status) {
  const el = historyElements[jobId];
  if (!el) return;
  const icon = el.querySelector('.history-item-icon');
  icon.classList.remove('active', 'done', 'failed', 'needs_repair');
  icon.classList.add(['generating', 'validating', 'queued'].includes(status) ? 'active' : status);
}

function updateHistoryCount() {
  const n = Object.keys(historyElements).length;
  historyCount.textContent = n ? String(n) : '';
}

// ================= Queue chrome =================

function showQueue() {
  queueEmpty.classList.remove('show');
}

if (!queue.children.length) queueEmpty.classList.add('show');

const origPrepend = queue.prepend.bind(queue);
queue.prepend = function(node) { origPrepend(node); showQueue(); };

// ================= Connectivity check =================

window.addEventListener('load', () => {
  fetch('/jobs').catch(() => {
    showToast('error', 'Server unreachable', 'Could not connect to the Spark Convert backend. Make sure python webapp/app.py is running.', 0);
  });
});

// ================= Discover jobs created outside the browser =================
// (e.g. files dropped into inbox/ and picked up by the folder watcher)

function discoverExternalJobs() {
  fetch('/jobs')
    .then(res => res.json())
    .then(jobs => {
      jobs.forEach(job => {
        if (knownJobIds.has(job.id) || jobElements[job.id]) return;
        if (job.source !== 'inbox') return;

        knownJobIds.add(job.id);
        const el = createJobCard(job.id, job.filename);
        showQueue();
        queue.prepend(el);
        jobElements[job.id] = el;
        wireJobActions(job.id, el);
        startTimer(job.id, el);
        addHistoryItem(job.id, job.filename, job.status || 'queued');
        applyJobState(job.id, job);
        updateHistoryStatus(job.id, job.status);

        if (!TERMINAL_STATUSES.includes(job.status)) {
          pollStatus(job.id, job.filename);
        } else {
          stopTimer(job.id);
        }

        showToast('info', 'Picked up from inbox', `${job.filename} was dropped in the inbox folder and is being converted.`);
      });
    })
    .catch(() => {});
}

setInterval(discoverExternalJobs, 2000);
discoverExternalJobs();
