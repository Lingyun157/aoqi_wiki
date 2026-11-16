/* Frame Studio · 帧工坊 — 前端逻辑（无框架、无外部依赖） */
'use strict';

const $ = (id) => document.getElementById(id);
const S = {
  env: null, upload: null, probe: null, fpsMode: 'source',
  project: null, selected: new Set(), page: 1, perPage: 48,
  previewIndex: 0, pendingAction: null, pendingIds: [], activeJob: null,
};

/* ---------------------------------------------------------------- 基础工具 */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
function fmtSize(n) {
  n = Number(n) || 0;
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < 3) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : Math.round(n)) + ' ' + units[i];
}
function status(text) { $('statusText').textContent = text; }

let toastTimer = null;
function toast(msg, kind = '') {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast show ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 3600);
}
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let data;
  try { data = await res.json(); }
  catch (e) { throw new Error('服务返回异常 (HTTP ' + res.status + ')'); }
  if (!res.ok || data.error) throw new Error(data.error || '请求失败');
  return data;
}
function post(path, body = {}) {
  return api(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload?name=' + encodeURIComponent(file.name));
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
    }
    xhr.onload = () => {
      let r;
      try { r = JSON.parse(xhr.responseText); }
      catch (e) { return reject(new Error('上传响应解析失败')); }
      if (xhr.status >= 200 && xhr.status < 300) resolve(r);
      else reject(new Error(r.error || '上传失败'));
    };
    xhr.onerror = () => reject(new Error('上传失败，请检查服务是否在运行'));
    xhr.send(file);
  });
}
function setStep(n) {
  document.querySelectorAll('.step').forEach((el) => {
    const s = Number(el.dataset.step);
    el.classList.toggle('active', s === n);
    el.classList.toggle('done', s < n);
  });
}
function pollJob(jid, onProgress, onDone, onError) {
  const tick = async () => {
    let j;
    try { j = await api('/api/jobs/' + jid); }
    catch (e) { return onError(e.message); }
    onProgress(j);
    if (j.status === 'running') setTimeout(tick, 320);
    else if (j.status === 'done') onDone(j.result || {});
    else onError(j.error || '任务失败');
  };
  setTimeout(tick, 120);
}

/* ---------------------------------------------------------------- 导入 */
function renderProbe(r) {
  const box = $('probeBox');
  box.classList.remove('hidden');
  if (!r.probe) {
    box.innerHTML = '<div class="probe-head"><strong>' + esc(r.name) +
      '</strong><span>' + fmtSize(r.size) + '</span></div>' +
      '<div class="probe-error">无法解析视频流：' + esc(r.probe_error || '未知原因') +
      '。请确认文件是有效视频 / 动图。</div>';
    return;
  }
  const p = r.probe;
  const cells = [
    ['分辨率', p.width + ' × ' + p.height, ''],
    ['帧率', Number(p.fps.toFixed(3)), 'fps'],
    ['总帧数', p.nb_frames, '帧'],
    ['时长', Number(p.duration.toFixed(2)), 's'],
    ['编码', esc(p.codec), ''],
    ['容器', esc(String(p.container).split(',')[0]), ''],
    ['体积', fmtSize(p.size), ''],
  ];
  box.innerHTML =
    '<div class="probe-head"><strong>' + esc(p.filename) + '</strong>' +
    '<span>原始帧率 ' + esc(p.fps_label) + ' · ' + fmtSize(p.size) + '</span></div>' +
    '<div class="probe-grid">' + cells.map((c) =>
      '<div class="probe-cell"><label>' + c[0] + '</label><b>' + c[1] +
      (c[2] ? ' <small>' + c[2] + '</small>' : '') + '</b></div>').join('') + '</div>';
}

function currentParams() {
  const p = S.probe;
  const mode = S.fpsMode;
  const fallback = p ? p.fps : 24;
  const fps = mode === 'source' ? fallback : (parseFloat($('fpsInput').value) || fallback);
  const start = Math.max(0, parseFloat($('startInput').value) || 0);
  const endVal = parseFloat($('endInput').value);
  const hasEnd = Number.isFinite(endVal) && endVal > 0;
  const duration = p ? p.duration : 0;
  const end = hasEnd ? Math.min(endVal, duration || endVal) : duration;
  const clip = Math.max(0, (end || 0) - start);
  const max = Math.max(0, parseInt($('maxInput').value, 10) || 0);
  return { mode, fps, start, end, hasEnd, clip, max,
           format: $('formatInput').value,
           scale_width: Math.max(0, parseInt($('scaleInput').value, 10) || 0) };
}
function updateEstimate() {
  const p = S.probe;
  if (!p) {
    $('estFrames').textContent = '—';
    $('estFormula').textContent = '帧率 × 时长 = 帧数';
    $('estimateHint').textContent = '导入素材后自动计算预计帧数';
    return;
  }
  const c = currentParams();
  let n = Math.round(c.clip * c.fps);
  if (c.mode === 'source' && c.start === 0 && !c.hasEnd) n = p.nb_frames;
  if (c.max) n = Math.min(n, c.max);
  n = Math.max(1, n);
  $('estFrames').textContent = n.toLocaleString('zh-CN');
  $('estFormula').textContent =
    Number(c.fps.toFixed(2)) + ' fps × ' + c.clip.toFixed(2) + ' s ≈ ' + n +
    ' 张图片' + (c.max ? '（已限制 ' + c.max + ' 帧）' : '');
  $('estimateHint').textContent =
    p.width + '×' + p.height + ' · ' + p.duration.toFixed(2) + 's · ' + esc(p.codec);
}

async function handleFile(file) {
  if (!file) return;
  status('正在导入 ' + file.name);
  $('uploadProgress').classList.remove('hidden');
  $('uploadBar').style.width = '0%';
  try {
    const r = await uploadFile(file, (ratio) => {
      $('uploadBar').style.width = (ratio * 100).toFixed(0) + '%';
      $('uploadText').textContent = '上传 ' + (ratio * 100).toFixed(0) + '% · ' + fmtSize(file.size);
    });
    S.upload = r;
    S.probe = r.probe || null;
    $('nameInput').value = file.name.replace(/\.[^.]+$/, '');
    $('fpsInput').value = S.probe ? Number(S.probe.fps.toFixed(3)) : 24;
    $('startInput').value = '0';
    $('endInput').value = '';
    renderProbe(r);
    updateEstimate();
    $('extractBtn').disabled = !S.probe;
    $('extractTip').textContent = S.probe ? '参数确认后即可开始' : '该素材无法拆解';
    setStep(2);
    toast('素材已导入，确认参数后开始拆帧', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    $('uploadProgress').classList.add('hidden');
    status('就绪');
  }
}

function startExtract() {
  if (!S.upload || !S.probe) return;
  const c = currentParams();
  const payload = {
    upload_id: S.upload.upload_id,
    name: $('nameInput').value.trim() || undefined,
    fps_mode: c.mode, fps: c.fps,
    start: c.start, end: c.hasEnd ? c.end : null,
    format: c.format, scale_width: c.scale_width, max_frames: c.max,
  };
  $('extractBtn').disabled = true;
  $('cancelBtn').classList.remove('hidden');
  $('extractProgress').classList.remove('hidden');
  $('extractBar').style.width = '0%';
  status('正在拆帧…');
  post('/api/extract', payload).then((r) => {
    S.activeJob = r.job_id;
    pollJob(r.job_id,
      (j) => {
        $('extractBar').style.width = j.progress + '%';
        $('extractText').textContent = j.message + ' · ' + j.progress.toFixed(0) + '%';
      },
      (result) => {
        S.activeJob = null;
        S.project = result.project;
        S.selected.clear();
        S.page = 1;
        renderProject();
        refreshProjects();
        setStep(3);
        $('extractBtn').disabled = false;
        $('cancelBtn').classList.add('hidden');
        $('extractProgress').classList.add('hidden');
        status('就绪');
        toast('拆帧完成，共 ' + result.frames + ' 张图片', 'ok');
        $('framesCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
      },
      (err) => {
        S.activeJob = null;
        $('extractBtn').disabled = false;
        $('cancelBtn').classList.add('hidden');
        $('extractProgress').classList.add('hidden');
        status('就绪');
        toast('拆帧失败：' + err, 'error');
      });
  }).catch((e) => {
    $('extractBtn').disabled = false;
    $('cancelBtn').classList.add('hidden');
    $('extractProgress').classList.add('hidden');
    toast(e.message, 'error');
    status('就绪');
  });
}

/* ---------------------------------------------------------------- 项目与帧 */
function renderProjects(projects) {
  const list = projects || [];
  $('projectList').innerHTML = list.length ? list.map((p) =>
    '<div class="project-item ' + (S.project && S.project.id === p.id ? 'active' : '') +
    '" data-id="' + esc(p.id) + '">' +
    '<button class="pi-del" data-del="' + esc(p.id) + '" title="删除项目">×</button>' +
    '<div class="pi-name">' + esc(p.name) + '</div>' +
    '<div class="pi-meta">' + p.frames + ' 帧 · ' + p.fps + ' fps · ' +
    p.width + '×' + p.height + '</div></div>').join('')
    : '<div class="empty-side">暂无项目<br>导入素材后会自动创建</div>';
}
async function refreshProjects() {
  try { const r = await api('/api/projects'); renderProjects(r.projects); }
  catch (e) { /* 静默 */ }
}
async function loadProject(pid) {
  try {
    const r = await api('/api/projects/' + pid);
    S.project = r.project;
    S.selected.clear();
    S.page = 1;
    renderProject();
    setStep(3);
    toast('已载入项目：' + S.project.name, 'ok');
  } catch (e) { toast(e.message, 'error'); }
}
function renderProject() {
  const P = S.project;
  if (!P) return;
  $('framesCard').classList.remove('hidden');
  $('exportCard').classList.remove('hidden');
  $('framesMeta').textContent = P.frames.length + ' 帧 · ' + P.fps + ' fps · ' +
    P.width + '×' + P.height + ' · ' + String(P.format).toUpperCase() +
    ' · 原始 ' + P.frame_count_original + ' 帧';
  $('exportFps').value = P.fps;
  $('undoBtn').disabled = !P.can_undo;
  renderFrames();
  renderExports();
}
function typeLabel(fr) {
  return fr.type === 'replaced' ? '已替换' : fr.type === 'inserted' ? '补帧' : '原帧';
}
function renderFrames() {
  const P = S.project;
  if (!P) return;
  const total = P.frames.length;
  const pages = Math.max(1, Math.ceil(total / S.perPage));
  S.page = Math.min(Math.max(1, S.page), pages);
  const start = (S.page - 1) * S.perPage;
  const slice = P.frames.slice(start, start + S.perPage);
  $('frameGrid').innerHTML = slice.map((fr, i) => {
    const idx = start + i;
    const dir = fr.has_thumb ? 'thumbs' : 'frames';
    const label = typeLabel(fr);
    const cls = fr.type !== 'original' ? fr.type : '';
    return '<div class="frame-card ' + cls + (S.selected.has(fr.id) ? ' selected' : '') +
      '" data-id="' + esc(fr.id) + '" data-idx="' + idx + '">' +
      '<span class="frame-check">✓</span>' +
      (fr.type !== 'original' ? '<span class="frame-badge">' + label + '</span>' : '') +
      '<img class="frame-thumb" loading="lazy" alt="帧 ' + (idx + 1) +
      '" src="/media/' + esc(P.id) + '/' + dir + '/' + encodeURIComponent(fr.thumb) + '">' +
      '<div class="frame-foot"><span class="fnum">' + String(idx + 1).padStart(4, '0') +
      '</span><span class="ftype">' + label + ' · ' + fmtSize(fr.size) + '</span></div></div>';
  }).join('');
  $('pager').innerHTML =
    '<button data-p="prev"' + (S.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
    '<span>第 ' + S.page + ' / ' + pages + ' 页 · 共 ' + total + ' 帧</span>' +
    '<button data-p="next"' + (S.page >= pages ? ' disabled' : '') + '>下一页</button>' +
    '<select id="perPageSel">' + [24, 48, 96, 192].map((n) =>
      '<option value="' + n + '"' + (n === S.perPage ? ' selected' : '') + '>每页 ' + n +
      ' 帧</option>').join('') + '</select>';
  updateSelectionHint();
}
function updateSelectionHint() {
  $('selectionHint').textContent = S.selected.size
    ? '已选中 ' + S.selected.size + ' 帧（跨页保留选择）· 单击缩略图查看大图，右上角方框可取消勾选'
    : '单击缩略图查看大图，点右上角方框勾选；插入补帧后可选中它并替换成本地插帧结果。';
}
function toggleSelect(id, card) {
  if (S.selected.has(id)) S.selected.delete(id); else S.selected.add(id);
  if (card) card.classList.toggle('selected', S.selected.has(id));
  updateSelectionHint();
}

/* ---------------------------------------------------------------- 预览 */
function openPreview(idx) {
  if (!S.project || idx < 0 || idx >= S.project.frames.length) return;
  S.previewIndex = idx;
  updatePreview();
  $('previewModal').classList.remove('hidden');
}
function updatePreview() {
  const P = S.project;
  if (!P) return;
  const fr = P.frames[S.previewIndex];
  if (!fr) return;
  $('previewImg').src = '/media/' + esc(P.id) + '/frames/' + encodeURIComponent(fr.file);
  $('previewTitle').textContent = '第 ' + (S.previewIndex + 1) + ' / ' + P.frames.length + ' 帧';
  $('previewSub').textContent = P.width + '×' + P.height + ' · ' + P.fps + ' fps';
  $('previewInfo').textContent = esc(fr.file) + ' · ' + fmtSize(fr.size) + ' · ' + typeLabel(fr);
}
function stepPreview(delta) {
  if (!S.project) return;
  const n = S.project.frames.length;
  S.previewIndex = Math.min(n - 1, Math.max(0, S.previewIndex + delta));
  updatePreview();
}
function closePreview() { $('previewModal').classList.add('hidden'); }

/* ---------------------------------------------------------------- 帧编辑 */
async function applyEdit(action, ids, payload = {}) {
  if (!S.project) return;
  const body = Object.assign({ action, ids: ids || [...S.selected] }, payload);
  try {
    const r = await post('/api/projects/' + S.project.id + '/edit', body);
    S.project = r.project;
    S.selected.clear();
    renderProject();
    refreshProjects();
    const tips = {
      replace: '帧已替换', insert: '补帧已插入，记得替换成插帧结果',
      delete: '帧已删除', double: '帧数翻倍，导出帧率已同步加倍',
      undo: '已撤销上一步', restore: '已还原为原始拆帧结果',
    };
    toast(tips[action] || '操作完成', 'ok');
    if (!$('previewModal').classList.contains('hidden')) closePreview();
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    S.pendingAction = null;
    S.pendingIds = [];
  }
}
function needSelection() {
  if (!S.selected.size) { toast('请先勾选目标帧', 'error'); return false; }
  return true;
}

/* ---------------------------------------------------------------- 导出 */
function renderExports() {
  const list = (S.project && S.project.exports) || [];
  $('exportList').innerHTML = list.length ? list.map((e) =>
    '<div class="export-item"><span class="ei-name">' + esc(e.name) + '</span>' +
    '<span class="ei-meta">' + String(e.type).toUpperCase() + ' · ' + e.frames +
    ' 帧 · ' + e.fps + ' fps · ' + fmtSize(e.size) + '</span>' +
    '<a href="/api/download?path=' + encodeURIComponent(e.path) + '" download>下载</a></div>').join('')
    : '<div class="empty-side">还没有导出记录</div>';
}
async function startExport(type) {
  if (!S.project) return;
  const fps = parseFloat($('exportFps').value) || S.project.fps;
  $('exportProgress').classList.remove('hidden');
  $('exportBar').style.width = '0%';
  status('正在导出…');
  try {
    const r = await post('/api/projects/' + S.project.id + '/export', { type, fps });
    pollJob(r.job_id,
      (j) => {
        $('exportBar').style.width = j.progress + '%';
        $('exportText').textContent = j.message + ' · ' + j.progress.toFixed(0) + '%';
      },
      async (result) => {
        $('exportProgress').classList.add('hidden');
        setStep(4);
        status('就绪');
        toast('导出完成：' + result.file, 'ok');
        try {
          const fresh = await api('/api/projects/' + S.project.id);
          S.project = fresh.project;
          renderExports();
        } catch (e) { /* 静默 */ }
        refreshProjects();
      },
      (err) => {
        $('exportProgress').classList.add('hidden');
        status('就绪');
        toast('导出失败：' + err, 'error');
      });
  } catch (e) {
    $('exportProgress').classList.add('hidden');
    status('就绪');
    toast(e.message, 'error');
  }
}

/* ---------------------------------------------------------------- 事件绑定 */
function init() {
  const theme = localStorage.getItem('fs-theme') || 'light';
  document.body.dataset.theme = theme;
  $('themeBtn').onclick = () => {
    const next = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
    document.body.dataset.theme = next;
    localStorage.setItem('fs-theme', next);
  };

  api('/api/env').then((env) => {
    S.env = env;
    $('envPill').querySelector('span').textContent =
      'ffmpeg 就绪 · Python ' + env.python + ' · ' + env.projects.length + ' 个项目';
    $('workspaceText').textContent = env.workspace;
    renderProjects(env.projects);
  }).catch((e) => {
    $('envPill').classList.add('bad');
    $('envPill').querySelector('span').textContent = '服务异常：' + e.message;
  });

  const dz = $('dropzone'), fi = $('fileInput');
  dz.onclick = () => fi.click();
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('over'); };
  dz.ondragleave = () => dz.classList.remove('over');
  dz.ondrop = (e) => {
    e.preventDefault(); dz.classList.remove('over');
    if (e.dataTransfer.files && e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  };
  fi.onchange = (e) => { if (e.target.files[0]) handleFile(e.target.files[0]); e.target.value = ''; };

  document.querySelectorAll('#fpsMode .seg').forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll('#fpsMode .seg').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      S.fpsMode = btn.dataset.value;
      $('fpsInput').disabled = S.fpsMode === 'source';
      updateEstimate();
    };
  });
  ['fpsInput', 'startInput', 'endInput', 'maxInput'].forEach((id) => {
    $(id).addEventListener('input', updateEstimate);
  });
  $('extractBtn').onclick = startExtract;
  $('cancelBtn').onclick = () => {
    if (S.activeJob) post('/api/jobs/' + S.activeJob + '/cancel', {}).catch(() => {});
  };

  $('newProjectBtn').onclick = () => {
    S.project = null; S.upload = null; S.probe = null; S.selected.clear(); S.page = 1;
    $('framesCard').classList.add('hidden');
    $('exportCard').classList.add('hidden');
    $('probeBox').classList.add('hidden');
    $('extractBtn').disabled = true;
    $('extractTip').textContent = '先导入素材';
    $('nameInput').value = '';
    $('endInput').value = '';
    $('startInput').value = '0';
    updateEstimate();
    setStep(1);
    refreshProjects();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  $('projectList').addEventListener('click', async (e) => {
    const del = e.target.closest('[data-del]');
    if (del) {
      e.stopPropagation();
      const pid = del.dataset.del;
      if (!window.confirm('删除该项目及其所有帧图片和导出文件？此操作不可恢复。')) return;
      try {
        await api('/api/projects/' + pid, { method: 'DELETE' });
        if (S.project && S.project.id === pid) S.project = null;
        $('framesCard').classList.add('hidden');
        $('exportCard').classList.add('hidden');
        refreshProjects();
        toast('项目已删除', 'ok');
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    const item = e.target.closest('.project-item');
    if (item) loadProject(item.dataset.id);
  });

  $('cleanupBtn').onclick = async () => {
    try {
      const r = await post('/api/cleanup', {});
      toast('已清理 ' + r.removed + ' 个临时文件，释放 ' + r.freed, 'ok');
    } catch (e) { toast(e.message, 'error'); }
  };

  $('frameGrid').addEventListener('click', (e) => {
    const card = e.target.closest('.frame-card');
    if (!card) return;
    if (e.target.closest('.frame-check')) return toggleSelect(card.dataset.id, card);
    openPreview(Number(card.dataset.idx));
  });
  $('pager').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-p]');
    if (!btn) return;
    S.page += btn.dataset.p === 'next' ? 1 : -1;
    renderFrames();
  });
  $('pager').addEventListener('change', (e) => {
    if (e.target.id === 'perPageSel') {
      S.perPage = Number(e.target.value);
      S.page = 1;
      renderFrames();
    }
  });

  $('selectAllBtn').onclick = () => {
    const P = S.project; if (!P) return;
    const start = (S.page - 1) * S.perPage;
    P.frames.slice(start, start + S.perPage).forEach((fr) => S.selected.add(fr.id));
    renderFrames();
  };
  $('clearSelBtn').onclick = () => { S.selected.clear(); renderFrames(); };
  $('replaceBtn').onclick = () => {
    if (!needSelection()) return;
    if (S.selected.size !== 1) { toast('替换帧请只勾选一帧', 'error'); return; }
    S.pendingAction = 'replace';
    S.pendingIds = [...S.selected];
    $('frameFileInput').click();
  };
  $('insertBtn').onclick = () => {
    if (!needSelection()) return;
    applyEdit('insert');
  };
  $('doubleBtn').onclick = () => {
    if (!S.project) return;
    if (!window.confirm('将在每一帧后插入一张占位帧，帧数翻倍并把合成帧率加倍。\n确定继续？')) return;
    applyEdit('double', []);
  };
  $('deleteBtn').onclick = () => {
    if (!needSelection()) return;
    if (!window.confirm('确定删除选中的 ' + S.selected.size + ' 帧？可用「撤销」恢复。')) return;
    applyEdit('delete');
  };
  $('undoBtn').onclick = () => applyEdit('undo', []);
  $('restoreBtn').onclick = () => {
    if (!window.confirm('还原为最初拆帧的帧序列？所有替换与补帧都会被丢弃（可撤销）。')) return;
    applyEdit('restore', []);
  };
  $('frameFileInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file || !S.pendingAction || !S.project) return;
    status('正在上传帧图片…');
    try {
      const r = await uploadFile(file, null);
      await applyEdit(S.pendingAction, S.pendingIds, { upload_id: r.upload_id });
    } catch (err) { toast(err.message, 'error'); }
    finally { status('就绪'); }
  });

  $('closePreview').onclick = closePreview;
  $('modalBackdrop').onclick = closePreview;
  $('prevFrame').onclick = () => stepPreview(-1);
  $('nextFrame').onclick = () => stepPreview(1);
  $('previewReplace').onclick = () => {
    if (!S.project) return;
    S.pendingAction = 'replace';
    S.pendingIds = [S.project.frames[S.previewIndex].id];
    $('frameFileInput').click();
  };
  document.addEventListener('keydown', (e) => {
    if ($('previewModal').classList.contains('hidden')) return;
    if (e.key === 'Escape') closePreview();
    else if (e.key === 'ArrowLeft') stepPreview(-1);
    else if (e.key === 'ArrowRight') stepPreview(1);
  });

  document.querySelectorAll('[data-export]').forEach((btn) => {
    btn.onclick = () => startExport(btn.dataset.export);
  });
}

init();
