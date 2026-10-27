/* Generate-VASP Webview 主逻辑。后端状态由 Python 服务提供，这里只负责呈现与交互。 */
'use strict';

const vscode = typeof acquireVsCodeApi === 'function'
  ? acquireVsCodeApi()
  : { postMessage: (message) => console.log('[mock]', message) };

const $ = (id) => document.getElementById(id);
const editor = $('editor');
const highlight = $('highlight');

const S = {
  initialized: false,
  initializing: false,
  check: null,
  settings: {},
  options: null,
  defaults: null,
  summary: null,
  potcar: null,
  files: {},
  serverText: {},
  activeFiles: [],
  activeTab: 'structure',
  structureRevision: null,
  generation: 0,
  masks: [],
  hints: {},
  status: null,
  fix: null,
  llm: null,
  canUndo: false,
  canRedo: false,
  mode: 'rotate',
  viewerReady: false,
  viewerQueue: null,
  structurePayload: null,
  pendingOpenPath: null,
  lastSource: null,
  structureSources: { open: [], root: '' },
  tree: { root: '', nodes: new Map(), expanded: new Set(), loading: new Set() },
  treeVisible: true,
  dirtyFile: null,
  editTimer: null,
  optionsTimer: null,
  optionsSeq: 0,
  optionsApplied: 0,
  optionsRevision: 0,
  segHandlers: Object.create(null),
  find: { open: false, query: '', caseSensitive: false, matches: [], current: -1 },
  llmHint: '',
  llmStartedAt: 0,
  llmTimer: null,
  toastTimer: null,
  findTimer: null,
  llmRefreshTimer: null,
  highlightPending: false,
  applyingSelection: false,
  projectKey: '',
  spinKey: '',
  uKey: '',
  potKey: '',
  mixKey: '',
  ngxfKey: '',
  optKey: '',
  togglesKey: '',
  tabsKey: '',
  modeKey: '',
  bondKey: '',
};

let rpcSeq = 0;
const rpcPending = new Map();
let hostSeq = 0;
const hostPending = new Map();

/* ------------------------------------------------------------------ 基础工具 */
function post(message) {
  vscode.postMessage(message);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setInputValue(element, value) {
  if (!element || document.activeElement === element) return;
  const text = value === undefined || value === null ? '' : String(value);
  if (element.value !== text) element.value = text;
}

function setCheckbox(element, value) {
  if (!element) return;
  const checked = Boolean(value);
  if (element.checked !== checked) element.checked = checked;
}

function setHint(id, hint) {
  const element = $(id);
  if (!element) return;
  const text = hint && hint.text ? hint.text : '';
  if (element.textContent !== text) element.textContent = text;
  element.style.color = text && hint.color ? hint.color : '';
  element.classList.toggle('hidden', !text);
}

function showToast(message, error = false) {
  const toast = $('toast');
  toast.textContent = String(message || '');
  toast.className = 'toast' + (error ? ' error' : '');
  toast.classList.remove('hidden');
  if (S.toastTimer) clearTimeout(S.toastTimer);
  S.toastTimer = setTimeout(() => {
    toast.classList.add('hidden');
    S.toastTimer = null;
  }, error ? 6000 : 4000);
}

function reportWebviewError(where, error) {
  const detail = error && (error.stack || error.message) ? (error.stack || error.message) : String(error);
  const message = `${where}: ${detail}`;
  console.error(message);
  try {
    post({ type: 'log', message });
  } catch {
    // ignore
  }
}

function withKnown(params = {}) {
  return Object.assign({}, params, { knownStructureRevision: S.structureRevision });
}

function rpc(method, params = {}, timeoutMs) {
  return new Promise((resolve, reject) => {
    const id = ++rpcSeq;
    const timeout = timeoutMs || (method === 'generate' ? 180000 : method === 'loadStructure' ? 300000 : 120000);
    const timer = setTimeout(() => {
      rpcPending.delete(id);
      reject(new Error(`${method} 请求超时`));
    }, timeout);
    rpcPending.set(id, { resolve, reject, timer });
    post({ type: 'rpc', id, method, params });
  });
}

function host(action, payload = {}) {
  return new Promise((resolve, reject) => {
    const id = ++hostSeq;
    const timer = setTimeout(() => {
      hostPending.delete(id);
      reject(new Error(`${action} 操作超时`));
    }, 600000);
    hostPending.set(id, { resolve, reject, timer });
    post(Object.assign({ type: 'host', id, action }, payload));
  });
}

function sendViewer(message) {
  try {
    window.postMessage(message, '*');
  } catch (error) {
    console.error(error);
  }
}

function queueViewerState(state) {
  S.viewerQueue = state;
  if (S.viewerReady) {
    sendViewer({ type: 'state', payload: state });
    S.viewerQueue = null;
  }
}

function persistSetting(key, value) {
  void host('setSetting', { key, value }).catch((error) => {
    console.warn('保存设置失败', error);
  });
}

/* ------------------------------------------------------------------ 消息处理 */
window.addEventListener('message', (event) => {
  const message = event.data || {};
  switch (message.type) {
    case 'init':
      void onInit(message);
      break;
    case 'openPath':
      queueOpen(message.path);
      break;
    case 'structureTabs':
      onStructureTabs(message);
      break;
    case 'rpcResult': {
      const pending = rpcPending.get(message.id);
      if (!pending) break;
      rpcPending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.ok) pending.resolve(message.result);
      else pending.reject(new Error(message.error || '后端请求失败'));
      break;
    }
    case 'hostResult': {
      const pending = hostPending.get(message.id);
      if (!pending) break;
      hostPending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.ok) pending.resolve(message.value);
      else pending.reject(new Error(message.error || '宿主操作失败'));
      break;
    }
    case 'llmProgress':
      void onLlmProgress(message);
      break;
    case 'llmDone':
      showToast(message.message || 'LLM 自旋分析完成');
      void refreshState();
      break;
    case 'llmFailed':
      showToast(message.message || 'LLM 调用失败', true);
      void refreshState();
      break;
    case 'backendExit':
      onBackendExit(message);
      break;
    case 'backendRestarted':
      void onBackendRestarted(message);
      break;
    case 'settingsChanged':
      onSettingsChanged(message);
      break;
    case 'ready':
    case 'selectAtoms':
    case 'requestMode':
    case 'reportError':
      handleViewerMessage(message);
      break;
    default:
      break;
  }
});

async function onInit(message) {
  S.check = message.check || { ok: false, error: '后端没有返回环境信息' };
  S.settings = message.settings || {};
  S.structureSources = message.structures || { open: [], root: '' };
  S.treeVisible = !S.structureSources.open.length;
  if (S.tree.root !== S.structureSources.root) {
    S.tree = { root: S.structureSources.root, nodes: new Map(), expanded: new Set(), loading: new Set() };
  }
  updateEnvBadge();
  if (!S.check.ok) {
    showFatal('Python 环境未就绪', S.check.error || '请安装 numpy / ase 并重试。');
    S.initializing = false;
    return;
  }
  hideFatal();
  S.initializing = true;
  S.initialized = true;
  try {
    await refreshState();
  } catch (error) {
    reportWebviewError('refreshState', error);
    showToast(error.message, true);
  }
  S.initializing = false;
  if (S.pendingOpenPath) {
    const path = S.pendingOpenPath;
    S.pendingOpenPath = null;
    await loadStructure(path);
  }
  renderStructureSources();
}

function onStructureTabs(message) {
  const previousCount = (S.structureSources.open || []).length;
  S.structureSources = { open: message.open || [], root: message.root || '' };
  if (S.tree.root !== S.structureSources.root) {
    S.tree = { root: S.structureSources.root, nodes: new Map(), expanded: new Set(), loading: new Set() };
  }
  if (!S.structureSources.open.length) S.treeVisible = true;
  else if (previousCount === 0) S.treeVisible = false;
  renderStructureSources();
  if (!S.lastSource && S.structureSources.open.length && S.initialized && !S.initializing) {
    const active = S.structureSources.open.find((item) => item.active)?.path
      || S.structureSources.open[0]?.path;
    if (active) void loadStructure(active);
  }
}

function queueOpen(path) {
  if (!path) return;
  if (S.initialized && !S.initializing) {
    void loadStructure(path);
  } else {
    S.pendingOpenPath = path;
  }
}

async function onLlmProgress(message) {
  if (!S.llm) return;
  S.llm.msg = message.msg || S.llm.msg;
  renderLLM();
  if (S.llmRefreshTimer) clearTimeout(S.llmRefreshTimer);
  S.llmRefreshTimer = setTimeout(() => {
    S.llmRefreshTimer = null;
    void refreshState();
  }, 250);
}

function onBackendExit(message) {
  S.check = { ok: false, error: message.message || 'Python 服务已退出。' };
  updateEnvBadge();
  showFatal('Python 服务已退出', S.check.error);
}

async function onBackendRestarted(message) {
  S.check = message.check || { ok: false, error: '后端重启失败' };
  updateEnvBadge();
  if (!S.check.ok) {
    showFatal('Python 环境未就绪', S.check.error || '请检查 Python 与依赖。');
    return;
  }
  hideFatal();
  S.initialized = true;
  S.initializing = false;
  S.structurePayload = null;
  S.masks = [];
  sendViewer({ type: 'state', payload: { clear: true } });
  if (S.lastSource) {
    await loadStructure(S.lastSource);
  } else if (S.pendingOpenPath) {
    const path = S.pendingOpenPath;
    S.pendingOpenPath = null;
    await loadStructure(path);
  } else {
    await refreshState();
  }
}

function onSettingsChanged(message) {
  S.settings = message.settings || {};
  if (S.options) {
    if (S.settings.potcarDir) S.options.potcarDir = S.settings.potcarDir;
    S.options.boundary = Boolean(S.settings.boundary);
    S.options.bonds = S.settings.bonds || 'half';
    renderSettings();
    renderViewerOptions();
    scheduleOptions();
  }
}

/* ------------------------------------------------------------------ 状态同步 */
async function refreshState() {
  if (!S.check || !S.check.ok) return;
  const state = await rpc('getState', withKnown());
  applyState(state);
}

function applyState(state) {
  if (!state || !state.options) return;
  S.options = state.options;
  S.defaults = state.defaults;
  S.summary = state.summary;
  S.potcar = state.potcar;
  S.files = state.files || {};
  S.serverText = {};
  for (const [name, record] of Object.entries(S.files)) S.serverText[name] = record.text;
  S.activeFiles = state.activeFiles || [];
  S.structureRevision = state.structureRevision;
  S.generation = state.generation;
  S.masks = state.masks || [];
  S.hints = state.hints || {};
  S.status = state.status;
  S.fix = state.fix;
  S.llm = state.llm;
  S.canUndo = state.canUndo;
  S.canRedo = state.canRedo;
  if (state.summary && state.summary.loaded) {
    S.lastSource = state.source || S.lastSource;
  } else {
    S.lastSource = null;
  }

  if (state.clearViewer) {
    S.structurePayload = null;
    queueViewerState({ clear: true });
  } else if (state.structure) {
    S.structurePayload = state.structure;
    queueViewerState({
      clear: false,
      structure: state.structure,
      masks: state.masks,
      mode: S.mode,
      generation: state.generation,
      keepView: Boolean(state.keepView),
      view: state.view,
    });
  } else if (state.masks) {
    if (S.viewerReady) {
      sendViewer({
        type: 'state',
        payload: { masks: state.masks, mode: S.mode, generation: state.generation },
      });
    } else if (S.viewerQueue && S.viewerQueue.structure) {
      S.viewerQueue.masks = state.masks;
      S.viewerQueue.generation = state.generation;
    } else {
      S.viewerQueue = { masks: state.masks, mode: S.mode, generation: state.generation };
    }
  }

  if (S.activeTab !== 'structure' && !S.activeFiles.includes(S.activeTab)) {
    S.activeTab = 'structure';
  }
  renderAll(state);
  syncActiveEditor();
}

function syncActiveEditor() {
  if (S.activeTab === 'structure') return;
  const name = S.activeTab;
  if (S.dirtyFile === name) return;
  const file = S.files[name];
  if (!file) return;
  const server = S.serverText[name] ?? file.text ?? '';
  if (editor.value !== server) setEditorValue(server, { reset: false });
}

function renderAll(state) {
  updateEnvBadge();
  renderSummary();
  renderProjects();
  renderSettings();
  renderViewerOptions();
  renderTabs();
  renderPage(false);
  renderFileToggles();
  renderStatus();
  renderHints();
  renderLLM();
  renderFix();
  try {
    renderStructureSources();
  } catch (error) {
    reportWebviewError('结构文件来源 UI', error);
  }
}

/* ------------------------------------------------------------------ 顶部 / 状态 */
function updateEnvBadge() {
  const badge = $('envBadge');
  if (!badge) return;
  if (!S.check) {
    badge.textContent = '检查环境…';
    badge.className = 'env-badge';
    badge.title = '';
    return;
  }
  if (!S.check.ok) {
    badge.textContent = '环境未就绪';
    badge.className = 'env-badge bad';
    badge.title = S.check.error || '';
    return;
  }
  badge.textContent = `Python ${S.check.python || '?'} · numpy ${S.check.numpy || '?'}`;
  badge.className = 'env-badge ok';
  badge.title = S.check.pythonExecutable || S.check.pythonPath || '';
}

function renderStatus() {
  const status = S.status || {};
  const outdir = status.outdir || '';
  $('outLabel').textContent = outdir || '结构文件所在目录';
  $('outLabel').title = outdir;
  $('openFolder').disabled = !outdir;
  $('generateBtn').disabled = !status.canGenerate;
  setHint('statusText', { text: status.text || '', color: status.color || '' });
  $('statusText').classList.toggle('error', status.color === '#FF3B30');
}

function renderHints() {
  const hints = S.hints || {};
  setHint('sysHint', hints.sys);
  setHint('spinHint', hints.spin);
  setHint('uHint', hints.u);
  setHint('mixHint', hints.mix);
  setHint('ngxfHint', hints.ngxf);
  setHint('kHint', hints.k);
  setHint('klineHint', hints.kline);
  setHint('potHint', hints.pot);
  setHint('cleanHint', hints.clean);
  setHint('solHint', hints.sol);
  setHint('lobHint', hints.lob);
  setHint('optHint', hints.opt);

  const tpl = hints.tpl && hints.tpl.text ? hints.tpl.text : '';
  const note = $('tplNote');
  note.textContent = tpl;
  note.classList.toggle('hidden', !tpl);
  setHint('loadNote', hints.load);
}

/* ------------------------------------------------------------------ 结构摘要 */
function renderSummary() {
  const summary = S.summary || { loaded: false };
  const loaded = Boolean(summary.loaded);
  $('fileBox').classList.toggle('hidden', !loaded);
  if (!loaded) {
    $('chips').replaceChildren();
    return;
  }
  $('fileName').textContent = summary.sourceName || '';
  $('fileDir').textContent = summary.sourceDir || '';
  $('fileDir').title = summary.sourceDir || '';
  const chips = [];
  if (summary.formula) {
    const span = document.createElement('span');
    span.className = 'chip badge';
    span.textContent = summary.formula;
    chips.push(span);
  }
  const addChip = (text) => {
    const span = document.createElement('span');
    span.className = 'chip';
    span.textContent = text;
    chips.push(span);
  };
  addChip(`${summary.nAtoms} 个原子`);
  addChip(summary.detectedLabel || '');
  if (summary.gaps) {
    addChip(`真空 a/b/c  ${summary.gaps.a.toFixed(1)} / ${summary.gaps.b.toFixed(1)} / ${summary.gaps.c.toFixed(1)} Å`);
  }
  $('chips').replaceChildren(...chips);
}

/* ------------------------------------------------------------------ 结构文件来源 */
function baseName(file) {
  const text = String(file || '').replace(/[\\/]+$/, '');
  return text.split(/[\\/]/).pop() || text;
}

function renderStructureSources() {
  const sources = S.structureSources || { open: [], root: '' };
  const open = Array.isArray(sources.open) ? sources.open : [];
  const box = $('openStructureBox');
  const select = $('openStructureSelect');
  const hint = $('openStructureHint');
  if (!box || !select || !hint) return;
  box.classList.toggle('hidden', !open.length);
  if (open.length) {
    const current = S.lastSource || '';
    const key = JSON.stringify([open.map((item) => [item.path, item.name, item.relative, item.active]), current]);
    if (select.dataset.key !== key) {
      select.replaceChildren();
      const seen = new Set();
      for (const item of open) {
        const option = document.createElement('option');
        option.value = item.path;
        option.textContent = `${item.name}${item.relative && item.relative !== item.name ? ' — ' + item.relative : ''}${item.active ? ' · 当前标签页' : ''}`;
        select.append(option);
        seen.add(item.path);
      }
      if (current && !seen.has(current)) {
        const option = document.createElement('option');
        option.value = current;
        option.textContent = `${baseName(current)} — 当前加载`;
        select.append(option);
      }
      select.dataset.key = key;
    }
    const preferred = current && [...select.options].some((item) => item.value === current)
      ? current
      : (open.find((item) => item.active)?.path || open[0]?.path || '');
    if (document.activeElement !== select && select.value !== preferred) select.value = preferred;
    hint.textContent = `${open.length} 个已打开的结构文件 · 默认使用当前标签页`;
    hint.classList.remove('hidden');
  } else {
    select.dataset.key = '';
    hint.textContent = '';
    hint.classList.add('hidden');
  }

  const root = sources.root || '';
  if (S.tree.root !== root) {
    S.tree = { root, nodes: new Map(), expanded: new Set(), loading: new Set() };
  }
  const treeBox = $('treeBox');
  const tree = $('tree');
  const treeHint = $('treeHint');
  const toggle = $('toggleTree');
  if (!treeBox || !tree || !treeHint || !toggle) return;
  treeBox.classList.remove('hidden');
  if (!root) {
    tree.classList.add('hidden');
    toggle.classList.add('hidden');
    treeHint.textContent = '没有打开的项目文件夹；请使用“选择文件…”。';
    treeHint.classList.remove('hidden');
    return;
  }
  toggle.classList.remove('hidden');
  tree.classList.toggle('hidden', !S.treeVisible);
  toggle.textContent = S.treeVisible ? '收起' : '展开';
  if (!S.treeVisible) {
    treeHint.textContent = '从项目目录选择结构文件';
    treeHint.classList.remove('hidden');
    return;
  }
  if (!S.tree.nodes.has(root) && !S.tree.loading.has(root)) {
    S.tree.expanded.add(root);
    void loadTreeDir(root);
  }
  renderTree();
}

function renderTree() {
  const tree = $('tree');
  const hint = $('treeHint');
  if (!tree || !hint) return;
  const root = S.tree.root;
  if (!root) {
    tree.replaceChildren();
    return;
  }
  const scroll = tree.scrollTop;
  tree.replaceChildren();
  const expanded = S.tree.expanded.has(root);
  const row = document.createElement('div');
  row.className = 'tree-row tree-root';
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'tree-toggle';
  toggle.textContent = S.tree.loading.has(root) ? '…' : expanded ? '▾' : '▸';
  const name = document.createElement('span');
  name.className = 'tree-name';
  name.textContent = baseName(root) || root;
  const pathLabel = document.createElement('span');
  pathLabel.className = 'tree-path';
  pathLabel.textContent = '项目根目录';
  pathLabel.title = root;
  row.append(toggle, name, pathLabel);
  row.addEventListener('click', () => toggleTreeDir(root));
  tree.append(row);
  if (expanded) renderTreeDir(root, 0, tree);
  const node = S.tree.nodes.get(root);
  if (S.tree.loading.has(root)) hint.textContent = '正在读取项目目录…';
  else if (node && node.error) hint.textContent = node.error;
  else if (node && !node.dirs.length && !node.files.length) hint.textContent = '根目录没有直接找到结构文件，展开子目录继续查找。';
  else if (node && node.truncated) hint.textContent = '目录条目过多，只显示前 300 项。';
  else hint.textContent = '点击目录展开，点击结构文件加载。';
  hint.classList.remove('hidden');
  tree.scrollTop = scroll;
}

function treeRow(level) {
  const row = document.createElement('div');
  row.className = 'tree-row';
  row.style.paddingLeft = `${level * 14}px`;
  return row;
}

function treePathLabel(file) {
  const label = document.createElement('span');
  label.className = 'tree-path';
  label.textContent = file.relative || '';
  label.title = file.path || '';
  return label;
}

function renderTreeDir(dir, level, container) {
  const node = S.tree.nodes.get(dir);
  if (!node) {
    if (S.tree.loading.has(dir)) {
      const row = treeRow(level + 1);
      const note = document.createElement('span');
      note.className = 'tree-note';
      note.textContent = '正在读取…';
      row.append(note);
      container.append(row);
    }
    return;
  }
  if (node.error) {
    const row = treeRow(level + 1);
    const note = document.createElement('span');
    note.className = 'tree-note error';
    note.textContent = node.error;
    row.append(note);
    container.append(row);
    return;
  }
  for (const child of node.dirs) {
    const expanded = S.tree.expanded.has(child.path);
    const row = treeRow(level + 1);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'tree-toggle';
    toggle.textContent = S.tree.loading.has(child.path) ? '…' : expanded ? '▾' : '▸';
    const name = document.createElement('span');
    name.className = 'tree-name';
    name.textContent = child.name;
    row.append(toggle, name, treePathLabel(child));
    row.addEventListener('click', () => toggleTreeDir(child.path));
    container.append(row);
    if (expanded) renderTreeDir(child.path, level + 1, container);
  }
  for (const file of node.files) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'tree-row tree-file-row';
    row.style.paddingLeft = `${(level + 1) * 14}px`;
    if (S.lastSource === file.path) row.classList.add('selected');
    const spacer = document.createElement('span');
    spacer.className = 'tree-toggle';
    spacer.textContent = '';
    const name = document.createElement('span');
    name.className = 'tree-file';
    name.textContent = file.name;
    row.append(spacer, name, treePathLabel(file));
    row.addEventListener('click', () => { void loadStructure(file.path); });
    container.append(row);
  }
  if (!node.dirs.length && !node.files.length) {
    const row = treeRow(level + 1);
    const note = document.createElement('span');
    note.className = 'tree-note';
    note.textContent = '（空）';
    row.append(note);
    container.append(row);
  }
}

function toggleTreeDir(dir) {
  if (S.tree.expanded.has(dir)) {
    S.tree.expanded.delete(dir);
  } else {
    S.tree.expanded.add(dir);
    if (!S.tree.nodes.has(dir)) void loadTreeDir(dir);
  }
  renderTree();
}

async function loadTreeDir(dir) {
  if (!dir || S.tree.nodes.has(dir) || S.tree.loading.has(dir)) return;
  S.tree.loading.add(dir);
  renderTree();
  try {
    const result = await host('listDirectory', { path: dir });
    S.tree.nodes.set(dir, {
      dirs: result.dirs || [],
      files: result.files || [],
      truncated: Boolean(result.truncated),
      error: '',
    });
  } catch (error) {
    S.tree.nodes.set(dir, { dirs: [], files: [], truncated: false, error: error.message });
  } finally {
    S.tree.loading.delete(dir);
    renderTree();
  }
}

async function refreshOpenStructures() {
  try {
    const result = await host('getOpenStructures');
    onStructureTabs({ open: result.open || [], root: result.root || '' });
  } catch (error) {
    showToast(error.message, true);
  }
}

function toggleTreeVisible() {
  S.treeVisible = !S.treeVisible;
  if (S.treeVisible) {
    const root = S.tree.root;
    if (root && !S.tree.nodes.has(root) && !S.tree.loading.has(root)) {
      S.tree.expanded.add(root);
      void loadTreeDir(root);
    }
  }
  renderStructureSources();
}

/* ------------------------------------------------------------------ 计算项目 */
function renderProjects() {
  if (!S.defaults || !S.defaults.calcs) return;
  const calcs = S.defaults.calcs;
  const key = JSON.stringify(calcs);
  const grid = $('projectGrid');
  if (key !== S.projectKey) {
    grid.replaceChildren();
    calcs.forEach((calc, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'project-card';
      button.dataset.index = String(index);
      const number = document.createElement('span');
      number.className = 'project-no';
      number.textContent = calc.no;
      const name = document.createElement('span');
      name.className = 'project-name';
      name.textContent = calc.name;
      button.append(number, name);
      button.addEventListener('click', () => {
        if (!S.options) return;
        S.options.calc = index;
        renderProjects();
        renderSettings();
        scheduleOptions();
      });
      grid.append(button);
    });
    S.projectKey = key;
  }
  grid.querySelectorAll('.project-card').forEach((button) => {
    button.classList.toggle('active', Number(button.dataset.index) === S.options.calc);
  });
}

/* ------------------------------------------------------------------ 通用分段控件 */
function buildSegmented(element, items, current, handler) {
  if (!element) return;
  if (element.id) S.segHandlers[element.id] = handler;
  const key = JSON.stringify(items);
  if (element.dataset.key !== key) {
    element.replaceChildren();
    for (const item of items) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'segment';
      button.dataset.value = String(item.key);
      button.textContent = item.label;
      element.append(button);
    }
    element.dataset.key = key;
  }
  element.querySelectorAll('.segment').forEach((button) => {
    button.classList.toggle('active', button.dataset.value === String(current));
  });
}

function onSegmentedClick(event) {
  const button = event.target && event.target.closest
    ? event.target.closest('.segment[data-value]')
    : null;
  if (!button) return;
  const group = button.closest('.segmented');
  if (!group || !group.id) return;
  const handler = S.segHandlers[group.id];
  if (!handler) return;
  try {
    post({ type: 'log', message: `click ${group.id} = ${button.dataset.value}` });
  } catch {
    // ignore
  }
  handler(button.dataset.value);
}

function scheduleOptions() {
  if (!S.options) return;
  S.optionsRevision += 1;
  if (S.optionsTimer) clearTimeout(S.optionsTimer);
  S.optionsTimer = setTimeout(() => {
    S.optionsTimer = null;
    void applyOptionsNow();
  }, 120);
}

async function applyOptionsNow() {
  if (!S.options || !S.check || !S.check.ok) return true;
  if (S.optionsTimer) {
    clearTimeout(S.optionsTimer);
    S.optionsTimer = null;
  }
  const seq = ++S.optionsSeq;
  const revision = S.optionsRevision;
  const payload = JSON.parse(JSON.stringify(S.options));
  try {
    const state = await rpc('applyOptions', {
      options: payload,
      knownStructureRevision: S.structureRevision,
    });
    if (seq < S.optionsApplied) return true;
    if (revision !== S.optionsRevision) {
      try {
        post({ type: 'log', message: `applyOptions ignored stale rev ${revision}/${S.optionsRevision}` });
      } catch {
        // ignore
      }
      return true;
    }
    S.optionsApplied = seq;
    if (state && state.options) {
      try {
        post({
          type: 'log',
          message: `applyOptions applied rev ${revision}: system=${state.options.system} spin=${state.options.spin} u=${state.options.u}`,
        });
      } catch {
        // ignore
      }
      applyState(state);
    }
    return true;
  } catch (error) {
    reportWebviewError('applyOptions', error);
    showToast(error.message, true);
    return false;
  }
}

async function flushOptions() {
  if (S.optionsTimer) {
    clearTimeout(S.optionsTimer);
    S.optionsTimer = null;
  }
  return applyOptionsNow();
}

/* ------------------------------------------------------------------ 设置渲染 */
function updatePanelVisibility() {
  const o = S.options;
  if (!o) return;
  $('spinPanel').classList.toggle('hidden', o.spin !== 'custom');
  $('llmPanel').classList.toggle('hidden', o.spin !== 'llm');
  $('uPanel').classList.toggle('hidden', o.u !== 'custom');
  $('mixPanel').classList.toggle('hidden', !o.mix.on);
  $('ngxfPanel').classList.toggle('hidden', !o.ngxf.on);
  $('efieldPanel').classList.toggle('hidden', !o.efield.on);
  $('cpPanel').classList.toggle('hidden', !o.cp.on);
  $('parallelPanel').classList.toggle('hidden', !o.parallel.on);
  $('lobsterPanel').classList.toggle('hidden', !o.lobster.on);
  $('optcellPanel').classList.toggle('hidden', !o.optcell.on);

  const calcs = S.defaults ? S.defaults.calcs : [];
  const calc = calcs[o.calc] || {};
  const band = calc.tpl === 'band-band';
  $('klineRow').classList.toggle('hidden', !band);
  $('kmeshRow').classList.toggle('hidden', band);

  const system = (S.summary && S.summary.system) || (o.system === 'auto' ? 'bulk' : o.system);
  const tplKey = calc.tpl || 'relax';
  const optAllowed = tplKey === 'relax' && system === 'bulk';
  $('optcellOn').disabled = !optAllowed;
  $('solvation').disabled = Boolean(o.cp.on);
  $('clean').disabled = Boolean(o.lobster.on);
  $('llmBtn').disabled = !(S.summary && S.summary.loaded) && !(S.llm && S.llm.running);
}

function renderSettings() {
  const o = S.options;
  const d = S.defaults;
  if (!o || !d) return;
  buildSegmented($('sysSeg'), d.systems, o.system, (value) => {
    o.system = value;
    renderSettings();
    scheduleOptions();
  });
  buildSegmented($('spinSeg'), d.spinModes, o.spin, (value) => {
    o.spin = value;
    renderSettings();
    scheduleOptions();
  });
  buildSegmented($('uSeg'), d.uModes, o.u, (value) => {
    o.u = value;
    renderSettings();
    scheduleOptions();
  });

  setCheckbox($('mixOn'), o.mix.on);
  setCheckbox($('ngxfOn'), o.ngxf.on);
  setCheckbox($('solvation'), o.solvation);
  setCheckbox($('efieldOn'), o.efield.on);
  setCheckbox($('cpOn'), o.cp.on);
  setCheckbox($('parallelOn'), o.parallel.on);
  setCheckbox($('lobsterOn'), o.lobster.on);
  setCheckbox($('optcellOn'), o.optcell.on);
  setCheckbox($('clean'), o.clean);

  setInputValue($('kdens'), o.kdens);
  setInputValue($('perSeg'), o.perSeg);
  setInputValue($('efieldValue'), o.efield.value);
  if ($('efieldDir').value !== String(o.efield.idipol)) {
    $('efieldDir').value = String(o.efield.idipol);
  }
  setInputValue($('cpU'), o.cp.u);
  setInputValue($('cpMu'), o.cp.mu);
  setInputValue($('kpar'), o.parallel.kpar);
  setInputValue($('npar'), o.parallel.npar);
  setInputValue($('potcarDir'), o.potcarDir);
  if (document.activeElement !== $('lobsterPairs')) {
    $('lobsterPairs').value = o.lobster.pairs || '';
  }

  renderSpinPanel();
  renderUPanel();
  renderPotcarGrid();
  renderValuePanel('mixPanel', 'mix', d.mixValues, 'mixKey');
  renderValuePanel('ngxfPanel', 'ngxf', d.ngxfValues, 'ngxfKey');
  renderOptcellGrid();
  updatePanelVisibility();
}

function renderSpinPanel() {
  const box = $('spinPanel');
  const elements = (S.defaults && S.defaults.elements) || [];
  const key = JSON.stringify([elements, S.defaults && S.defaults.spinDefaults]);
  if (key !== S.spinKey) {
    box.replaceChildren();
    if (!elements.length) {
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.textContent = '加载结构文件后可按元素设置';
      box.append(hint);
    } else {
      const grid = document.createElement('div');
      grid.className = 'mini-grid';
      for (const element of elements) {
        const field = document.createElement('label');
        field.className = 'mini-field';
        const label = document.createElement('span');
        label.textContent = element;
        const input = document.createElement('input');
        input.type = 'number';
        input.min = '-10';
        input.max = '10';
        input.step = '0.5';
        input.dataset.element = element;
        input.addEventListener('input', () => {
          const value = Number(input.value);
          if (Number.isFinite(value)) {
            S.options.spinCustom[element] = value;
            scheduleOptions();
          }
        });
        field.append(label, input);
        grid.append(field);
      }
      box.append(grid);
    }
    S.spinKey = key;
  }
  box.querySelectorAll('input[data-element]').forEach((input) => {
    const element = input.dataset.element;
    setInputValue(input, S.options.spinCustom[element] ?? 0);
  });
}

function renderUPanel() {
  const box = $('uPanel');
  const elements = (S.defaults && S.defaults.elements) || [];
  const key = JSON.stringify([elements, S.defaults && S.defaults.uDefaults]);
  if (key !== S.uKey) {
    box.replaceChildren();
    if (!elements.length) {
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.textContent = '加载结构文件后可按元素设置';
      box.append(hint);
    } else {
      const grid = document.createElement('div');
      grid.className = 'u-grid';
      ['元素', '轨道 LDAUL', 'U (eV)', 'J (eV)'].forEach((text) => {
        const head = document.createElement('div');
        head.className = 'u-head';
        head.textContent = text;
        grid.append(head);
      });
      for (const element of elements) {
        const label = document.createElement('div');
        label.className = 'u-element';
        label.textContent = element;
        const select = document.createElement('select');
        select.dataset.element = element;
        [
          ['不加 U', '-1'],
          ['p  (1)', '1'],
          ['d  (2)', '2'],
          ['f  (3)', '3'],
        ].forEach(([text, value]) => {
          const option = document.createElement('option');
          option.value = value;
          option.textContent = text;
          select.append(option);
        });
        const u = document.createElement('input');
        u.type = 'number';
        u.min = '-10';
        u.max = '20';
        u.step = '0.1';
        u.dataset.element = element;
        u.dataset.field = 'u';
        const j = document.createElement('input');
        j.type = 'number';
        j.min = '0';
        j.max = '10';
        j.step = '0.1';
        j.dataset.element = element;
        j.dataset.field = 'j';
        select.addEventListener('change', () => {
          const record = S.options.uCustom[element] || {};
          record.orb = Number(select.value);
          S.options.uCustom[element] = record;
          scheduleOptions();
        });
        for (const input of [u, j]) {
          input.addEventListener('input', () => {
            const value = Number(input.value);
            if (!Number.isFinite(value)) return;
            const record = S.options.uCustom[element] || {};
            record[input.dataset.field] = value;
            S.options.uCustom[element] = record;
            scheduleOptions();
          });
        }
        grid.append(label, select, u, j);
      }
      box.append(grid);
    }
    S.uKey = key;
  }
  box.querySelectorAll('select[data-element]').forEach((select) => {
    const element = select.dataset.element;
    const record = S.options.uCustom[element] || { orb: -1 };
    if (document.activeElement !== select && select.value !== String(record.orb)) {
      select.value = String(record.orb);
    }
  });
  box.querySelectorAll('input[data-field]').forEach((input) => {
    const element = input.dataset.element;
    const record = S.options.uCustom[element] || {};
    setInputValue(input, record[input.dataset.field] ?? 0);
  });
}

function renderPotcarGrid() {
  const box = $('potcarGrid');
  const elements = (S.defaults && S.defaults.elements) || [];
  const variants = (S.potcar && S.potcar.variants) || {};
  const key = JSON.stringify([elements, variants]);
  if (key !== S.potKey) {
    box.replaceChildren();
    if (!elements.length) {
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.textContent = '加载结构文件后可按元素设置';
      box.append(hint);
    } else {
      const grid = document.createElement('div');
      grid.className = 'pot-grid';
      for (const element of elements) {
        const field = document.createElement('label');
        field.className = 'mini-field';
        const label = document.createElement('span');
        label.textContent = element;
        const select = document.createElement('select');
        select.dataset.element = element;
        const options = variants[element] && variants[element].length
          ? variants[element]
          : [element];
        for (const value of options) {
          const option = document.createElement('option');
          option.value = value;
          option.textContent = value;
          select.append(option);
        }
        select.addEventListener('change', () => {
          void changePotcarLabel(element, select.value);
        });
        field.append(label, select);
        grid.append(field);
      }
      box.append(grid);
    }
    S.potKey = key;
  }
  box.querySelectorAll('select[data-element]').forEach((select) => {
    const element = select.dataset.element;
    const current = (S.potcar && S.potcar.labels && S.potcar.labels[element])
      || (S.options.potcarLabels && S.options.potcarLabels[element])
      || element;
    if (document.activeElement !== select && select.value !== current) {
      if (![...select.options].some((option) => option.value === current)) {
        const option = document.createElement('option');
        option.value = current;
        option.textContent = current;
        select.append(option);
      }
      select.value = current;
    }
  });
}

async function changePotcarLabel(element, label) {
  try {
    const state = await rpc('setPotcarLabel', withKnown({ element, label }));
    if (state && state.options) applyState(state);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderValuePanel(boxId, optionKey, defaults, keyName) {
  const box = $(boxId);
  const key = JSON.stringify(defaults || {});
  if (key !== S[keyName]) {
    box.replaceChildren();
    if (!defaults || !Object.keys(defaults).length) {
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.textContent = '模板中没有可编辑的参数';
      box.append(hint);
    } else {
      const grid = document.createElement('div');
      grid.className = 'mini-grid';
      for (const tag of Object.keys(defaults)) {
        const field = document.createElement('label');
        field.className = 'mini-field';
        const label = document.createElement('span');
        label.textContent = tag;
        const input = document.createElement('input');
        input.type = 'text';
        input.dataset.tag = tag;
        input.addEventListener('input', () => {
          S.options[optionKey].values[tag] = input.value;
          scheduleOptions();
        });
        field.append(label, input);
        grid.append(field);
      }
      box.append(grid);
    }
    S[keyName] = key;
  }
  box.querySelectorAll('input[data-tag]').forEach((input) => {
    const tag = input.dataset.tag;
    setInputValue(input, S.options[optionKey].values[tag] ?? '');
  });
}

function renderOptcellGrid() {
  const box = $('optcellGrid');
  const defaults = (S.defaults && S.defaults.optcellDefault) || [[1, 0, 0], [1, 1, 0], [0, 0, 0]];
  const key = JSON.stringify(defaults);
  if (key !== S.optKey) {
    box.replaceChildren();
    const head = document.createElement('div');
    head.className = 'optcell-row optcell-head';
    head.append(document.createElement('span'));
    for (const axis of ['x', 'y', 'z']) {
      const span = document.createElement('span');
      span.textContent = axis;
      head.append(span);
    }
    box.append(head);
    ['a', 'b', 'c'].forEach((axis, row) => {
      const line = document.createElement('div');
      line.className = 'optcell-row';
      const label = document.createElement('span');
      label.className = 'row-key';
      label.textContent = `${axis} 矢量`;
      line.append(label);
      for (let column = 0; column < 3; column++) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'opt-cell';
        button.dataset.row = String(row);
        button.dataset.column = String(column);
        button.addEventListener('click', () => {
          const grid = S.options.optcell.grid;
          grid[row][column] = grid[row][column] ? 0 : 1;
          renderOptcellGrid();
          scheduleOptions();
        });
        line.append(button);
      }
      box.append(line);
    });
    S.optKey = key;
  }
  box.querySelectorAll('.opt-cell').forEach((button) => {
    const row = Number(button.dataset.row);
    const column = Number(button.dataset.column);
    const value = S.options.optcell.grid[row][column] ? 1 : 0;
    button.textContent = String(value);
    button.classList.toggle('on', Boolean(value));
  });
}

/* ------------------------------------------------------------------ 视图 / 固定原子 */
function renderViewerOptions() {
  const d = S.defaults;
  if (!d) return;
  const modes = [
    { key: 'rotate', label: '旋转' },
    { key: 'point', label: '点选' },
    { key: 'box', label: '框选' },
  ];
  buildSegmented($('modeSeg'), modes, S.mode, setViewerMode);
  buildSegmented($('bondSeg'), d.bondModes, S.options.bonds, (value) => {
    S.options.bonds = value;
    renderViewerOptions();
    persistSetting('bonds', value);
    scheduleOptions();
  });
  setCheckbox($('boundary'), S.options.boundary);
  const loaded = Boolean(S.summary && S.summary.loaded);
  for (const id of ['viewFront', 'viewRight', 'viewTop', 'fixAll', 'releaseAll', 'fixLayers',
    'indexFix', 'indexRelease']) {
    $(id).disabled = !loaded;
  }
  $('indexInput').disabled = !loaded;
}

function setViewerMode(mode) {
  S.mode = mode;
  buildSegmented($('modeSeg'), [
    { key: 'rotate', label: '旋转' },
    { key: 'point', label: '点选' },
    { key: 'box', label: '框选' },
  ], mode, setViewerMode);
  sendViewer({ type: 'mode', payload: mode });
}

function renderFix() {
  const fix = S.fix || { loaded: false, text: '未加载结构', layerText: '', canUndo: false, canRedo: false };
  $('fixStats').textContent = fix.text || '未加载结构';
  $('layerInfo').textContent = fix.layerText || '';
  $('undoBtn').disabled = !fix.loaded || !S.canUndo;
  $('redoBtn').disabled = !fix.loaded || !S.canRedo;
  $('fixAll').disabled = !fix.loaded;
  $('releaseAll').disabled = !fix.loaded;
  $('fixLayers').disabled = !fix.loaded;
  $('indexFix').disabled = !fix.loaded;
  $('indexRelease').disabled = !fix.loaded;
  $('indexInput').disabled = !fix.loaded;
}

function onViewerReady() {
  S.viewerReady = true;
  if (S.viewerQueue) {
    sendViewer({ type: 'state', payload: S.viewerQueue });
    S.viewerQueue = null;
  } else if (S.structurePayload) {
    sendViewer({
      type: 'state',
      payload: {
        clear: false,
        structure: S.structurePayload,
        masks: S.masks,
        mode: S.mode,
        generation: S.generation,
        keepView: true,
      },
    });
  } else {
    sendViewer({ type: 'state', payload: { clear: true } });
  }
}

function handleViewerMessage(message) {
  if (!message || !message.type) return;
  switch (message.type) {
    case 'ready':
      onViewerReady();
      break;
    case 'selectAtoms': {
      let data;
      try {
        data = JSON.parse(message.payload || '{}');
      } catch {
        return;
      }
      if (data.generation !== S.generation) return;
      if (!data.indices || !data.indices.length) return;
      void rpc('editMask', withKnown({ indices: data.indices, action: data.action || 'toggle' }))
        .then((state) => { if (state && state.options) applyState(state); })
        .catch((error) => showToast(error.message, true));
      break;
    }
    case 'requestMode':
      if (['rotate', 'point', 'box'].includes(message.payload)) setViewerMode(message.payload);
      break;
    case 'reportError':
      showToast(`3D 视图错误：${message.payload || ''}`, true);
      break;
    default:
      break;
  }
}

async function setStandardView(name) {
  if (!S.summary || !S.summary.loaded) return;
  try {
    const result = await rpc('setView', { name });
    if (result && result.view) sendViewer({ type: 'view', payload: result.view });
  } catch (error) {
    showToast(error.message, true);
  }
}

/* ------------------------------------------------------------------ 标签页 / 页面 */
function renderTabs() {
  const tabs = [{ key: 'structure', label: '结构' }]
    .concat(S.activeFiles.map((file) => ({ key: file, label: file })));
  if (!tabs.some((tab) => tab.key === S.activeTab)) S.activeTab = 'structure';
  const key = JSON.stringify(tabs.map((tab) => tab.key));
  const box = $('tabs');
  if (key !== S.tabsKey) {
    box.replaceChildren();
    for (const tab of tabs) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'tab';
      button.dataset.tab = tab.key;
      button.textContent = tab.label;
      button.addEventListener('click', () => setActiveTab(tab.key));
      box.append(button);
    }
    S.tabsKey = key;
  }
  box.querySelectorAll('.tab').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === S.activeTab);
  });
}

function setActiveTab(tab) {
  if (tab === S.activeTab) return;
  const previous = S.activeTab;
  if (previous !== 'structure' && S.dirtyFile === previous) {
    void flushEditor();
  }
  S.activeTab = tab;
  renderTabs();
  renderPage(true);
  if (tab !== 'structure') {
    setTimeout(() => editor.focus(), 0);
  }
}

function renderPage(force) {
  const fileTab = S.activeTab !== 'structure';
  $('pageStructure').classList.toggle('hidden', fileTab);
  $('pageFile').classList.toggle('hidden', !fileTab);
  if (fileTab) renderEditor(force);
}

/* ------------------------------------------------------------------ 文件编辑器 */
function currentFile() {
  return S.activeTab !== 'structure' ? S.files[S.activeTab] : null;
}

function renderEditor(force) {
  const file = currentFile();
  if (!file) return;
  const name = S.activeTab;
  $('fileState').className = 'state-chip' + (file.manual ? ' manual' : '') + (file.error ? ' error' : '');
  $('fileState').textContent = file.stateChip || (file.manual ? '手动编辑 · 未保存' : '自动生成 · 未保存');
  if (S.dirtyFile === name) {
    $('fileState').className = 'state-chip manual';
    $('fileState').textContent = '手动编辑 · 未保存';
  }
  $('fileInfo').className = 'hint file-info' + (file.error ? ' error' : '');
  $('fileInfo').textContent = file.info || '';
  $('fileInfo').classList.toggle('hidden', !file.info);
  $('revertBtn').classList.toggle('hidden', !file.revert);
  const server = S.serverText[name] ?? file.text ?? '';
  const shouldSync = force || (S.dirtyFile !== name && document.activeElement !== editor);
  if (shouldSync) {
    setEditorValue(server, { reset: Boolean(force) });
  }
  updateHighlight();
  if (S.find.open) runFind(false);
}

function setEditorValue(text, { reset = false } = {}) {
  const value = String(text ?? '');
  if (editor.value === value) {
    updateHighlight();
    return;
  }
  const start = reset ? 0 : editor.selectionStart;
  const end = reset ? 0 : editor.selectionEnd;
  const top = reset ? 0 : editor.scrollTop;
  const left = reset ? 0 : editor.scrollLeft;
  editor.value = value;
  editor.setSelectionRange(Math.min(start, value.length), Math.min(end, value.length));
  editor.scrollTop = top;
  editor.scrollLeft = left;
  updateHighlight();
  syncScroll();
}

async function flushEditor() {
  if (S.activeTab === 'structure') return true;
  const name = S.activeTab;
  if (S.editTimer) {
    clearTimeout(S.editTimer);
    S.editTimer = null;
  }
  const server = S.serverText[name] ?? '';
  if (editor.value === server) {
    S.dirtyFile = null;
    return true;
  }
  try {
    const state = await rpc('editFile', withKnown({ file: name, text: editor.value }));
    S.dirtyFile = null;
    if (state && state.options) applyState(state);
    return true;
  } catch (error) {
    showToast(error.message, true);
    return false;
  }
}

function scheduleEditor() {
  if (S.activeTab === 'structure') return;
  S.dirtyFile = S.activeTab;
  $('fileState').className = 'state-chip manual';
  $('fileState').textContent = '手动编辑 · 未保存';
  if (S.editTimer) clearTimeout(S.editTimer);
  S.editTimer = setTimeout(() => {
    S.editTimer = null;
    void flushEditor();
  }, 350);
}

function syncScroll() {
  if (!highlight) return;
  highlight.style.transform = `translate(${-editor.scrollLeft}px, ${-editor.scrollTop}px)`;
}

function scheduleHighlight() {
  if (S.highlightPending) return;
  S.highlightPending = true;
  requestAnimationFrame(() => {
    S.highlightPending = false;
    updateHighlight();
  });
}

function commentIndex(line) {
  const hash = line.indexOf('#');
  const bang = line.indexOf('!');
  if (hash < 0) return bang;
  if (bang < 0) return hash;
  return Math.min(hash, bang);
}

function syntaxSpans(line, file, lineIndex) {
  const spans = [];
  const add = (start, end, cls) => {
    if (end > start && start >= 0) spans.push({ start, end, cls });
  };
  const trimmed = line.trim();
  if (!trimmed) return spans;
  const first = line.length - line.trimStart().length;
  if (file === 'INCAR') {
    if (trimmed[0] === '#' || trimmed[0] === '!') {
      add(0, line.length, 'tok-comment');
      return spans;
    }
    const match = /^\s*([A-Za-z_]\w*)\s*=/.exec(line);
    if (match) add(match.index, match.index + match[1].length, 'tok-tag');
    else add(0, line.length, 'tok-header');
    const cut = commentIndex(line);
    if (cut >= 0) add(cut, line.length, 'tok-comment');
    return spans;
  }
  if (file === 'KPOINTS') {
    if (lineIndex === 0) {
      add(0, line.length, 'tok-comment');
      return spans;
    }
    if (/[A-Za-z]/.test(trimmed[0])) add(0, line.length, 'tok-header');
    const cut = line.indexOf('!');
    if (cut >= 0) add(cut, line.length, 'tok-label');
    return spans;
  }
  if (file === 'POSCAR') {
    if (lineIndex === 0) {
      add(0, line.length, 'tok-comment');
      return spans;
    }
    if (lineIndex === 5 && /[A-Za-z]/.test(trimmed[0])) {
      add(0, line.length, 'tok-tag');
      return spans;
    }
    if (/[A-Za-z]/.test(trimmed[0])) {
      add(0, line.length, 'tok-header');
      return spans;
    }
    if (lineIndex > 6) {
      const flags = /(?<=\s)([TF])(?=\s|$)/g;
      let match;
      while ((match = flags.exec(line))) {
        add(match.index, match.index + 1, match[1] === 'T' ? 'tok-true' : 'tok-false');
      }
      const cut = commentIndex(line);
      if (cut >= 0) add(cut, line.length, 'tok-comment');
    }
    return spans;
  }
  if (file === 'POTCAR') {
    if (trimmed[0] === '#') {
      add(0, line.length, 'tok-comment');
      return spans;
    }
    const word = trimmed.split(/\s+/)[0];
    add(first, first + word.length, 'tok-tag');
    const cut = line.indexOf('#');
    if (cut >= 0) add(cut, line.length, 'tok-comment');
    return spans;
  }
  if (file === 'lobsterin') {
    if (trimmed[0] === '#' || trimmed[0] === '!') {
      add(0, line.length, 'tok-comment');
      return spans;
    }
    const word = trimmed.split(/\s+/)[0];
    add(first, first + word.length, 'tok-tag');
    for (const keyword of ['atom', 'and', 'orbitalwise']) {
      const regex = new RegExp(`\\b${keyword}\\b`, 'g');
      let match;
      while ((match = regex.exec(line))) add(match.index, match.index + keyword.length, 'tok-label');
    }
    return spans;
  }
  if (file === 'OPTCELL') {
    for (let i = 0; i < line.length; i++) {
      if (line[i] === '0') add(i, i + 1, 'tok-false');
      if (line[i] === '1') add(i, i + 1, 'tok-true');
    }
  }
  return spans;
}

function computeMatches(text, query, caseSensitive) {
  if (!query) return [];
  const matches = [];
  const source = caseSensitive ? text : text.toLowerCase();
  const needle = caseSensitive ? query : query.toLowerCase();
  let from = 0;
  while (matches.length < 3000) {
    const index = source.indexOf(needle, from);
    if (index < 0) break;
    matches.push({ start: index, end: index + needle.length });
    from = index + Math.max(1, needle.length);
  }
  return matches;
}

function renderLine(line, spans, matches, lineIndex, currentLine) {
  const cuts = new Set([0, line.length]);
  for (const span of spans) {
    cuts.add(Math.max(0, Math.min(line.length, span.start)));
    cuts.add(Math.max(0, Math.min(line.length, span.end)));
  }
  for (const match of matches) {
    cuts.add(Math.max(0, Math.min(line.length, match.start)));
    cuts.add(Math.max(0, Math.min(line.length, match.end)));
  }
  const points = [...cuts].sort((a, b) => a - b);
  let html = '';
  for (let i = 0; i < points.length - 1; i++) {
    const start = points[i];
    const end = points[i + 1];
    if (end <= start) continue;
    let cls = '';
    for (const span of spans) {
      if (span.start <= start && span.end >= end) cls = span.cls;
    }
    const match = matches.find((item) => item.start <= start && item.end >= end);
    let chunk = escapeHtml(line.slice(start, end));
    if (match) {
      const current = match.global === S.find.current;
      const inner = cls ? `<span class="${cls}">${chunk}</span>` : chunk;
      chunk = `<mark class="match${current ? ' current' : ''}">${inner}</mark>`;
    } else if (cls) {
      chunk = `<span class="${cls}">${chunk}</span>`;
    }
    html += chunk;
  }
  return `<div class="line${lineIndex === currentLine ? ' curline' : ''}">${html || ' '}</div>`;
}

function updateHighlight() {
  const file = S.activeTab;
  if (file === 'structure' || !S.files[file]) return;
  const text = editor.value;
  const lines = text.split('\n');
  const offsets = [];
  let offset = 0;
  for (const line of lines) {
    offsets.push(offset);
    offset += line.length + 1;
  }
  const query = S.find.open ? S.find.query : '';
  const allMatches = computeMatches(text, query, S.find.caseSensitive);
  S.find.matches = allMatches;
  if (S.find.current >= allMatches.length) S.find.current = allMatches.length ? allMatches.length - 1 : -1;
  if (S.find.open && query && S.find.current < 0 && allMatches.length) S.find.current = 0;
  const selection = editor.selectionStart || 0;
  const currentLine = text.slice(0, selection).split('\n').length - 1;
  const html = lines.map((line, index) => {
    const lineStart = offsets[index];
    const lineEnd = lineStart + line.length;
    const matches = [];
    allMatches.forEach((match, globalIndex) => {
      if (match.start < lineEnd && match.end > lineStart) {
        matches.push({
          start: Math.max(match.start, lineStart) - lineStart,
          end: Math.min(match.end, lineEnd) - lineStart,
          global: globalIndex,
        });
      }
    });
    return renderLine(line, syntaxSpans(line, file, index), matches, index, currentLine);
  }).join('');
  highlight.innerHTML = html;
  updateFindCount();
  syncScroll();
}

/* ------------------------------------------------------------------ 查找 */
function updateFindCount() {
  if (!S.find.open) return;
  const count = $('findCount');
  const total = S.find.matches.length;
  if (!S.find.query) {
    count.textContent = '';
    count.style.color = '';
    return;
  }
  if (!total) {
    count.textContent = '无匹配';
    count.style.color = '#FF3B30';
    return;
  }
  const current = S.find.current >= 0 ? S.find.current + 1 : 0;
  count.textContent = `${current}/${total}${total >= 3000 ? '+' : ''}`;
  count.style.color = '';
}

function openFind() {
  if (S.activeTab === 'structure') return;
  S.find.open = true;
  $('findBar').classList.remove('hidden');
  const selected = editor.value.slice(editor.selectionStart, editor.selectionEnd);
  if (selected && !selected.includes('\n') && selected.length < 200) {
    $('findQuery').value = selected;
  }
  S.find.query = $('findQuery').value;
  $('findQuery').focus();
  $('findQuery').select();
  runFind(true);
}

function closeFind() {
  S.find.open = false;
  S.find.current = -1;
  S.find.matches = [];
  $('findBar').classList.add('hidden');
  updateHighlight();
  editor.focus();
}

function runFind(selectFirst) {
  S.find.query = $('findQuery').value;
  updateHighlight();
  if (!S.find.query) {
    S.find.current = -1;
    updateFindCount();
    return;
  }
  if (S.find.matches.length) {
    if (selectFirst || S.find.current < 0 || S.find.current >= S.find.matches.length) {
      goToMatch(0, false);
    } else {
      goToMatch(S.find.current, false);
    }
  } else {
    S.find.current = -1;
    updateFindCount();
  }
}

function scrollToOffset(offset) {
  const before = editor.value.slice(0, offset).split('\n').length - 1;
  const lineHeight = 20;
  const target = before * lineHeight - editor.clientHeight / 2 + 10;
  editor.scrollTop = Math.max(0, target);
  syncScroll();
}

function goToMatch(index, focus = true) {
  const total = S.find.matches.length;
  if (!total) return;
  if (index < 0) index = total - 1;
  if (index >= total) index = 0;
  S.find.current = index;
  const match = S.find.matches[index];
  if (focus) editor.focus();
  editor.setSelectionRange(match.start, match.end);
  scrollToOffset(match.start);
  updateHighlight();
  updateFindCount();
}

function findNext() {
  if (!S.find.open) {
    openFind();
    return;
  }
  if (!S.find.matches.length) return;
  goToMatch((S.find.current + 1) % S.find.matches.length);
}

function findPrevious() {
  if (!S.find.open) {
    openFind();
    return;
  }
  if (!S.find.matches.length) return;
  goToMatch((S.find.current - 1 + S.find.matches.length) % S.find.matches.length);
}

function scheduleFind() {
  if (S.findTimer) clearTimeout(S.findTimer);
  S.findTimer = setTimeout(() => {
    S.findTimer = null;
    if (S.find.open) runFind(false);
  }, 180);
}

/* ------------------------------------------------------------------ LLM */
function renderLLM() {
  const llm = S.llm || {};
  $('llmBtn').textContent = llm.buttonText || '开始分析';
  $('llmBtn').disabled = !(S.summary && S.summary.loaded) && !llm.running;
  if (document.activeElement !== $('llmHint')) $('llmHint').value = S.llmHint;
  const box = $('llmBox');
  box.replaceChildren();
  const title = document.createElement('div');
  title.className = 'result-title';
  title.textContent = llm.title || '尚未分析';
  if (llm.titleColor) title.style.color = llm.titleColor;
  box.append(title);
  if (llm.status === 'running') {
    if (!S.llmStartedAt) S.llmStartedAt = Date.now() - (llm.elapsed || 0) * 1000;
  } else {
    S.llmStartedAt = 0;
  }
  const body = document.createElement('div');
  body.className = 'result-body';
  if (llm.status === 'done') {
    if (llm.magmom) {
      const magmom = document.createElement('p');
      magmom.className = 'result-magmom';
      magmom.textContent = llm.magmom;
      body.append(magmom);
    }
    const rationale = document.createElement('p');
    const strong = document.createElement('b');
    strong.textContent = '依据';
    rationale.append(strong, document.createTextNode('　' + (llm.rationale || '未给出说明')));
    body.append(rationale);
    if (llm.warnings && llm.warnings.length) {
      for (const warning of llm.warnings) {
        const item = document.createElement('p');
        item.className = 'hint warn';
        item.textContent = '⚠ ' + warning;
        body.append(item);
      }
    }
  } else if (llm.body) {
    const paragraph = document.createElement('p');
    paragraph.textContent = llm.body;
    body.append(paragraph);
    if (llm.status === 'error') {
      const hint = document.createElement('p');
      hint.className = 'hint';
      hint.textContent = '可重试，或改用启发式 / 按元素指定';
      body.append(hint);
    }
  }
  box.append(body);

  if (S.llmTimer) {
    clearInterval(S.llmTimer);
    S.llmTimer = null;
  }
  if (llm.running) {
    const baseTitle = String(llm.title || '分析中').split(' · 已用')[0];
    S.llmTimer = setInterval(() => {
      if (!S.llm || !S.llm.running) return;
      const el = $('llmBox').querySelector('.result-title');
      if (!el) return;
      const seconds = Math.max(0, Math.round((Date.now() - S.llmStartedAt) / 1000));
      el.textContent = `${baseTitle} · 已用 ${seconds} s`;
    }, 1000);
  }
}

/* ------------------------------------------------------------------ 底部文件开关 */
function renderFileToggles() {
  const key = S.activeFiles.join('|');
  const box = $('fileToggles');
  if (key !== S.togglesKey) {
    box.replaceChildren();
    for (const file of S.activeFiles) {
      const label = document.createElement('label');
      label.className = 'toggle';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.dataset.file = file;
      const span = document.createElement('span');
      span.textContent = file;
      input.addEventListener('change', () => {
        if (!S.options) return;
        S.options.include[file] = input.checked;
        scheduleOptions();
      });
      label.append(input, span);
      box.append(label);
    }
    S.togglesKey = key;
  }
  box.querySelectorAll('input[data-file]').forEach((input) => {
    const included = !S.options || S.options.include[input.dataset.file] !== false;
    if (input.checked !== included) input.checked = included;
  });
}

/* ------------------------------------------------------------------ 操作 */
async function loadStructure(path) {
  if (!path) return;
  if (!S.check || !S.check.ok) {
    showToast('Python 环境未就绪，无法读取结构', true);
    return;
  }
  try {
    const result = await rpc('loadStructure', { path });
    if (result && result.state) {
      applyState(result.state);
      setActiveTab('structure');
    }
    if (result && result.message) showToast(result.message);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function pickStructure() {
  try {
    const path = await host('pickStructure');
    if (path) await loadStructure(path);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function clearStructure() {
  try {
    const result = await rpc('clearStructure', {});
    if (result && result.state) applyState(result.state);
    if (result && result.message) showToast(result.message);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editAll(action) {
  if (!S.summary || !S.summary.loaded) return;
  try {
    const state = await rpc('editAll', withKnown({ action }));
    if (state && state.options) applyState(state);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editMaskAction(indices, action) {
  try {
    const state = await rpc('editMask', withKnown({ indices, action }));
    if (state && state.options) applyState(state);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editTyped(action) {
  if (!S.summary || !S.summary.loaded) return;
  try {
    const result = await rpc('editTyped', withKnown({ text: $('indexInput').value, action }));
    if (result && result.state) applyState(result.state);
    if (result && result.message) showToast(result.message);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function fixBottomLayers() {
  if (!S.summary || !S.summary.loaded) return;
  try {
    const result = await rpc('fixBottomLayers', withKnown({ count: Number($('layerN').value) || 1 }));
    if (result && result.state) applyState(result.state);
    if (result && result.message) showToast(result.message);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function undo() {
  if (!S.canUndo) return;
  try {
    const state = await rpc('undo', withKnown());
    if (state && state.options) applyState(state);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function redo() {
  if (!S.canRedo) return;
  try {
    const state = await rpc('redo', withKnown());
    if (state && state.options) applyState(state);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function revertCurrent() {
  if (S.activeTab === 'structure') return;
  try {
    const result = await rpc('revertFile', withKnown({ file: S.activeTab }));
    if (result && result.state) applyState(result.state);
    if (result && result.message) showToast(result.message);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function copyCurrent() {
  if (S.activeTab === 'structure') return;
  try {
    await navigator.clipboard.writeText(editor.value);
  } catch {
    const area = document.createElement('textarea');
    area.value = editor.value;
    document.body.append(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  showToast(`已复制 ${S.activeTab}${S.activeTab === 'POTCAR' ? '（组成）' : ''}`);
}

async function confirmOverwrite(result) {
  const detail = result.sourceConflict
    ? `注意：${S.summary && S.summary.sourceName ? S.summary.sourceName : '当前输入结构文件'} 是当前输入的结构文件。`
    : undefined;
  try {
    return await host('confirm', {
      message: result.message || '目标文件已存在，是否覆盖？',
      detail,
      confirmLabel: '覆盖',
    });
  } catch (error) {
    showToast(error.message, true);
    return false;
  }
}

async function saveCurrent() {
  if (S.activeTab === 'structure') {
    await generate();
    return;
  }
  if (!(await flushOptions())) return;
  if (!(await flushEditor())) return;
  const file = S.activeTab;
  try {
    let result = await rpc('saveFile', withKnown({ file, force: false }));
    if (result && result.needConfirm) {
      if (!(await confirmOverwrite(result))) return;
      result = await rpc('saveFile', withKnown({ file, force: true }));
    }
    if (result && result.state) applyState(result.state);
    showToast(result && result.message ? result.message : `已保存 ${file}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function saveAsCurrent() {
  if (S.activeTab === 'structure') return;
  const file = S.activeTab;
  if (!(await flushEditor())) return;
  const dir = (S.status && S.status.outdir) || '';
  const defaultPath = dir ? `${dir}/${file}` : file;
  try {
    const path = await host('pickSaveAs', { defaultPath, fileName: file });
    if (!path) return;
    const result = await rpc('saveFileAs', withKnown({ file, path }));
    if (result && result.state) applyState(result.state);
    showToast(result && result.message ? result.message : `已保存 ${file}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function generate() {
  if (!(await flushOptions())) return;
  if (S.activeTab !== 'structure' && !(await flushEditor())) return;
  if (!S.activeFiles.length) {
    showToast('请至少选择一个要写入的文件', true);
    return;
  }
  const files = S.activeFiles.filter((file) => !S.options || S.options.include[file] !== false);
  if (!files.length) {
    showToast('请至少选择一个要写入的文件', true);
    return;
  }
  try {
    let result = await rpc('generate', withKnown({ files, force: false }), 180000);
    if (result && result.needConfirm) {
      if (!(await confirmOverwrite(result))) return;
      result = await rpc('generate', withKnown({ files, force: true }), 180000);
    }
    if (result && result.state) applyState(result.state);
    showToast(result && result.message ? result.message : '已生成作业文件');
  } catch (error) {
    showToast(error.message, true);
  }
}

async function startOrCancelLLM() {
  if (!S.llm) return;
  try {
    if (S.llm.running) {
      const state = await rpc('cancelLLM', withKnown({ note: '已取消' }));
      if (state && state.options) applyState(state);
      return;
    }
    const state = await rpc('startLLM', withKnown({ hint: $('llmHint').value || '' }));
    if (state && state.options) applyState(state);
    showToast('已开始 LLM 自旋分析');
  } catch (error) {
    showToast(error.message, true);
  }
}

/* ------------------------------------------------------------------ 致命错误 */
function showFatal(title, message) {
  $('fatalTitle').textContent = title || '无法启动生成器';
  $('fatalMessage').textContent = message || '';
  $('fatal').classList.remove('hidden');
  $('fatalRetry').disabled = false;
}

function hideFatal() {
  $('fatal').classList.add('hidden');
}

async function retryBackend() {
  $('fatalRetry').disabled = true;
  $('fatalMessage').textContent = '正在重启 Python 服务…';
  try {
    const check = await host('restartBackend');
    if (check && check.ok) {
      hideFatal();
    } else if (check) {
      showFatal('Python 环境未就绪', check.error || '请检查 Python 与依赖');
    }
    $('fatalRetry').disabled = false;
  } catch (error) {
    showFatal('重启失败', error.message);
    $('fatalRetry').disabled = false;
  }
}

/* ------------------------------------------------------------------ 事件绑定 */
function bindEvents() {
  $('openSettings').addEventListener('click', () => {
    void host('openSettings').catch((error) => showToast(error.message, true));
  });
  $('pickFile')?.addEventListener('click', () => { void pickStructure(); });
  $('refreshTabs')?.addEventListener('click', () => { void refreshOpenStructures(); });
  $('toggleTree')?.addEventListener('click', toggleTreeVisible);
  const openSelect = $('openStructureSelect');
  openSelect?.addEventListener('change', () => {
    if (openSelect.value) void loadStructure(openSelect.value);
  });
  $('changeFile').addEventListener('click', () => { void pickStructure(); });
  $('removeFile').addEventListener('click', () => { void clearStructure(); });
  $('browsePotcar').addEventListener('click', async () => {
    try {
      const path = await host('pickPotcarDir');
      if (!path || !S.options) return;
      S.options.potcarDir = path;
      $('potcarDir').value = path;
      persistSetting('potcarDir', path);
      renderSettings();
      scheduleOptions();
    } catch (error) {
      showToast(error.message, true);
    }
  });
  $('potcarDir').addEventListener('change', () => {
    if (!S.options) return;
    const value = $('potcarDir').value.trim();
    S.options.potcarDir = value;
    persistSetting('potcarDir', value);
    scheduleOptions();
  });

  $('mixOn').addEventListener('change', () => {
    if (!S.options) return;
    S.options.mix.on = $('mixOn').checked;
    updatePanelVisibility();
    scheduleOptions();
  });
  $('ngxfOn').addEventListener('change', () => {
    if (!S.options) return;
    S.options.ngxf.on = $('ngxfOn').checked;
    updatePanelVisibility();
    scheduleOptions();
  });
  $('solvation').addEventListener('change', () => {
    if (!S.options) return;
    S.options.solvation = $('solvation').checked;
    scheduleOptions();
  });
  $('efieldOn').addEventListener('change', () => {
    if (!S.options) return;
    S.options.efield.on = $('efieldOn').checked;
    updatePanelVisibility();
    scheduleOptions();
  });
  $('cpOn').addEventListener('change', () => {
    if (!S.options) return;
    S.options.cp.on = $('cpOn').checked;
    updatePanelVisibility();
    scheduleOptions();
  });
  $('parallelOn').addEventListener('change', () => {
    if (!S.options) return;
    S.options.parallel.on = $('parallelOn').checked;
    updatePanelVisibility();
    scheduleOptions();
  });
  $('lobsterOn').addEventListener('change', () => {
    if (!S.options) return;
    S.options.lobster.on = $('lobsterOn').checked;
    updatePanelVisibility();
    scheduleOptions();
  });
  $('optcellOn').addEventListener('change', () => {
    if (!S.options) return;
    S.options.optcell.on = $('optcellOn').checked;
    updatePanelVisibility();
    scheduleOptions();
  });
  $('clean').addEventListener('change', () => {
    if (!S.options) return;
    S.options.clean = $('clean').checked;
    scheduleOptions();
  });

  $('kdens').addEventListener('input', () => {
    if (!S.options) return;
    const value = Number($('kdens').value);
    if (Number.isFinite(value)) {
      S.options.kdens = value;
      scheduleOptions();
    }
  });
  $('perSeg').addEventListener('input', () => {
    if (!S.options) return;
    const value = Number($('perSeg').value);
    if (Number.isFinite(value)) {
      S.options.perSeg = value;
      scheduleOptions();
    }
  });
  $('efieldValue').addEventListener('input', () => {
    if (!S.options) return;
    const value = Number($('efieldValue').value);
    if (Number.isFinite(value)) {
      S.options.efield.value = value;
      scheduleOptions();
    }
  });
  $('efieldDir').addEventListener('change', () => {
    if (!S.options) return;
    S.options.efield.idipol = Number($('efieldDir').value);
    scheduleOptions();
  });
  $('cpU').addEventListener('input', () => {
    if (!S.options) return;
    const value = Number($('cpU').value);
    if (!Number.isFinite(value)) return;
    S.options.cp.u = value;
    S.options.cp.mu = Math.round((S.defaults.cpRef - value) * 1000) / 1000;
    setInputValue($('cpMu'), S.options.cp.mu);
    scheduleOptions();
  });
  $('cpMu').addEventListener('input', () => {
    if (!S.options) return;
    const value = Number($('cpMu').value);
    if (!Number.isFinite(value)) return;
    S.options.cp.mu = value;
    S.options.cp.u = Math.round((S.defaults.cpRef - value) * 1000) / 1000;
    setInputValue($('cpU'), S.options.cp.u);
    scheduleOptions();
  });
  $('kpar').addEventListener('input', () => {
    if (!S.options) return;
    const value = Number($('kpar').value);
    if (Number.isFinite(value)) {
      S.options.parallel.kpar = value;
      scheduleOptions();
    }
  });
  $('npar').addEventListener('input', () => {
    if (!S.options) return;
    const value = Number($('npar').value);
    if (Number.isFinite(value)) {
      S.options.parallel.npar = value;
      scheduleOptions();
    }
  });
  $('lobsterPairs').addEventListener('input', () => {
    if (!S.options) return;
    S.options.lobster.pairs = $('lobsterPairs').value;
    scheduleOptions();
  });

  $('boundary').addEventListener('change', () => {
    if (!S.options) return;
    S.options.boundary = $('boundary').checked;
    persistSetting('boundary', S.options.boundary);
    scheduleOptions();
  });
  $('llmBtn').addEventListener('click', () => { void startOrCancelLLM(); });
  $('llmHint').addEventListener('input', () => { S.llmHint = $('llmHint').value; });

  $('viewFront').addEventListener('click', () => { void setStandardView('front'); });
  $('viewRight').addEventListener('click', () => { void setStandardView('right'); });
  $('viewTop').addEventListener('click', () => { void setStandardView('top'); });
  $('fixAll').addEventListener('click', () => { void editAll('fix'); });
  $('releaseAll').addEventListener('click', () => { void editAll('release'); });
  $('undoBtn').addEventListener('click', () => { void undo(); });
  $('redoBtn').addEventListener('click', () => { void redo(); });
  $('fixLayers').addEventListener('click', () => { void fixBottomLayers(); });
  $('indexFix').addEventListener('click', () => { void editTyped('fix'); });
  $('indexRelease').addEventListener('click', () => { void editTyped('release'); });
  $('indexInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      void editTyped('fix');
    }
  });

  $('generateBtn').addEventListener('click', () => { void generate(); });
  $('openFolder').addEventListener('click', () => {
    const outdir = S.status && S.status.outdir;
    if (outdir) void host('openExternal', { path: outdir }).catch((error) => showToast(error.message, true));
  });
  $('saveBtn').addEventListener('click', () => { void saveCurrent(); });
  $('saveAsBtn').addEventListener('click', () => { void saveAsCurrent(); });
  $('copyBtn').addEventListener('click', () => { void copyCurrent(); });
  $('revertBtn').addEventListener('click', () => { void revertCurrent(); });
  $('findBtn').addEventListener('click', openFind);
  $('findClose').addEventListener('click', closeFind);
  $('findPrev').addEventListener('click', findPrevious);
  $('findNext').addEventListener('click', findNext);
  $('findQuery').addEventListener('input', scheduleFind);
  $('findQuery').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (event.shiftKey) findPrevious(); else findNext();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closeFind();
    }
  });
  $('findCase').addEventListener('click', () => {
    S.find.caseSensitive = !S.find.caseSensitive;
    $('findCase').classList.toggle('active', S.find.caseSensitive);
    runFind(true);
  });

  $('fatalInstall').addEventListener('click', () => {
    void host('installDeps').catch((error) => showToast(error.message, true));
  });
  $('fatalRetry').addEventListener('click', () => { void retryBackend(); });
  $('fatalSettings').addEventListener('click', () => {
    void host('openSettings').catch((error) => showToast(error.message, true));
  });

  editor.addEventListener('input', () => {
    if (S.activeTab === 'structure') return;
    S.dirtyFile = S.activeTab;
    $('fileState').className = 'state-chip manual';
    $('fileState').textContent = '手动编辑 · 未保存';
    scheduleHighlight();
    if (S.find.open) scheduleFind();
    if (S.editTimer) clearTimeout(S.editTimer);
    S.editTimer = setTimeout(() => {
      S.editTimer = null;
      void flushEditor();
    }, 350);
  });
  editor.addEventListener('scroll', syncScroll);
  editor.addEventListener('keyup', scheduleHighlight);
  editor.addEventListener('click', scheduleHighlight);
  editor.addEventListener('mouseup', scheduleHighlight);
  editor.addEventListener('keydown', (event) => {
    if (event.key === 'Tab') {
      event.preventDefault();
      document.execCommand('insertText', false, '    ');
      scheduleHighlight();
      scheduleEditor();
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
      event.preventDefault();
      event.stopPropagation();
      openFind();
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      event.stopPropagation();
      void saveCurrent();
    }
  });

  document.addEventListener('keydown', (event) => {
    const target = event.target;
    const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT');
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === 'o') {
      event.preventDefault();
      void pickStructure();
      return;
    }
    if (mod && event.key === 'Enter') {
      event.preventDefault();
      void generate();
      return;
    }
    if (mod && event.key.toLowerCase() === 's') {
      event.preventDefault();
      void saveCurrent();
      return;
    }
    if (mod && event.key.toLowerCase() === 'f' && S.activeTab !== 'structure') {
      event.preventDefault();
      openFind();
      return;
    }
    if (!typing && S.activeTab === 'structure') {
      if (event.key === '1') setViewerMode('rotate');
      else if (event.key === '2') setViewerMode('point');
      else if (event.key === '3') setViewerMode('box');
      else if (event.key === 'Escape') setViewerMode('rotate');
      else if (mod && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        void undo();
      } else if (mod && event.key.toLowerCase() === 'y') {
        event.preventDefault();
        void redo();
      }
    }
    if (event.key === 'F3') {
      event.preventDefault();
      if (event.shiftKey) findPrevious(); else findNext();
    }
  });

  ['dragenter', 'dragover'].forEach((name) => {
    document.body.addEventListener(name, (event) => {
      event.preventDefault();
      $('sourcePanel')?.classList.add('drag');
    });
  });
  ['dragleave', 'drop'].forEach((name) => {
    document.body.addEventListener(name, (event) => {
      event.preventDefault();
      $('sourcePanel')?.classList.remove('drag');
    });
  });
  document.body.addEventListener('drop', (event) => {
    const path = pathFromDrop(event);
    if (path) void loadStructure(path);
    else showToast('无法读取拖入的文件路径，请使用“选择文件…”', true);
  });
}

function pathFromDrop(event) {
  const data = event.dataTransfer;
  if (!data) return null;
  for (const file of Array.from(data.files || [])) {
    if (file.path) return file.path;
  }
  const uriList = (data.getData('text/uri-list') || data.getData('text/plain') || '').trim();
  if (!uriList) return null;
  const first = uriList.split(/\r?\n/).find((line) => line && !line.startsWith('#'));
  if (!first) return null;
  if (first.startsWith('file://')) {
    try {
      let path = decodeURIComponent(first.replace(/^file:\/\//, ''));
      if (/^\/[A-Za-z]:/.test(path)) return path.slice(1).replace(/\//g, '\\');
      return path;
    } catch {
      return null;
    }
  }
  if (/^[A-Za-z]:[\\/]/.test(first) || first.startsWith('/')) return first;
  return null;
}

/* ------------------------------------------------------------------ 启动 */
window.addEventListener('error', (event) => {
  const target = event.target;
  if (target && (target.src || target.href)) {
    reportWebviewError('资源加载失败', target.src || target.href);
  } else {
    reportWebviewError('webview error', event.error || event.message || 'unknown');
  }
});
window.addEventListener('unhandledrejection', (event) => {
  reportWebviewError('webview unhandledrejection', event.reason);
});

document.addEventListener('click', onSegmentedClick, true);
bindEvents();
post({ type: 'ready' });
