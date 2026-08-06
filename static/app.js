// ==================== 状态管理 ====================
const state = {
  file: null,
  mode: 'auto',
  jobId: null,
  pollTimer: null,
  packFormat: 'zip',
  packPath: '',
  packResultData: null,
};

// ==================== DOM 引用 ====================
const $ = (id) => document.getElementById(id);
const uploadZone = $('uploadZone');
const fileInput = $('fileInput');
const fileInfo = $('fileInfo');
const fileName = $('fileName');
const clearFileBtn = $('clearFile');
const processBtn = $('processBtn');
const envBadge = $('envBadge');

// ==================== 初始化 ====================
async function init() {
  // 检查环境
  try {
    const res = await fetch('/api/check');
    const data = await res.json();
    if (data.ok) {
      envBadge.textContent = 'ffmpeg 就绪';
      envBadge.classList.add('ok');
    } else {
      envBadge.textContent = '缺少 ffmpeg';
      envBadge.classList.add('fail');
    }
  } catch {
    envBadge.textContent = '服务未启动';
    envBadge.classList.add('fail');
  }

  bindUpload();
  bindSettings();
  bindActions();
}

// ==================== 上传交互 ====================
function bindUpload() {
  // 点击上传
  uploadZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => {
    if (e.target.files[0]) selectFile(e.target.files[0]);
  });

  // 拖拽上传
  uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('dragover');
  });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
  uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    if (e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0]);
  });

  // 移除文件
  clearFileBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    state.file = null;
    fileInput.value = '';
    uploadZone.style.display = '';
    fileInfo.hidden = true;
    processBtn.disabled = true;
  });
}

function selectFile(file) {
  state.file = file;
  fileName.textContent = file.name;
  uploadZone.style.display = 'none';
  fileInfo.hidden = false;
  processBtn.disabled = false;
}

// ==================== 设置交互 ====================
function bindSettings() {
  // 模式切换
  document.querySelectorAll('.mode-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      state.mode = tab.dataset.mode;
      updateModeVisibility();
    });
  });

  // 滑块实时显示
  bindSlider('sceneThreshold', 'sceneThresholdVal', (v) => parseFloat(v).toFixed(2));
  bindSlider('interval', 'intervalVal', (v) => parseFloat(v).toFixed(1) + 's');
  bindSlider('hamming', 'hammingVal', (v) => v);
  bindSlider('maxFrames', 'maxFramesVal', (v) => v);
}

function bindSlider(inputId, labelId, formatter) {
  const input = $(inputId);
  const label = $(labelId);
  input.addEventListener('input', () => {
    label.textContent = formatter(input.value);
  });
}

function updateModeVisibility() {
  const sceneRow = $('sceneRow');
  const intervalRow = $('intervalRow');
  sceneRow.style.display = (state.mode === 'auto' || state.mode === 'scene') ? '' : 'none';
  intervalRow.style.display = (state.mode === 'auto' || state.mode === 'interval') ? '' : 'none';
}

// ==================== 处理流程 ====================
function bindActions() {
  processBtn.addEventListener('click', startProcess);
  $('retryBtn').addEventListener('click', () => showState('emptyState'));
  $('newTaskBtn').addEventListener('click', resetAll);
  $('downloadAllBtn').addEventListener('click', () => downloadFile(`/api/results/${state.jobId}/download`));
  $('downloadManifestBtn').addEventListener('click', () => downloadFile(`/api/results/${state.jobId}/manifest`));
  $('downloadGridBtn').addEventListener('click', () => downloadFile(`/api/results/${state.jobId}/contact_sheet`));
  $('copyPromptBtn').addEventListener('click', copyPrompt);

  // 打包按钮
  $('packBtn').addEventListener('click', openPackModal);
  $('packModalClose').addEventListener('click', closePackModal);
  $('packConfirmBtn').addEventListener('click', startPack);
  $('packRetryBtn').addEventListener('click', () => showPackView('packForm'));
  $('packAgainBtn').addEventListener('click', () => showPackView('packForm'));
  $('packDoneBtn').addEventListener('click', closePackModal);

  // 点击遮罩关闭
  $('packModal').addEventListener('click', (e) => {
    if (e.target === $('packModal')) closePackModal();
  });
}

async function startProcess() {
  if (!state.file) return;

  processBtn.disabled = true;
  showState('processingState');
  $('processingSteps').innerHTML = '';
  $('progressFill').style.width = '0%';

  const formData = new FormData();
  formData.append('video', state.file);
  formData.append('mode', state.mode);
  formData.append('scene_threshold', $('sceneThreshold').value);
  formData.append('interval', $('interval').value);
  formData.append('hamming', $('hamming').value);
  formData.append('max_frames', $('maxFrames').value);
  formData.append('make_grid', $('makeGrid').checked ? 'true' : 'false');
  formData.append('grid_cols', '4');

  try {
    const res = await fetch('/api/process', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    state.jobId = data.job_id;
    pollStatus();
  } catch (err) {
    showError(err.message);
  }
}

function pollStatus() {
  state.pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${state.jobId}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      // 更新进度条
      $('progressFill').style.width = data.progress + '%';

      // 更新步骤列表
      renderSteps(data.steps);

      if (data.status === 'done') {
        clearInterval(state.pollTimer);
        await loadResults();
      } else if (data.status === 'error') {
        clearInterval(state.pollTimer);
        showError(data.error || '未知错误');
      }
    } catch (err) {
      clearInterval(state.pollTimer);
      showError(err.message);
    }
  }, 600);
}

const ALL_STEPS = [
  { step: 'probe', label: '分析视频' },
  { step: 'extract_scene', label: '场景检测抽帧' },
  { step: 'extract_interval', label: '固定间隔抽帧' },
  { step: 'dedup', label: 'pHash 去重' },
  { step: 'sample', label: '均匀采样' },
  { step: 'output', label: '生成输出文件' },
  { step: 'done', label: '完成' },
];

function renderSteps(completedSteps) {
  const container = $('processingSteps');
  const completedSet = new Set(completedSteps.map(s => s.step));
  const detailMap = {};
  completedSteps.forEach(s => { if (s.detail) detailMap[s.step] = s.detail; });

  container.innerHTML = ALL_STEPS.map(s => {
    const isDone = completedSet.has(s.step);
    const isActive = !isDone && completedSteps.length === ALL_STEPS.findIndex(x => x.step === s.step);
    // 跳过不适用的步骤
    if (s.step === 'extract_scene' && state.mode === 'interval') return '';
    if (s.step === 'extract_interval' && state.mode === 'scene') return '';
    const cls = isDone ? 'done' : (isActive ? 'active' : '');
    const dotCls = isDone ? 'done' : (isActive ? 'active' : '');
    return `<div class="step-item ${cls}">
      <div class="step-dot ${dotCls}"></div>
      <span>${s.label}</span>
      ${detailMap[s.step] ? `<span class="step-detail">${detailMap[s.step]}</span>` : ''}
    </div>`;
  }).join('');
}

// ==================== 结果展示 ====================
async function loadResults() {
  try {
    const res = await fetch(`/api/results/${state.jobId}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    renderResults(data);
    showState('resultsState');
  } catch (err) {
    showError(err.message);
  }
}

function renderResults(data) {
  const stats = data.stats;
  const manifest = data.manifest;

  // 统计栏
  const statsBar = $('statsBar');
  statsBar.innerHTML = `
    <div class="stat-chip highlight">
      <div class="stat-value">${stats.final_frames}</div>
      <div class="stat-label">最终关键帧</div>
    </div>
    <div class="stat-chip">
      <div class="stat-value">${stats.total_extracted}</div>
      <div class="stat-label">初始抽帧</div>
    </div>
    <div class="stat-chip">
      <div class="stat-value">${stats.after_dedup}</div>
      <div class="stat-label">去重后</div>
    </div>
    <div class="stat-chip">
      <div class="stat-value">${formatDuration(stats.duration)}</div>
      <div class="stat-label">视频时长</div>
    </div>
    <div class="stat-chip">
      <div class="stat-value">${stats.fps}</div>
      <div class="stat-label">帧率 fps</div>
    </div>
  `;

  // 网格预览图
  if (data.has_contact_sheet) {
    $('contactSheetSection').hidden = false;
    $('contactSheetImg').src = `/api/results/${state.jobId}/contact_sheet?t=${Date.now()}`;
    $('downloadGridBtn').hidden = false;
  } else {
    $('contactSheetSection').hidden = true;
    $('downloadGridBtn').hidden = true;
  }

  // 帧画廊
  const gallery = $('frameGallery');
  gallery.innerHTML = manifest.frames.map(f => `
    <div class="frame-card" onclick="downloadFile('/api/results/${state.jobId}/frame/${f.file}')">
      <img class="frame-thumb" src="/api/results/${state.jobId}/frame/${f.file}" alt="${f.file}" loading="lazy">
      <div class="frame-meta">
        <span class="frame-index">#${String(f.index + 1).padStart(2, '0')}</span>
        <span class="frame-time">${f.timecode}</span>
      </div>
      <div class="frame-download">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        点击下载
      </div>
    </div>
  `).join('');

  // 更新打包摘要
  $('packFrameCount').textContent = stats.final_frames;
  $('packGridSummary').hidden = !data.has_contact_sheet;
}

// ==================== 复制大模型提示词 ====================
async function copyPrompt() {
  try {
    const res = await fetch(`/api/results/${state.jobId}`);
    const data = await res.json();
    const manifest = data.manifest;
    const frameList = manifest.frames.map(f =>
      `  ${f.index + 1}. ${f.file} (时间码: ${f.timecode})`
    ).join('\n');

    const prompt = `以下是从一段视频中提取的 ${manifest.frame_count} 张关键帧图片。
视频时长：${manifest.duration_timecode}，抽帧模式：${manifest.extraction_mode}。

请按时间顺序查看这些图片，理解视频内容，并完成以下任务：
1. 概述视频的主要内容
2. 按时间线描述关键事件
3. 总结视频的核心信息

关键帧清单：
${frameList}

请结合图片内容和以上时间信息进行分析。`;

    await navigator.clipboard.writeText(prompt);
    showToast('提示词已复制到剪贴板', 'success');
  } catch {
    showToast('复制失败，请手动复制', 'error');
  }
}

// ==================== 工具函数 ====================
function showState(stateId) {
  ['emptyState', 'processingState', 'errorState', 'resultsState'].forEach(id => {
    $(id).hidden = (id !== stateId);
  });
}

function showError(msg) {
  $('errorMsg').textContent = msg;
  showState('errorState');
  processBtn.disabled = false;
}

function resetAll() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.file = null;
  state.jobId = null;
  fileInput.value = '';
  uploadZone.style.display = '';
  fileInfo.hidden = true;
  processBtn.disabled = true;
  showState('emptyState');
}

function downloadFile(url) {
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function formatDuration(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function showToast(msg, type = '') {
  const toast = $('toast');
  toast.textContent = msg;
  toast.className = 'toast ' + type;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 2500);
}

// ==================== 自动打包功能（浏览器下载） ====================

function openPackModal() {
  showPackView('packForm');
  $('packModal').hidden = false;
}

function closePackModal() {
  $('packModal').hidden = true;
}

function showPackView(viewId) {
  ['packForm', 'packProgress', 'packSuccess', 'packError'].forEach(id => {
    $(id).hidden = (id !== viewId);
  });
}

async function startPack() {
  if (!state.jobId) {
    $('packErrorMsg').textContent = '没有可打包的结果，请先处理视频';
    showPackView('packError');
    return;
  }

  showPackView('packProgress');

  try {
    // 触发浏览器原生下载 ZIP 文件（完全绕过服务器端写权限问题）
    downloadFile(`/api/results/${state.jobId}/download`);

    // 短暂延迟后显示成功
    setTimeout(() => {
      const frameCount = $('packFrameCount').textContent;
      $('packSuccessDetail').textContent = `已开始下载 ${frameCount} 个关键帧的 ZIP 压缩包`;
      showPackView('packSuccess');
    }, 800);
  } catch (err) {
    $('packErrorMsg').textContent = err.message;
    showPackView('packError');
  }
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// 暴露给 onclick
window.downloadFile = downloadFile;

// 启动
init();
