"use strict";
const $ = (id) => document.getElementById(id);
const labels = { uploading: "正在接收", queued: "排队中", processing: "处理中", completed: "已完成", failed: "处理失败", expired: "任务已失效", unknown: "待确认" };
const stemLabels = { vocals: "人声", drums: "鼓", bass: "贝斯", other: "其他乐器" };
const storageKey = "ordinary-muse-tasks-v1";
const localPreview = location.protocol === "file:";
let config = { max_file_size_mb: 50, allowed_extensions: [".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"] };
let selectedFile = null, uploading = false, activeId = null, pollTimer = null, generation = 0;
let mode = "separation", extractionSource = null;
const videoDefaults = [".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"];

function fileRules() {
  return mode === "extraction" ? { extensions: config.video_extensions || videoDefaults, size: config.max_video_size_mb || 500 } : { extensions: config.allowed_extensions, size: config.max_file_size_mb };
}
function resetResults() {
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
  $("upload-hint").textContent = video ? "视频上传至 Mac 处理，提取第一条音轨。上传时请保持页面打开。" : "音频将在服务端处理。上传时请保持页面打开。";
  $("result-title").textContent = video ? "你的提取音频" : "探索独立音轨";
  $("result-count").textContent = video ? "AUDIO" : "4 STEMS";
  $("start").textContent = video ? "开始提取 ↗" : "开始分离 ↗";
  $("start").disabled = true; $("status-box").hidden = true;
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
  $("status-box").hidden = false;
  $("status-box").classList.toggle("error", error);
  $("status-title").textContent = title;
  $("status-detail").textContent = detail;
}
function setUploading(value) {
  uploading = value;
  $("file").disabled = value;
  $("dropzone").classList.toggle("busy", value);
  $("start").disabled = value || !selectedFile;
  $("start").textContent = value ? "正在提交…" : mode === "extraction" ? "开始提取 ↗" : "开始分离 ↗";
  for (const id of ["mode-separation", "mode-extraction", "export-format", "separate-extracted"]) $(id).disabled = value;
  renderHistory();
}
async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, { ...options, signal: controller.signal, cache: "no-store" });
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
    await request("/api/v1/health");
    $("connection").className = "connection online";
    $("connection-label").textContent = "分离服务在线";
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
  $("start").disabled = false;
}
function upload(file, id) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(), form = new FormData();
    form.append("file", file);
    xhr.open("POST", mode === "extraction" ? `/api/v1/extract/async?format=${encodeURIComponent($("export-format").value)}` : "/api/v1/separate/async");
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
  if (!selectedFile || uploading) return;
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
    status(task.status === "processing" && task.kind === "extraction" ? "正在提取音频" : labels[task.status] || "正在处理", task.status === "queued" ? "前面的任务正在处理，轮到你时会自动开始。" : "服务端正在处理文件。可以离开页面，稍后从最近的创作中查看。");
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
  $("extraction-empty").hidden = true; $("extraction-results").hidden = true;
  $("result-title").textContent = "探索独立音轨"; $("result-count").textContent = "4 STEMS";
  $("empty-results").hidden = true; $("results").hidden = false;
  $("result-filename").textContent = task.filename;
  $("tracks").replaceChildren();
  const base = `/api/v1/task/${encodeURIComponent(id)}`;
  for (const name of Object.keys(task.stems || {})) {
    const track = document.createElement("div"), head = document.createElement("div"), title = document.createElement("strong");
    track.className = "track"; head.className = "track-head"; title.textContent = stemLabels[name] || name;
    head.append(title);
    const stemUrl = `${base}/stem/${encodeURIComponent(name)}`;
    const hasM4a = Boolean(task.aac_stems && task.aac_stems[name]);
    for (const format of hasM4a ? ["m4a", "wav"] : ["wav"]) {
      const link = document.createElement("a"); link.href = `${stemUrl}?format=${format}`;
      link.download = `${name}.${format}`; link.textContent = `${format.toUpperCase()} ↓`;
      link.setAttribute("aria-label", `下载${title.textContent} ${format.toUpperCase()}`); head.append(link);
    }
    const audio = document.createElement("audio"); audio.controls = true; audio.preload = "none";
    audio.src = `${stemUrl}?format=${hasM4a ? "m4a" : "wav"}`; audio.setAttribute("aria-label", `试听${title.textContent}`);
    audio.addEventListener("play", () => document.querySelectorAll("audio").forEach(other => { if (other !== audio) other.pause(); }));
    audio.addEventListener("error", () => status("音轨暂时无法播放", "请检查网络或尝试下载文件。文件可能已过期，也可能是浏览器不支持此格式。", true));
    track.append(head, audio); $("tracks").append(track);
  }
  $("download-all").href = `${base}/download`;
}

function showExtraction(id, task) {
  $("empty-results").hidden = true; $("extraction-empty").hidden = true; $("results").hidden = true;
  $("extraction-results").hidden = false; $("result-title").textContent = "你的提取音频";
  $("result-count").textContent = task.export_format.toUpperCase();
  $("extraction-filename").textContent = task.filename;
  $("extraction-player").src = `/api/v1/task/${encodeURIComponent(id)}/audio?preview=true`;
  $("download-audio").href = `/api/v1/task/${encodeURIComponent(id)}/audio`;
  $("download-audio").textContent = `下载 ${task.export_format.toUpperCase()} 音频 ↓`;
  extractionSource = { id, name: task.filename };
}
async function separateExtraction() {
  if (!extractionSource || uploading || localPreview) return;
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
    const rules = fileRules();
    $("file").accept = rules.extensions.join(",");
    if (!selectedFile) $("file-detail").textContent = `${rules.extensions.map(x => x.slice(1).toUpperCase()).join(" / ")} · 最大 ${rules.size} MB`;
    $("retention").textContent = `提取或分离完成后保留 ${config.retention_hours} 小时，过期自动清理，请及时下载`;
    if (selectedFile) selectFile(selectedFile);
  } catch { /* Keep safe defaults and allow manual connection retry. */ }
  if (records.length) activate(records[0].id);
}
init();
