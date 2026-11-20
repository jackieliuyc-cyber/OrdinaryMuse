"use strict";
// One audio clock schedules every stem, including after pause/seek/solo changes.
class StemMixer {
  constructor(tracks, onChange = () => {}) {
    this.tracks = tracks.map(track => ({...track, volume: .88, muted: false, buffer: null, gain: null}));
    this.onChange = onChange;
    this.context = null; this.sources = []; this.offset = 0; this.when = 0;
    this.duration = 0; this.playing = false; this.loading = false; this.loaded = 0;
    this.solo = null; this.disposed = false; this.intent = 0; this.run = 0;
  }
  notify() { if (!this.disposed) this.onChange(this); }
  position() {
    return Math.min(this.duration, this.offset + (this.playing ? Math.max(0, this.context.currentTime - this.when) : 0));
  }
  graph() {
    if (this.context) return;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) throw new Error("当前浏览器不支持同步混音，请使用新版 Safari、Chrome 或 Edge。");
    this.context = new Audio();
    this.master = this.context.createGain();
    const limiter = this.context.createDynamicsCompressor();
    limiter.threshold.value = -1; limiter.knee.value = 0; limiter.ratio.value = 20;
    limiter.attack.value = .003; limiter.release.value = .1;
    this.master.connect(limiter); limiter.connect(this.context.destination);
    for (const track of this.tracks) {
      track.gain = this.context.createGain(); track.gain.connect(this.master);
    }
    this.updateGains();
  }
  async prepare() {
    if (this.pending) return this.pending;
    if (this.tracks.every(track => track.buffer)) return;
    this.loading = true; this.loaded = this.tracks.filter(track => track.buffer).length;
    const controller = this.controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    this.notify();
    this.pending = Promise.allSettled(this.tracks.map(async track => {
      if (track.buffer) return track.buffer;
      try {
        const response = await fetch(track.url, {signal: controller.signal, cache: "no-store"});
        if (!response.ok) throw new Error(response.status === 410 ? "音轨已过期，请重新分轨。" : `音轨加载失败 (${response.status})，请重试。`);
        const buffer = await this.context.decodeAudioData(await response.arrayBuffer());
        if (!this.disposed && !controller.signal.aborted) { this.loaded++; this.notify(); }
        return buffer;
      } catch (error) { controller.abort(); throw error; }
    })).then(results => {
      const errors = results.filter(result => result.status === "rejected").map(result => result.reason);
      if (errors.length) throw errors.find(error => error.name !== "AbortError") || errors[0];
      if (!this.disposed && controller.signal.aborted) throw Object.assign(new Error("音轨加载已中断"), {name: "AbortError"});
      if (!this.disposed) {
        this.tracks.forEach((track, index) => { track.buffer = results[index].value; });
        this.duration = Math.max(...this.tracks.map(track => track.buffer.duration));
      }
    }).catch(error => {
      this.controller.abort();
      throw error.name === "AbortError" ? new Error("音轨加载超时或连接中断，请重试。") : error;
    }).finally(() => {
      clearTimeout(timeout); this.loading = false; this.pending = null; this.notify();
    });
    return this.pending;
  }
  async play() {
    if (this.disposed || this.playing || !this.tracks.length) return;
    const intent = ++this.intent;
    this.graph();
    // Resume immediately inside the user's click, before downloading any audio.
    const resumed = this.context.resume();
    await Promise.all([resumed, this.prepare()]);
    if (this.disposed || intent !== this.intent) return;
    if (this.offset >= this.duration) this.offset = 0;
    this.schedule();
  }
  stopSources() {
    this.run++;
    for (const source of this.sources) { source.onended = null; source.stop(); source.disconnect(); }
    this.sources = [];
  }
  schedule() {
    this.stopSources(); this.updateGains();
    this.when = this.context.currentTime + .05;
    const run = this.run;
    this.playing = true;
    for (const track of this.tracks) {
      if (this.offset >= track.buffer.duration) continue;
      const source = this.context.createBufferSource();
      source.buffer = track.buffer; source.connect(track.gain);
      source.onended = () => {
        if (this.disposed || run !== this.run || !this.playing) return;
        if (this.position() >= this.duration - .02) {
          this.offset = this.duration; this.playing = false; this.stopSources(); this.notify();
        }
      };
      this.sources.push(source); source.start(this.when, this.offset);
    }
    this.notify();
  }
  pause() {
    this.intent++; this.offset = this.position(); this.playing = false;
    this.stopSources(); this.notify();
  }
  seek(seconds) {
    if (!Number.isFinite(seconds)) return;
    this.offset = Math.max(0, Math.min(this.duration, seconds));
    if (this.playing) {
      if (this.offset >= this.duration) { this.playing = false; this.stopSources(); }
      else this.schedule();
    }
    this.notify();
  }
  updateGains() {
    if (!this.context) return;
    for (const track of this.tracks) {
      const value = track.muted || (this.solo && this.solo !== track.name) ? 0 : track.volume;
      const gain = track.gain.gain, now = this.context.currentTime;
      gain.cancelScheduledValues(now);
      if (this.playing) gain.setTargetAtTime(value, now, .015);
      else gain.setValueAtTime(value, now);
    }
  }
  setVolume(name, value) {
    const track = this.tracks.find(track => track.name === name);
    if (!track || !Number.isFinite(value)) return;
    track.volume = Math.max(0, Math.min(1, value)); this.updateGains(); this.notify();
  }
  toggleMute(name) {
    const track = this.tracks.find(track => track.name === name);
    if (!track) return;
    track.muted = !track.muted; this.updateGains(); this.notify();
  }
  setSolo(name) {
    if (name && !this.tracks.some(track => track.name === name)) return;
    this.solo = name;
    if (name) this.tracks.find(track => track.name === name).muted = false;
    this.updateGains(); this.notify();
  }
  reset() {
    this.solo = null;
    for (const track of this.tracks) { track.volume = .88; track.muted = false; }
    this.updateGains(); this.notify();
  }
  dispose() {
    this.disposed = true; this.intent++; this.playing = false; this.stopSources();
    if (this.controller) this.controller.abort();
    for (const track of this.tracks) { track.buffer = null; if (track.gain) track.gain.disconnect(); }
    if (this.context) this.context.close().catch(() => {});
  }
}

const $ = (id) => document.getElementById(id);
const labels = { uploading: "正在接收", queued: "排队中", processing: "处理中", completed: "已完成", failed: "处理失败", expired: "任务已失效", unknown: "待确认" };
const stemLabels = { vocals: "人声", drums: "鼓", bass: "贝斯", other: "其他乐器" };
const stageLabels = {
  loading: "正在读取音频", loading_audio: "正在读取音频", preparing: "正在准备音频",
  inference: "正在分离音轨", separating: "正在分离音轨",
  saving: "正在保存音轨", saving_stems: "正在保存音轨",
  encoding: "正在生成试听音频", encoding_aac: "正在生成试听音频",
};
const storageKey = "ordinary-muse-tasks-v1";
const localPreview = location.protocol === "file:";
// Resolve every API/media URL within this page's mount (e.g. /audio/), while
// retaining direct Mac/App access at /. Canonical trailing slashes are set by Nginx.
const appBaseUrl = new URL("./", location.href);
function apiUrl(path) { return new URL(path.replace(/^\/+/, ""), appBaseUrl).href; }
let config = { max_file_size_mb: 50, allowed_extensions: [".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"] };
let selectedFile = null, uploading = false, activeId = null, pollTimer = null, generation = 0;
let mode = "separation", extractionSource = null;
let elapsedTimer = null;
let mixer = null, mixerTimer = null;
let downloadJob = null, downloadTimer = null;
const mixerControls = new Map();
const controlIcons = {
  play: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5.5v13l10-6.5z" fill="currentColor" stroke="none"/></svg>',
  pause: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="6.5" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="13.5" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/></svg>',
  volume: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M11 5 6 9H3v6h3l5 4zM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/></svg>',
  muted: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M11 5 6 9H3v6h3l5 4zM16 9l5 6m0-6-5 6"/></svg>',
  reset: '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 10a8 8 0 1 1 1.5 7M4 4v6h6"/></svg>',
  loading: '<svg class="control-spinner" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8" opacity=".25"/><path d="M12 4a8 8 0 0 1 8 8"/></svg>',
};
function controlIcon(button, name, label) {
  if (button.dataset.icon !== name) { button.innerHTML = controlIcons[name]; button.dataset.icon = name; }
  button.setAttribute("aria-label", label); button.title = label;
}
const videoDefaults = [".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"];

function fileRules() {
  return mode === "extraction" ? { extensions: config.video_extensions || videoDefaults, size: config.max_video_size_mb || 500 } : { extensions: config.allowed_extensions, size: config.max_file_size_mb };
}
function resetResults() {
  resetDownload();
  disposeMixer();
  $("results").hidden = true; $("extraction-results").hidden = true;
  $("empty-results").hidden = mode === "extraction"; $("extraction-empty").hidden = mode !== "extraction";
  $("tracks").replaceChildren(); $("extraction-player").pause();
  $("extraction-player").removeAttribute("src"); $("extraction-player").load(); extractionSource = null;
}
function setMode(value) {
  if (uploading) return;
  mode = value; selectedFile = null; $("file").value = "";
  clearTimeout(pollTimer); generation++; activeId = null;
  const video = mode === "extraction", rules = fileRules();
  $("mode-separation").setAttribute("aria-pressed", String(!video));
  $("mode-extraction").setAttribute("aria-pressed", String(video));
  $("format-control").hidden = !video; $("file").accept = rules.extensions.join(",");
  $("upload-title").textContent = video ? "从视频中提取声音" : "放入你的音乐";
  $("mode-tag").textContent = video ? "视频提音" : "音轨分离";
  $("file-title").textContent = video ? "选择视频，或拖放到这里" : "选择音频，或拖放到这里";
  $("file-detail").textContent = `${rules.extensions.map(x => x.slice(1).toUpperCase()).join(" / ")} · 最大 ${rules.size} MB`;
  $("upload-hint").textContent = video ? "视频上传至服务端处理，提取第一条音轨。上传时请保持页面打开。" : "音频将在服务端处理。上传时请保持页面打开。";
  $("result-title").textContent = video ? "你的提取音频" : "探索独立音轨";
  $("result-count").textContent = video ? "AUDIO" : "4 STEMS";
  $("start").textContent = video ? "开始提取 ↗" : "开始分离 ↗";
  $("start").disabled = true; $("status-box").hidden = true; clearTaskProgress();
  resetResults(); renderHistory();
}
let records = [];
try {
  const saved = JSON.parse(localStorage.getItem(storageKey) || "[]");
  if (Array.isArray(saved)) records = saved.filter(r => r && typeof r.id === "string" && /^[\w-]{1,64}$/.test(r.id)).slice(0, 12);
} catch { /* Storage may be unavailable in private browsing. */ }

function remember(record) {
  records = [{ ...records.find(r => r.id === record.id), ...record }, ...records.filter(r => r.id !== record.id)].slice(0, 12);
  try { localStorage.setItem(storageKey, JSON.stringify(records)); } catch { /* Current task still works. */ }
  renderHistory();
}
function renderHistory() {
  $("history").replaceChildren();
  $("history-empty").hidden = records.length > 0;
  for (const record of records) {
    const li = document.createElement("li"), button = document.createElement("button");
    button.type = "button"; button.disabled = uploading;
    button.classList.toggle("active", record.id === activeId);
    const name = document.createElement("span"), state = document.createElement("small");
    name.textContent = record.name || "音频任务";
    state.textContent = `${record.kind === "extraction" ? "提音" : "分轨"} · ${labels[record.status] || "点击查看"}`;
    button.append(name, state); button.addEventListener("click", () => activate(record.id));
    li.append(button); $("history").append(li);
  }
}
function status(title, detail, error = false) {
  clearTaskProgress();
  $("status-box").hidden = false;
  $("status-box").classList.toggle("error", error);
  $("status-title").textContent = title;
  $("status-detail").textContent = detail;
}
function clearTaskProgress() {
  clearInterval(elapsedTimer); elapsedTimer = null;
  $("task-progress-box").hidden = true;
}
function formatElapsed(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分`;
}
function showTaskProgress(task) {
  const queued = task.status === "queued";
  const known = task.status === "processing" && typeof task.progress === "number" && Number.isFinite(task.progress);
  const title = queued ? "排队中" : task.status === "processing"
    ? stageLabels[task.stage] || (task.kind === "extraction" ? "正在提取音频" : "正在分离音轨")
    : labels[task.status] || "正在处理";
  status(title, queued ? "前面的任务正在处理，轮到你时会自动开始。"
    : `${known ? "进度由服务端更新。" : "服务端暂未提供处理百分比。"}可以离开页面，稍后从最近的创作中查看。`);
  $("task-progress-box").hidden = false;
  if (known) {
    // Completion includes exporting files; reserve 100% for a completed task.
    const percent = Math.max(0, Math.min(99, Math.floor(task.progress)));
    $("task-progress").value = percent;
    $("task-percent").textContent = `${percent}%`;
  } else {
    $("task-progress").removeAttribute("value");
    $("task-percent").textContent = queued ? "等待开始" : "进度待更新";
  }
  const started = task.status === "processing" ? Date.parse(task.started_at || task.processing_started_at || "") : NaN;
  const origin = Number.isFinite(started) ? started : Date.parse(task.created_at || "");
  $("task-elapsed").hidden = !Number.isFinite(origin);
  if (Number.isFinite(origin)) {
    const updateElapsed = () => {
      $("task-elapsed").textContent = `${Number.isFinite(started) ? "已处理" : "提交已过"} ${formatElapsed(Date.now() - origin)}`;
    };
    updateElapsed(); elapsedTimer = setInterval(updateElapsed, 1000);
  }
}
function setUploading(value) {
  uploading = value;
  $("file").disabled = value;
  $("dropzone").classList.toggle("busy", value);
  $("start").disabled = value || !selectedFile || (mode === "separation" && config.separation_enabled === false);
  $("start").textContent = value ? "正在提交…" : mode === "extraction" ? "开始提取 ↗" : "开始分离 ↗";
  for (const id of ["mode-separation", "mode-extraction", "export-format", "separate-extracted"]) $(id).disabled = value;
  if (config.separation_enabled === false) $("separate-extracted").disabled = true;
  renderHistory();
}
async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(apiUrl(path), { ...options, signal: controller.signal, cache: "no-store" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const error = new Error(typeof data.detail === "string" ? data.detail : `请求失败 (${response.status})`); error.status = response.status; throw error;
    }
    return await response.json();
  } finally { clearTimeout(timeout); }
}
async function checkConnection() {
  if (localPreview) {
    $("connection-label").textContent = "本地外观预览";
    status("请通过服务地址使用", "启动 Mac 上的分离服务，然后打开 http://127.0.0.1:8000/，或使用花生壳公网地址。本地文件仅支持外观预览。");
    return;
  }
  $("connection-label").textContent = "检查连接中";
  try {
    const health = await request("/api/v1/health");
    $("connection").className = "connection online";
    $("connection-label").textContent = health.separation_enabled === false ? "提音服务在线" : "分离服务在线";
  } catch {
    $("connection").className = "connection offline";
    $("connection-label").textContent = "未连接 · 点击重试";
  }
}
function selectFile(file) {
  if (!file || uploading || localPreview) return;
  const extension = "." + file.name.split(".").pop().toLowerCase();
  const rules = fileRules();
  if (!rules.extensions.includes(extension) || !file.size || file.size > rules.size * 1024 * 1024) {
    selectedFile = null; $("start").disabled = true; $("file").value = "";
    $("file-title").textContent = "请选择支持的文件";
    status("文件无法上传", `请选择非空文件，支持 ${rules.extensions.join(" / ")}，最大 ${rules.size} MB。`, true);
    return;
  }
  selectedFile = file;
  $("file-title").textContent = file.name;
  $("file-detail").textContent = `${(file.size / 1024 / 1024).toFixed(1)} MB · 点击可重新选择`;
  $("start").disabled = mode === "separation" && config.separation_enabled === false;
  if ($("start").disabled) status("分轨暂未启用", "当前服务器支持视频提音，Demucs 分轨仍在评估中。请选择视频提音使用。", true);
}
function upload(file, id) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(), form = new FormData();
    form.append("file", file);
    xhr.open("POST", apiUrl(mode === "extraction" ? `/api/v1/extract/async?format=${encodeURIComponent($("export-format").value)}` : "/api/v1/separate/async"));
    xhr.setRequestHeader("X-Request-ID", id);
    xhr.timeout = 600000;
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round(event.loaded / event.total * 100);
      $("progress").value = percent; $("upload-percent").textContent = `${percent}%`;
      if (percent === 100) status("正在确认上传", "文件已发送，等待服务端确认，请勿重复提交。");
    };
    xhr.onload = () => {
      let data;
      try { data = JSON.parse(xhr.responseText); } catch { reject(new Error("服务返回了无效响应，请检查隧道连接。")); return; }
      if (xhr.status >= 200 && xhr.status < 300 && data.task_id) resolve(data);
      else reject(new Error(typeof data.detail === "string" ? data.detail : `上传失败 (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("上传连接中断，请检查网络。"));
    xhr.ontimeout = () => reject(new Error("上传超时，请检查网络或换一个较小的文件。"));
    xhr.send(form);
  });
}
function newId() {
  // Works on both HTTPS and existing HTTP tunnel URLs.
  const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}
async function submit() {
  if (!selectedFile || uploading || (mode === "separation" && config.separation_enabled === false)) return;
  clearTimeout(pollTimer); generation++;
  const id = newId(), file = selectedFile;
  activeId = id; $("retry").hidden = true;
  resetResults();
  $("progress").hidden = false; $("progress").value = 0; $("upload-percent").textContent = "0%";
  remember({ id, name: file.name, status: "uploading", kind: mode });
  setUploading(true); status(mode === "extraction" ? "正在上传视频" : "正在上传音频", "上传时请保持页面打开，上传完成后可稍后回来查看结果。");
  let accepted = false;
  try {
    const task = await upload(file, id);
    activeId = task.task_id; accepted = true;
  } catch (error) {
    // An upload response may be lost even though the server accepted the file.
    try {
      const task = await request(`/api/v1/task/${encodeURIComponent(id)}`);
      accepted = true; remember({ id, status: task.status });
    } catch (lookupError) {
      remember({ id, status: lookupError.status === 404 ? "failed" : "unknown" });
      status("未能确认上传", `${error.message} 可先重新查询任务；若确认不存在，再重新上传。`, true);
      $("retry").hidden = false;
    }
  } finally {
    setUploading(false); $("progress").hidden = true; $("upload-percent").textContent = "";
  }
  if (accepted) activate(activeId);
}
function activate(id) {
  if (localPreview) { checkConnection(); return; }
  if (uploading) return;
  clearTimeout(pollTimer); generation++; activeId = id;
  $("retry").hidden = true; resetResults();
  $("progress").hidden = true; $("upload-percent").textContent = "";
  status("正在获取任务", "正在恢复文件的处理状态…"); renderHistory(); poll(id, generation);
}
async function poll(id, version) {
  if (version !== generation) return;
  try {
    const task = await request(`/api/v1/task/${encodeURIComponent(id)}`);
    if (version !== generation) return;
    remember({ id, name: task.filename, status: task.status, kind: task.kind || "separation" });
    $("retry").hidden = true;
    if (task.status === "completed") {
      const deadline = task.expires_at ? `请在 ${new Date(task.expires_at).toLocaleString("zh-CN")} 前下载。` : "";
      status(task.kind === "extraction" ? "音频提取完成" : "分离完成", `${task.processing_time ? `处理用时 ${task.processing_time}。` : ""}${deadline}`);
      if (task.kind === "extraction") showExtraction(id, task); else showResults(id, task);
      return;
    }
    if (task.status === "failed") {
      status("处理失败", task.error || "请换一个文件重试。", true); return;
    }
    showTaskProgress(task);
    pollTimer = setTimeout(() => poll(id, version), 3000);
  } catch (error) {
    if (version !== generation) return;
    if (error.status === 404 || error.status === 410) {
      status(error.status === 410 ? "结果已过期" : "任务不存在", error.status === 410 ? "结果已超过保留时间，无法继续试听或下载。请重新提交文件。" : "未找到此任务，可能尚未提交成功，或来自升级前未保存的记录。请重新提交文件。", true);
      remember({ id, status: "expired" }); return;
    }
    status("暂时无法查询进度", "服务端任务可能仍在继续。请检查网络与服务连接，10 秒后自动重试。", true);
    $("retry").hidden = false;
    pollTimer = setTimeout(() => poll(id, version), 10000);
  }
}
function showResults(id, task) {
  resetDownload();
  disposeMixer();
  $("extraction-empty").hidden = true; $("extraction-results").hidden = true;
  $("result-title").textContent = "探索独立音轨"; $("result-count").textContent = "4 STEMS";
  $("empty-results").hidden = true; $("results").hidden = false;
  $("result-filename").textContent = task.filename;
  $("tracks").replaceChildren();
  const base = apiUrl(`/api/v1/task/${encodeURIComponent(id)}`);
  const sources = [];
  for (const name of Object.keys(task.stems || {})) {
    const track = document.createElement("div"), head = document.createElement("div"), title = document.createElement("strong");
    track.className = "track"; track.dataset.stem = name; head.className = "track-head"; title.textContent = stemLabels[name] || name;
    head.append(title);
    const stemUrl = `${base}/stem/${encodeURIComponent(name)}`;
    const hasM4a = Boolean(task.aac_stems && task.aac_stems[name]);
    for (const format of hasM4a ? ["m4a", "wav"] : ["wav"]) {
      const link = document.createElement("a"); link.href = `${stemUrl}?format=${format}`;
      link.download = `${name}.${format}`; link.textContent = `${format.toUpperCase()} ↓`;
      link.setAttribute("aria-label", `下载${title.textContent} ${format.toUpperCase()}`); head.append(link);
    }
    sources.push({name, url: `${stemUrl}?format=${hasM4a ? "m4a" : "wav"}`});
    const controls = document.createElement("div"), solo = document.createElement("button"), mute = document.createElement("button");
    controls.className = "track-controls";
    solo.type = mute.type = "button"; solo.className = mute.className = "icon-button";
    solo.setAttribute("aria-label", `单独试听${title.textContent}`); mute.setAttribute("aria-label", `静音${title.textContent}`);
    solo.addEventListener("click", () => runMixer(async current => {
      if (current.playing && current.solo === name) current.pause();
      else { current.setSolo(name); await current.play(); }
    }));
    mute.addEventListener("click", () => mixer && mixer.toggleMute(name));
    const label = document.createElement("label"), volume = document.createElement("input"), value = document.createElement("output");
    label.className = "track-volume"; volume.type = "range"; volume.min = "0"; volume.max = "100"; volume.step = "1";
    volume.value = "88"; volume.setAttribute("aria-label", `${title.textContent}音量`);
    volume.addEventListener("input", () => mixer && mixer.setVolume(name, Number(volume.value) / 100));
    label.append(volume, value); controls.append(solo, mute, label);
    mixerControls.set(name, {solo, mute, volume, value, card:track});
    track.append(head, controls); $("tracks").append(track);
  }
  mixer = new StemMixer(sources, current => { if (current === mixer) renderMixer(); });
  $("mixer").hidden = !sources.length; renderMixer();
  $("download-all").href = `${base}/download`;
  $("download-all").download = `${(task.filename || "音轨").replace(/\.[^.]*$/, "").replace(/[\\/:*?"<>|\r\n]/g, "_")}-stems.zip`;
}

function resetDownload() {
  if (downloadJob) {
    downloadJob.controller.abort();
    if (downloadJob.blobUrl) URL.revokeObjectURL(downloadJob.blobUrl);
    $("download-all").href = downloadJob.source;
  }
  downloadJob = null; clearInterval(downloadTimer); downloadTimer = null;
  $("download-all").textContent = "下载全部 WAV 音轨 ↓";
  $("download-all").setAttribute("aria-disabled", "false");
  $("download-status").hidden = true; $("download-cancel").hidden = true;
  $("download-progress").hidden = true;
}
function renderDownload(job) {
  if (downloadJob !== job) return;
  const mb = value => (value / 1024 / 1024).toFixed(1);
  $("download-status").hidden = false;
  $("download-cancel").hidden = !job.busy;
  $("download-progress").hidden = !job.busy;
  $("download-all").setAttribute("aria-disabled", String(job.busy));
  if (job.busy) {
    $("download-all").textContent = job.received ? "正在接收压缩包…" : "正在准备压缩包…";
    $("download-message").textContent = job.received
      ? `已接收 ${mb(job.received)} MB${job.total ? ` / ${mb(job.total)} MB` : ""}，请保持页面打开。`
      : `正在准备并传输 WAV 压缩包，已等待 ${formatElapsed(Date.now() - job.started)}。文件较大，请保持页面打开。`;
    if (job.total) $("download-progress").value = Math.min(99, job.received / job.total * 100);
    else $("download-progress").removeAttribute("value");
  }
}
async function downloadAll(event) {
  // Leave a ready Blob link as a real user-clickable download (Safari/WebViews).
  if (downloadJob && downloadJob.blobUrl) return;
  event.preventDefault();
  if (downloadJob && downloadJob.busy) return;
  const link = $("download-all");
  const job = downloadJob = {source:link.href, controller:new AbortController(), busy:true, started:Date.now(), received:0, total:0};
  const timeout = setTimeout(() => { job.timedOut = true; job.controller.abort(); }, 600000);
  renderDownload(job); downloadTimer = setInterval(() => renderDownload(job), 1000);
  try {
    const response = await fetch(job.source, {signal:job.controller.signal, cache:"no-store"});
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === "string" ? error.detail : `下载失败 (${response.status})`);
    }
    const type = response.headers.get("Content-Type") || "";
    if (!/application\/(zip|octet-stream)/i.test(type)) throw new Error("服务未返回 ZIP 文件，请稍后重试。");
    job.total = Number(response.headers.get("Content-Length")) || 0;
    const chunks = [];
    if (response.body && response.body.getReader) {
      const reader = response.body.getReader();
      let lastRender = 0;
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        if (downloadJob !== job) return;
        if (job.controller.signal.aborted) throw new Error("下载已取消");
        chunks.push(value); job.received += value.byteLength;
        if (Date.now() - lastRender > 150) { renderDownload(job); lastRender = Date.now(); }
      }
    } else {
      const bytes = await response.arrayBuffer(); chunks.push(bytes); job.received = bytes.byteLength;
    }
    if (job.total && !response.headers.get("Content-Encoding") && job.received !== job.total) throw new Error("压缩包未接收完整，请重试。");
    const blob = new Blob(chunks, {type:"application/zip"});
    chunks.length = 0;
    const signature = new Uint8Array(await blob.slice(0,4).arrayBuffer());
    if (signature.length !== 4 || signature[0] !== 80 || signature[1] !== 75 || signature[2] !== 3 || signature[3] !== 4) throw new Error("压缩包内容无效，请重试或分别下载音轨。");
    if (downloadJob !== job) return;
    if (job.controller.signal.aborted) throw new Error("下载已取消");
    job.blobUrl = URL.createObjectURL(blob); job.busy = false;
    link.href = job.blobUrl; link.textContent = `保存 ZIP（${(blob.size/1024/1024).toFixed(1)} MB）↓`;
    $("download-message").textContent = "压缩包已完整接收。若未自动保存，请点击“保存 ZIP”；内置浏览器仍无保存窗口时，可在 Chrome / Safari 打开此页面保存。";
    try { link.click(); } catch { /* The visible Blob link remains available for a manual save. */ }
  } catch (error) {
    if (downloadJob !== job) return;
    job.busy = false;
    link.textContent = "重试下载全部 WAV 音轨 ↓";
    $("download-message").textContent = job.controller.signal.aborted
      ? job.timedOut ? "下载等待超时，可重试或分别下载音轨。" : "已取消下载，可以重新开始。"
      : error.message || "下载失败，请检查网络后重试。";
  } finally {
    clearTimeout(timeout);
    if (downloadJob === job) {
      clearInterval(downloadTimer); downloadTimer = null;
      $("download-all").setAttribute("aria-disabled", "false");
      $("download-cancel").hidden = true; $("download-progress").hidden = true;
    }
  }
}

function disposeMixer() {
  clearInterval(mixerTimer); mixerTimer = null;
  if (mixer) mixer.dispose();
  mixer = null; mixerControls.clear(); $("mixer").hidden = true;
}
function mixTime(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}
function renderMixer() {
  if (!mixer) return;
  $("mix-play").disabled = mixer.loading || (mixer.playing && !mixer.solo);
  controlIcon($("mix-play"), mixer.loading ? "loading" : "play", mixer.loading ? "正在载入音轨" : mixer.playing && !mixer.solo ? "正在合并播放" : "合并播放");
  $("mix-play").setAttribute("aria-busy", String(mixer.loading));
  controlIcon($("mix-pause"), "pause", "暂停播放");
  controlIcon($("mix-reset"), "reset", "重置所有音轨音量");
  $("mix-pause").disabled = !mixer.playing;
  $("mix-seek").disabled = !mixer.duration;
  const current = mixer.position();
  $("mix-seek").value = mixer.duration ? Math.round(current / mixer.duration * 1000) : 0;
  $("mix-seek").setAttribute("aria-valuetext", `${mixTime(current)} / ${mixTime(mixer.duration)}`);
  $("mix-time").textContent = `${mixTime(current)} / ${mixTime(mixer.duration)}`;
  $("mix-message").textContent = mixer.loading ? `正在载入音轨 ${mixer.loaded}/${mixer.tracks.length}，首次播放需要等待加载完成。`
    : mixer.playing ? mixer.solo ? `正在单独试听${stemLabels[mixer.solo] || mixer.solo}` : "正在播放当前混音 · 音量调节实时生效"
    : mixer.duration ? "已暂停 · 可继续播放或拖动进度" : "调整各轨音量后，点击合并播放。";
  for (const track of mixer.tracks) {
    const controls = mixerControls.get(track.name);
    controls.volume.value = String(Math.round(track.volume * 100)); controls.value.textContent = `${Math.round(track.volume * 100)}%`;
    const name = stemLabels[track.name] || track.name;
    const soloPlaying = mixer.playing && mixer.solo === track.name;
    controlIcon(controls.mute, track.muted ? "muted" : "volume", `${track.muted ? "取消静音" : "静音"}${name}`);
    controls.mute.setAttribute("aria-pressed", String(track.muted));
    controlIcon(controls.solo, soloPlaying ? "pause" : "play", `${soloPlaying ? "暂停" : "单独播放"}${name}`);
    controls.solo.setAttribute("aria-pressed", String(mixer.solo === track.name)); controls.solo.disabled = mixer.loading;
    const audible = !track.muted && track.volume > 0 && (!mixer.solo || mixer.solo === track.name);
    controls.card.classList.toggle("is-playing", mixer.playing && audible);
    controls.card.classList.toggle("is-muted", !audible);
  }
  if (mixer.playing && !mixerTimer) mixerTimer = setInterval(renderMixer, 250);
  if (!mixer.playing) { clearInterval(mixerTimer); mixerTimer = null; }
}
async function runMixer(action) {
  const current = mixer;
  if (!current) return;
  $("extraction-player").pause();
  try { await action(current); }
  catch (error) {
    if (mixer === current && !current.disposed) { renderMixer(); $("mix-message").textContent = error.message || "混音播放失败，请重试。"; }
  }
}

function showExtraction(id, task) {
  resetDownload();
  disposeMixer();
  $("empty-results").hidden = true; $("extraction-empty").hidden = true; $("results").hidden = true;
  $("extraction-results").hidden = false; $("result-title").textContent = "你的提取音频";
  $("result-count").textContent = task.export_format.toUpperCase();
  $("extraction-filename").textContent = task.filename;
  $("extraction-player").src = apiUrl(`/api/v1/task/${encodeURIComponent(id)}/audio?preview=true`);
  $("download-audio").href = apiUrl(`/api/v1/task/${encodeURIComponent(id)}/audio`);
  $("download-audio").textContent = `下载 ${task.export_format.toUpperCase()} 音频 ↓`;
  $("separate-extracted").disabled = config.separation_enabled === false;
  $("separate-extracted").textContent = config.separation_enabled === false ? "分轨暂未启用" : "继续分离四条音轨 ↗";
  extractionSource = { id, name: task.filename };
}
async function separateExtraction() {
  if (!extractionSource || uploading || localPreview || config.separation_enabled === false) return;
  const source = extractionSource, id = newId();
  setUploading(true); status("正在创建分轨任务", "使用已提取的音频，无需再次上传。");
  let accepted = false;
  try {
    await request(`/api/v1/task/${encodeURIComponent(source.id)}/separate`, {method:"POST", headers:{"X-Request-ID":id}});
    accepted = true;
  } catch (error) {
    try { await request(`/api/v1/task/${id}`); accepted = true; }
    catch { status("未能确认分轨任务", `${error.message} 请从最近的创作中查询该任务，避免重复提交。`, true); }
  } finally { setUploading(false); }
  remember({ id, name: `${source.name} · 分轨`, kind:"separation", status: accepted ? "queued" : "unknown" });
  if (accepted) activate(id);
}
$("mode-separation").addEventListener("click", () => setMode("separation"));
$("mode-extraction").addEventListener("click", () => setMode("extraction"));
$("separate-extracted").addEventListener("click", separateExtraction);
$("extraction-player").addEventListener("error", () => status("音频暂时无法播放", "请检查网络，或重新查询任务确认结果是否已过期。", true));
$("file").addEventListener("change", event => selectFile(event.target.files[0]));
for (const eventName of ["dragenter", "dragover"]) $("dropzone").addEventListener(eventName, event => { event.preventDefault(); if (!uploading) $("dropzone").classList.add("dragging"); });
for (const eventName of ["dragleave", "drop"]) $("dropzone").addEventListener(eventName, event => { event.preventDefault(); $("dropzone").classList.remove("dragging"); });
$("dropzone").addEventListener("drop", event => selectFile(event.dataTransfer.files[0]));
$("start").addEventListener("click", submit);
$("retry").addEventListener("click", () => activeId && activate(activeId));
$("connection").addEventListener("click", checkConnection);
$("mix-play").addEventListener("click", () => runMixer(async current => {
  current.setSolo(null); await current.play();
}));
$("mix-pause").addEventListener("click", () => mixer && mixer.pause());
$("mix-seek").addEventListener("input", () => mixer && mixer.seek(Number($("mix-seek").value) / 1000 * mixer.duration));
$("mix-reset").addEventListener("click", () => mixer && mixer.reset());
$("download-all").addEventListener("click", downloadAll);
$("download-cancel").addEventListener("click", () => downloadJob && downloadJob.controller.abort());
document.addEventListener("visibilitychange", () => { if (!document.hidden && activeId && !uploading && $("results").hidden && $("extraction-results").hidden) activate(activeId); });
window.addEventListener("beforeunload", event => { if (uploading) { event.preventDefault(); event.returnValue = ""; } });
async function init() {
  renderHistory(); checkConnection();
  if (localPreview) {
    $("file").disabled = true;
    $("dropzone").classList.add("busy");
    $("progress").hidden = true;
    return;
  }
  try {
    config = await request("/api/v1/web-config");
    if (config.separation_enabled === false) setMode("extraction");
    const rules = fileRules();
    $("file").accept = rules.extensions.join(",");
    if (!selectedFile) $("file-detail").textContent = `${rules.extensions.map(x => x.slice(1).toUpperCase()).join(" / ")} · 最大 ${rules.size} MB`;
    $("retention").textContent = `提取或分离完成后保留 ${config.retention_hours} 小时，过期自动清理，请及时下载`;
    if (selectedFile) selectFile(selectedFile);
  } catch { /* Keep safe defaults and allow manual connection retry. */ }
  if (records.length) activate(records[0].id);
}
init();
