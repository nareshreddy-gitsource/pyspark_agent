const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const browseBtn = document.getElementById('browseBtn');
const queue = document.getElementById('queue');
const queueEmpty = document.getElementById('queueEmpty');
const queueHeader = document.getElementById('queueHeader');
const queueCount = document.getElementById('queueCount');
const jobTemplate = document.getElementById('jobTemplate');
const toastStack = document.getElementById('toastStack');

const jobElements = {};
const jobTimers = {};
const jobMaxAttemptsSeen = {};

const TERMINAL_STATUSES = ['done', 'failed', 'stopped', 'needs_repair'];

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

  const dismiss = () => {
    toast.classList.add('leaving');
    setTimeout(() => toast.remove(), 200);
  };
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

// Also allow paste-to-upload for convenience (paste a .py/.sql file)
window.addEventListener('paste', (e) => {
  const files = e.clipboardData?.files;
  if (files && files.length) handleFiles(files);
});

// ================= Upload flow =================

function handleFiles(fileList) {
  const allowed = ['.py', '.sql', '.r'];
  const files = Array.from(fileList);
  if (!files.length) return;

  let rejected = 0;
  files.forEach(file => {
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!allowed.includes(ext)) {
      rejected++;
      return;
    }
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
      delete jobElements[tempId];
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
      .then(res => {
        if (!res.ok) throw new Error(`Server responded ${res.status}`);
        return res.json();
      })
      .then(job => {
        consecutiveErrors = 0;
        if (!job || (job.error && !job.status)) { clearInterval(interval); return; }
        const prevStatus = jobElements[jobId]?.dataset.lastStatus;
        applyJobState(jobId, job);
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
    fetch(`/report_error/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: errorText }),
    })
      .then(res => {
        if (!res.ok) throw new Error('Server error');
        return res.json();
      })
      .then(() => {
        repairSubmit.disabled = false;
        repairSubmit.textContent = 'Fix with this error';
        repairInput.value = '';
        pollStatus(jobId, el.querySelector('.job-filename').textContent);
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

  if (job.max_attempts) {
    renderAttemptTrack(el, jobId, job);
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

function renderAttemptTrack(el, jobId, job) {
  const track = el.querySelector('.job-attempt-track');
  const total = job.max_attempts || 1;

  if (jobMaxAttemptsSeen[jobId] !== total) {
    track.innerHTML = '';
    for (let i = 0; i < total; i++) {
      const seg = document.createElement('div');
      seg.className = 'attempt-segment';
      track.appendChild(seg);
    }
    jobMaxAttemptsSeen[jobId] = total;
  }

  const segments = track.querySelectorAll('.attempt-segment');
  const currentAttempt = job.attempt || 1;

  segments.forEach((seg, idx) => {
    seg.classList.remove('active', 'passed', 'failed');
    const segAttempt = idx + 1;
    if (segAttempt < currentAttempt) {
      seg.classList.add('passed');
    } else if (segAttempt === currentAttempt) {
      if (job.status === 'generating' || job.status === 'validating') {
        seg.classList.add('active');
      } else if (job.status === 'done') {
        seg.classList.add('passed');
      } else if (job.status === 'failed' || job.status === 'needs_repair') {
        seg.classList.add('failed');
      }
    }
  });
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

// ================= Queue chrome =================

function showQueue() {
  queueEmpty.style.display = 'none';
  queueHeader.style.display = 'flex';
  updateQueueCount();
}

function updateQueueCount() {
  const n = queue.children.length;
  queueCount.textContent = n === 1 ? '1 file' : `${n} files`;
}

const origPrepend = queue.prepend.bind(queue);
queue.prepend = function(node) { origPrepend(node); updateQueueCount(); };

// ================= Connectivity check =================
// Warn early if the backend seems unreachable at all (e.g. Ollama/Flask not running)
window.addEventListener('load', () => {
  fetch('/jobs').catch(() => {
    showToast('error', 'Server unreachable', 'Could not connect to the Spark Convert backend. Make sure python webapp/app.py is running.', 0);
  });
});
