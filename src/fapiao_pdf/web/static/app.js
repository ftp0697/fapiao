// fapiao SPA controller
const STATE = {
  current: "idle",
  taskId: null,
  files: [],
  pdfDpi: 200,
  eventSource: null,
  warnings: [],
  summary: "",
  error: "",
  queuePosition: null,
  progress: { current: 0, total: 0, key: "" },
  lastScrollFollow: true,
  abortCtrl: null,
  pollTimer: null,
  pollInFlight: false,
};

const ERROR_MESSAGES = {
  "failed-no-input": "未发现可处理的输入。请检查目录中是否包含 .jpg / .jpeg / .png / .pdf 文件。",
  "failed-ocr-missing": "OCR 模型未就绪，请联系部署管理员运行 `fapiao init`。",
  "failed-fatal": "处理失败：致命错误，请联系管理员。",
  "failed-internal": "内部错误，请稍后重试。若反复出现请联系管理员。",
  "failed-restart": "服务在处理过程中重启，任务未能完成。请重新提交。",
  "failed-upload": "上传失败，请稍后重试。",
  NoUploadedFiles: "请至少选择一个文件。",
  UploadTooLarge: "上传内容过大，请减少文件数量或大小。",
  TooManyFiles: "文件数量超出上限。",
  SingleFileTooLarge: "存在过大的单个文件。",
  InvalidPdfDpi: "PDF DPI 必须在 100 到 300 之间。",
  InsufficientStorage: "服务器存储不足。",
  TaskNotFound: "任务不存在或已被清理。",
  TaskExpired: "任务结果已过期。",
  TaskNotReady: "任务尚未完成。",
  TaskRunning: "任务正在运行，无法取消。",
  OcrModelMissing: "OCR 模型未就绪。",
  TooManyStreams: "并发订阅过多。",
};

const VALID_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".pdf"]);
const TERMINAL_STATES = new Set([
  "done",
  "failed-no-input",
  "failed-ocr-missing",
  "failed-fatal",
  "failed-internal",
  "failed-restart",
  "failed-upload",
]);

function errorCodeToMessage(code) {
  return ERROR_MESSAGES[code] || ERROR_MESSAGES["failed-internal"];
}

function normalizeFailedState(raw) {
  if (!raw) return "failed-internal";
  return raw.startsWith("failed-") ? raw : `failed-${raw}`;
}

function isTerminal(state) {
  return TERMINAL_STATES.has(state) || state.startsWith("failed-");
}

const TRANSITIONS = {
  idle: ["uploading", "queued"],
  uploading: ["queued", "failed-upload", "idle"],
  queued: ["queued", "running", "done", "idle"],
  running: ["running", "queued", "done", "idle"],
  done: ["idle"],
};

function transition(next, payload = {}) {
  const from = STATE.current;
  const allowed = TRANSITIONS[from] || [];
  const isFail = next.startsWith("failed-");
  if (!allowed.includes(next) && !isFail) {
    console.warn(`invalid transition: ${from} → ${next}`);
    return;
  }
  if (isTerminal(from) && next !== "idle") return;
  STATE.current = next;
  if (payload.taskId !== undefined) STATE.taskId = payload.taskId;
  if (payload.queuePosition !== undefined) STATE.queuePosition = payload.queuePosition;
  if (payload.progress) STATE.progress = payload.progress;
  if (payload.summary !== undefined) STATE.summary = formatSummary(payload.summary);
  if (payload.error !== undefined) STATE.error = payload.error;
  if (payload.warnings) STATE.warnings = payload.warnings.slice(-200);

  if ((next === "queued" || next === "running") && STATE.taskId && !STATE.eventSource) {
    subscribeEvents(STATE.taskId);
  }
  if (isTerminal(next) || next === "idle") {
    closeSSE();
    clearPoll();
  }
  render();
}

function formatSummary(s) {
  if (!s) return "";
  if (typeof s === "string") return s;
  const parts = [];
  if (s.processed != null) parts.push(`共处理 ${s.processed} 张`);
  if (s.invoices != null) parts.push(`发票 ${s.invoices}`);
  if (s.orders != null) parts.push(`订单 ${s.orders}`);
  if (s.ocr_failures != null) parts.push(`OCR 失败 ${s.ocr_failures}`);
  return parts.length > 0 ? parts.join("，") : JSON.stringify(s);
}

function closeSSE() {
  if (STATE.eventSource) {
    STATE.eventSource.close();
    STATE.eventSource = null;
  }
  if (STATE.abortCtrl) {
    try { STATE.abortCtrl.abort(); } catch {}
    STATE.abortCtrl = null;
  }
}

function clearPoll() {
  if (STATE.pollTimer) {
    clearInterval(STATE.pollTimer);
    STATE.pollTimer = null;
  }
  STATE.pollInFlight = false;
}

let rafId = 0;
function render() {
  cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(_renderFrame);
}

function _renderFrame() {
  const st = STATE.current;
  const isFailed = st.startsWith("failed-");
  const showCard = {
    "upload-card": st === "idle" || st === "uploading",
    "task-card": st === "queued" || st === "running",
    "result-card": st === "done",
    "error-card": isFailed,
    "warnings-panel": STATE.warnings.length > 0 && st !== "idle",
  };
  for (const [id, vis] of Object.entries(showCard)) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", !vis);
  }

  bind("position", STATE.queuePosition != null ? String(STATE.queuePosition) : "—");
  bind("progress-text", `${STATE.progress.current}/${STATE.progress.total}`);
  bind("key", STATE.progress.key || "—");
  bind("summary", STATE.summary);
  bind("error-message", STATE.error || (isFailed ? errorCodeToMessage(st) : ""));

  const progressEl = document.querySelector('[data-bind="progress"]');
  if (progressEl) {
    progressEl.value = STATE.progress.current;
    progressEl.max = STATE.progress.total || 100;
  }

  const badge = document.querySelector('[data-bind="state-label"]');
  if (badge) {
    const variantMap = { queued: "queued", running: "running", done: "done" };
    const variant = variantMap[st] || "failed";
    badge.className = `badge badge--${variant}`;
    const labels = { idle: "—", uploading: "上传中", queued: "排队中", running: "处理中", done: "完成" };
    badge.textContent = labels[st] || (isFailed ? "失败" : st);
  }

  bind("warning-count", String(STATE.warnings.length));
  bind("warning-count-total", String(STATE.warnings.length));

  const warnItems = STATE.warnings.slice(-50);
  const hidden = STATE.warnings.length - warnItems.length;
  for (const ul of document.querySelectorAll('[data-bind="warning-list"], [data-bind="warning-list-total"]')) {
    ul.replaceChildren(...warnItems.map(makeWarningLi));
    if (hidden > 0) {
      const li = document.createElement("li");
      li.className = "overflow";
      li.textContent = `…（${hidden} 条已隐藏）`;
      ul.appendChild(li);
    }
  }

  const fileUl = document.querySelector('[data-bind="file-list"]');
  if (fileUl) {
    fileUl.replaceChildren(...STATE.files.map((f) => {
      const li = document.createElement("li");
      li.textContent = f.name;
      return li;
    }));
  }

  const submitBtn = document.querySelector('[data-action="submit"]');
  if (submitBtn) submitBtn.disabled = STATE.files.length === 0 || st === "uploading";

  const downloadEl = document.querySelector('[data-action="download"]');
  if (downloadEl && STATE.taskId && st === "done") {
    downloadEl.href = `/api/tasks/${encodeURIComponent(STATE.taskId)}/result`;
  }

  scrollWarnings();
}

function makeWarningLi(text) {
  const li = document.createElement("li");
  li.textContent = text;
  li.title = text;
  return li;
}

function bind(key, value) {
  for (const el of document.querySelectorAll(`[data-bind="${key}"]`)) {
    if (el.tagName === "INPUT") el.value = value;
    else el.textContent = value;
  }
}

function scrollWarnings() {
  if (!STATE.lastScrollFollow) return;
  for (const ul of document.querySelectorAll('[data-bind="warning-list"], [data-bind="warning-list-total"]')) {
    ul.scrollTop = ul.scrollHeight;
  }
}

function setupDropzone() {
  const dz = document.querySelector(".dropzone");
  const fi = document.getElementById("file-input");
  if (!dz || !fi) return;
  dz.addEventListener("dragenter", (e) => { e.preventDefault(); dz.setAttribute("data-drag-over", "true"); });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.setAttribute("data-drag-over", "true"); });
  dz.addEventListener("dragleave", () => dz.removeAttribute("data-drag-over"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.removeAttribute("data-drag-over");
    addFiles(e.dataTransfer.files);
  });
  dz.addEventListener("click", () => fi.click());
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fi.click(); }
  });
  fi.addEventListener("change", () => { addFiles(fi.files); fi.value = ""; });
}

function addFiles(fileList) {
  let rejected = 0;
  const accepted = [];
  for (const f of fileList) {
    const dot = f.name.lastIndexOf(".");
    const ext = dot >= 0 ? f.name.slice(dot).toLowerCase() : "";
    if (VALID_EXTENSIONS.has(ext)) accepted.push(f);
    else rejected++;
  }
  STATE.files = [...STATE.files, ...accepted];
  if (rejected > 0) {
    STATE.warnings.push(`${rejected} 个文件因格式不支持被跳过`);
  }
  render();
}

async function submitUpload() {
  if (STATE.files.length === 0 || STATE.current === "uploading") return;
  transition("uploading");
  const fd = new FormData();
  for (const f of STATE.files) fd.append("files", f);
  fd.append("pdf_dpi", String(STATE.pdfDpi));
  STATE.abortCtrl = new AbortController();
  try {
    const res = await fetch("/api/tasks", { method: "POST", body: fd, signal: STATE.abortCtrl.signal });
    if (res.status === 202) {
      const data = await res.json();
      STATE.current = "idle";
      transition("queued", { taskId: data.task_id, queuePosition: data.queue_position ?? 0 });
      history.replaceState(null, "", `?task=${encodeURIComponent(data.task_id)}`);
    } else {
      let msg;
      try { const j = await res.json(); msg = errorCodeToMessage(j.error); } catch { msg = errorCodeToMessage("failed-upload"); }
      transition("failed-upload", { error: msg });
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      transition("failed-upload", { error: errorCodeToMessage("failed-upload") });
    }
  } finally {
    STATE.abortCtrl = null;
  }
}

function subscribeEvents(taskId) {
  closeSSE();
  clearPoll();
  const url = `/api/tasks/${encodeURIComponent(taskId)}/events`;
  const es = new EventSource(url);
  STATE.eventSource = es;

  es.addEventListener("queued", (e) => {
    const p = parsePayload(e);
    transition("queued", { taskId, queuePosition: p.queue_position });
  });
  es.addEventListener("progress", (e) => {
    const p = parsePayload(e);
    transition("running", { taskId, progress: p.progress });
  });
  es.addEventListener("warning", (e) => {
    const p = parsePayload(e);
    if (p.message) {
      STATE.warnings.push(p.message);
      if (STATE.warnings.length > 200) STATE.warnings = STATE.warnings.slice(-200);
    }
    if (Array.isArray(p.warnings)) {
      STATE.warnings = p.warnings.slice(-200);
    }
    render();
  });
  es.addEventListener("done", (e) => {
    const p = parsePayload(e);
    transition("done", { taskId, summary: p.summary });
  });
  es.addEventListener("error", (e) => {
    if (!e.data) return;
    const p = parsePayload(e);
    const next = normalizeFailedState(p.state);
    transition(next, { error: p.error || errorCodeToMessage(next) });
  });
  es.addEventListener("expired", () => {
    transition("failed-restart", { error: ERROR_MESSAGES["failed-restart"] });
  });
  es.addEventListener("heartbeat", () => {});

  es.onerror = () => {
    if (isTerminal(STATE.current)) return;
    closeSSE();
    startPolling(taskId);
  };
}

function parsePayload(e) {
  try { return JSON.parse(e.data); } catch { return {}; }
}

function startPolling(taskId) {
  clearPoll();
  STATE.pollTimer = setInterval(async () => {
    if (STATE.pollInFlight || STATE.taskId !== taskId) return;
    STATE.pollInFlight = true;
    try {
      const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
      if (STATE.taskId !== taskId) return;
      if (res.status === 404 || res.status === 410) {
        transition("failed-internal", { error: ERROR_MESSAGES.TaskNotFound });
        return;
      }
      if (!res.ok) return;
      const snap = await res.json();
      if (STATE.taskId !== taskId) return;
      applySnapshot(snap);
    } catch {} finally {
      STATE.pollInFlight = false;
    }
  }, 1500);
}

function applySnapshot(snap) {
  if (!snap || !snap.state) return;
  const s = snap.state;
  const taskId = snap.task_id || STATE.taskId;
  if (s === "queued") transition("queued", { taskId, queuePosition: snap.queue_position });
  else if (s === "running") transition("running", { taskId, progress: snap.progress });
  else if (s === "done") transition("done", { taskId, summary: snap.summary });
  else if (s.startsWith("failed-")) transition(s, { error: snap.error || errorCodeToMessage(s) });
  if (Array.isArray(snap.warnings) && snap.warnings.length > STATE.warnings.length) {
    STATE.warnings = snap.warnings.slice(-200);
  }
}

async function cancelTask() {
  if (!STATE.taskId) return;
  try {
    const res = await fetch(`/api/tasks/${encodeURIComponent(STATE.taskId)}`, { method: "DELETE" });
    if (res.status === 409) {
      let msg = ERROR_MESSAGES.TaskRunning;
      try { const j = await res.json(); msg = errorCodeToMessage(j.error) || msg; } catch {}
      STATE.error = msg;
      render();
      return;
    }
  } catch {}
  resetSession();
}

function triggerDownload(srcEvent) {
  if (!STATE.taskId) return;
  const a = document.querySelector('[data-action="download"]');
  if (!a) return;
  a.href = `/api/tasks/${encodeURIComponent(STATE.taskId)}/result`;
  if (srcEvent && srcEvent.target.closest('[data-action="download"]')) return;
  a.click();
}

function resetSession() {
  closeSSE();
  clearPoll();
  STATE.taskId = null;
  STATE.files = [];
  STATE.warnings = [];
  STATE.summary = "";
  STATE.error = "";
  STATE.queuePosition = null;
  STATE.progress = { current: 0, total: 0, key: "" };
  STATE.lastScrollFollow = true;
  STATE.current = "idle";
  history.replaceState(null, "", location.pathname);
  render();
}

function setupScrollFollow() {
  for (const ul of document.querySelectorAll('[data-bind="warning-list"], [data-bind="warning-list-total"]')) {
    ul.addEventListener("scroll", () => {
      STATE.lastScrollFollow = ul.scrollTop + ul.clientHeight >= ul.scrollHeight - 4;
    });
  }
}

function init() {
  setupDropzone();
  const form = document.getElementById("upload-form");
  if (form) form.addEventListener("submit", (e) => { e.preventDefault(); submitUpload(); });
  const dpiInput = document.querySelector('input[name="pdf_dpi"]');
  if (dpiInput) dpiInput.addEventListener("change", () => {
    const v = parseInt(dpiInput.value, 10);
    STATE.pdfDpi = Number.isFinite(v) ? Math.min(300, Math.max(100, v)) : 200;
    dpiInput.value = STATE.pdfDpi;
  });
  document.addEventListener("click", (e) => {
    const action = e.target.closest("[data-action]");
    if (!action) return;
    const act = action.dataset.action;
    if (act === "cancel") cancelTask();
    else if (act === "download") triggerDownload(e);
    else if (act === "reset") resetSession();
  });
  window.addEventListener("pageshow", () => {
    if (STATE.taskId) history.replaceState(null, "", `?task=${encodeURIComponent(STATE.taskId)}`);
  });
  window.addEventListener("beforeunload", () => {
    if (STATE.taskId) history.replaceState(null, "", `?task=${encodeURIComponent(STATE.taskId)}`);
  });
  setupScrollFollow();
  const urlTask = new URLSearchParams(location.search).get("task");
  if (urlTask) {
    STATE.taskId = urlTask;
    transition("queued", { taskId: urlTask });
  } else {
    transition("idle");
  }
}

init();
