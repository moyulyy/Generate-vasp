/**
 * VS Code 扩展宿主。
 *
 * - 在活动栏注册 Generate-VASP 入口视图；
 * - 点击按钮后在编辑器区域打开 WebviewPanel；
 * - 管理常驻 Python 后端（stdio JSON-RPC），转发 Webview 的请求；
 * - 负责文件选择、覆盖确认、打开输出目录等宿主能力。
 */
import * as vscode from 'vscode';
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as readline from 'readline';

const VIEW_ID = 'generateVasp.launcher';
const PANEL_TYPE = 'generateVasp.panel';
const STATE_PREFIX = 'generateVasp.';

type CheckInfo = {
  ok: boolean;
  error?: string;
  install?: string;
  python?: string;
  pythonPath?: string;
  pythonExecutable?: string;
  numpy?: string;
  ase?: string;
  spglib?: string | null;
  seekpath?: string | null;
  root?: string;
  potcarDir?: string;
  potcarExists?: boolean;
  llmConfigured?: boolean;
};

type Pending = {
  resolve: (value: any) => void;
  reject: (reason: Error) => void;
  timer: NodeJS.Timeout;
};

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function quoteShell(value: string): string {
  return `"${value.replace(/"/g, '\\"')}"`;
}

async function pickStructureFile(): Promise<string | undefined> {
  const result = await vscode.window.showOpenDialog({
    canSelectMany: false,
    canSelectFiles: true,
    canSelectFolders: false,
    openLabel: '打开结构文件',
    filters: {
      '结构文件 (CIF / POSCAR / CONTCAR)': ['cif', 'vasp', 'poscar', 'CONTCAR', 'POSCAR'],
      '所有文件': ['*'],
    },
  });
  return result?.[0]?.fsPath;
}

type StructureTab = {
  path: string;
  name: string;
  relative: string;
  active: boolean;
};

const IGNORED_DIRS = new Set([
  '.git', 'node_modules', 'dist', 'build', 'out', '.venv', 'venv', '__pycache__',
  '.vscode', '.idea', '.reasonix', '.claude', 'POTCAR', 'site-packages', '.next', 'target',
]);

function workspaceRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function isStructureFile(file: string): boolean {
  const base = path.basename(file).toUpperCase();
  const ext = path.extname(file).toLowerCase();
  return ext === '.cif'
    || ext === '.vasp'
    || ext === '.poscar'
    || base === 'POSCAR'
    || base.startsWith('POSCAR.')
    || base.startsWith('POSCAR_')
    || base === 'CONTCAR'
    || base.startsWith('CONTCAR.')
    || base.startsWith('CONTCAR_');
}

function getOpenStructures(): StructureTab[] {
  const tabGroups = (vscode.window as any).tabGroups;
  if (!tabGroups || !Array.isArray(tabGroups.all)) return [];
  const root = workspaceRoot();
  const seen = new Set<string>();
  const result: StructureTab[] = [];
  for (const group of tabGroups.all) {
    for (const tab of group?.tabs ?? []) {
      const uri = tab?.input?.uri;
      if (!uri || uri.scheme !== 'file') continue;
      const file = uri.fsPath as string;
      if (!isStructureFile(file) || seen.has(file)) continue;
      seen.add(file);
      result.push({
        path: file,
        name: path.basename(file),
        relative: root ? path.relative(root, file).replace(/\\/g, '/') : path.basename(file),
        active: Boolean(tab.isActive),
      });
    }
  }
  result.sort((a, b) => Number(b.active) - Number(a.active) || a.relative.localeCompare(b.relative));
  return result;
}

function structureState() {
  return { open: getOpenStructures(), root: workspaceRoot() ?? '' };
}


class BackendService implements vscode.Disposable {
  readonly events = new vscode.EventEmitter<any>();
  readonly output: vscode.OutputChannel;
  checkInfo?: CheckInfo;
  pythonUsed?: string;

  private proc?: ChildProcessWithoutNullStreams;
  private rl?: readline.Interface;
  private seq = 0;
  private pending = new Map<number, Pending>();
  private starting?: Promise<CheckInfo>;
  private disposed = false;

  constructor(private readonly context: vscode.ExtensionContext) {
    this.output = vscode.window.createOutputChannel('Generate-VASP');
  }

  private setting<T>(key: string, fallback: T): T {
    const value = this.context.globalState.get<T>(STATE_PREFIX + key);
    return value === undefined ? fallback : value;
  }

  private config() {
    return vscode.workspace.getConfiguration('generateVasp');
  }

  effectiveSettings() {
    const cfg = this.config();
    const recent = this.context.globalState.get<string[]>(STATE_PREFIX + 'recentStructures', []);
    const last = this.setting<string>('lastStructure', '');
    return {
      potcarDir: this.setting<string>('potcarDir', cfg.get<string>('potcarPath', '')),
      boundary: this.setting<boolean>('boundary', cfg.get<boolean>('boundary', true)),
      bonds: this.setting<string>('bonds', cfg.get<string>('bonds', 'half')),
      recent: recent.filter((file) => {
        try {
          return fs.existsSync(file);
        } catch {
          return false;
        }
      }),
      lastStructure: last,
      autoLoadLastStructure: cfg.get<boolean>('autoLoadLastStructure', false),
      llmConfigPath: cfg.get<string>('llmConfigPath', ''),
      pythonPath: cfg.get<string>('pythonPath', ''),
    };
  }

  recentStructures(): string[] {
    return this.effectiveSettings().recent;
  }

  async rememberStructure(file: string) {
    if (!file) return;
    const recent = this.context.globalState.get<string[]>(STATE_PREFIX + 'recentStructures', []);
    const list = [file, ...recent.filter((item) => item !== file)].slice(0, 8);
    await this.context.globalState.update(STATE_PREFIX + 'recentStructures', list);
    await this.context.globalState.update(STATE_PREFIX + 'lastStructure', file);
    this.events.fire({ event: 'recentChanged' });
  }

  private llmConfigPath(): string | undefined {
    const configured = (this.config().get<string>('llmConfigPath', '') || '').trim();
    if (configured) return configured;
    const candidates: string[] = [];
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      candidates.push(path.join(folder.uri.fsPath, 'llm_config.json'));
    }
    candidates.push(path.join(this.context.extensionPath, 'llm_config.json'));
    candidates.push(path.join(this.context.extensionPath, '..', 'llm_config.json'));
    return candidates.find((candidate) => {
      try {
        return fs.existsSync(candidate);
      } catch {
        return false;
      }
    });
  }

  private workspaceCwd(): string {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? this.context.extensionPath;
  }

  private pythonCandidates(): string[] {
    const configured = (this.config().get<string>('pythonPath', '') || '').trim();
    if (configured) return [configured];
    const names = new Set<string>();
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
      const base = folder.uri.fsPath;
      for (const rel of [
        ['.venv', 'Scripts', 'python.exe'],
        ['.venv', 'bin', 'python'],
        ['venv', 'Scripts', 'python.exe'],
        ['venv', 'bin', 'python'],
      ]) {
        const candidate = path.join(base, ...rel);
        try {
          if (fs.existsSync(candidate)) names.add(candidate);
        } catch {
          // ignore
        }
      }
    }
    if (process.platform === 'win32') {
      names.add('python');
      names.add('python3');
      names.add('py');
    } else {
      names.add('python3');
      names.add('python');
    }
    return [...names];
  }

  private buildArgs(): string[] {
    const service = path.join(this.context.extensionPath, 'python', 'service.py');
    if (!fs.existsSync(service)) {
      throw new Error(`找不到 Python 服务：${service}`);
    }
    const settings = this.effectiveSettings();
    const args = [service];
    if (settings.potcarDir) args.push('--potcar-dir', settings.potcarDir);
    const llm = this.llmConfigPath();
    if (llm) args.push('--llm-config', llm);
    if (!settings.boundary) args.push('--no-boundary');
    args.push('--bonds', settings.bonds);
    return args;
  }

  async ensureStarted(): Promise<CheckInfo> {
    if (this.disposed) throw new Error('扩展已停用');
    if (this.proc && this.checkInfo) return this.checkInfo;
    if (this.starting) return this.starting;
    this.starting = this.startInternal();
    try {
      return await this.starting;
    } catch (error) {
      this.checkInfo = { ok: false, error: errorMessage(error) };
      throw error;
    } finally {
      this.starting = undefined;
    }
  }

  private async startInternal(): Promise<CheckInfo> {
    const errors: string[] = [];
    let lastInfo: CheckInfo | undefined;
    const args = this.buildArgs();
    for (const python of this.pythonCandidates()) {
      let proc: ChildProcessWithoutNullStreams | undefined;
      try {
        proc = spawn(python, args, {
          cwd: this.workspaceCwd(),
          env: {
            ...process.env,
            PYTHONIOENCODING: 'utf-8',
            PYTHONUTF8: '1',
          },
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'pipe'],
        });
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('启动超时')), 20000);
          proc!.once('spawn', () => {
            clearTimeout(timer);
            resolve();
          });
          proc!.once('error', (error) => {
            clearTimeout(timer);
            reject(error);
          });
        });
        this.proc = proc;
        this.setupProcess(proc);
        const info = await this.requestDirect<CheckInfo>('check', {}, 30000);
        if (info.ok) {
          this.checkInfo = { ...info, pythonPath: python };
          this.pythonUsed = python;
          this.output.appendLine(`[backend] ${python} · ${info.python ?? ''} · numpy ${info.numpy ?? '?'} · ase ${info.ase ?? '?'}`);
          return this.checkInfo;
        }
        lastInfo = { ...info, pythonPath: python };
        errors.push(`${python}: ${info.error || '依赖检查失败'}`);
        this.stopProcess();
      } catch (error) {
        errors.push(`${python}: ${errorMessage(error)}`);
        this.stopProcess();
      }
    }
    if (lastInfo) {
      this.checkInfo = lastInfo;
      this.output.appendLine(`[backend] 环境未就绪：${lastInfo.error || ''}`);
      return lastInfo;
    }
    throw new Error(
      '无法启动 Python 后端。请设置 generateVasp.pythonPath，或安装 Python 与依赖。\n' +
      errors.join('\n')
    );
  }

  private setupProcess(proc: ChildProcessWithoutNullStreams) {
    this.rl = readline.createInterface({ input: proc.stdout });
    this.rl.on('line', (line) => {
      const text = line.trim();
      if (!text) return;
      let message: any;
      try {
        message = JSON.parse(text);
      } catch {
        this.output.appendLine(`[backend] ${text}`);
        return;
      }
      if (message && message.id !== undefined && message.id !== null && this.pending.has(message.id)) {
        const pending = this.pending.get(message.id)!;
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.ok) {
          pending.resolve(message.result);
        } else {
          pending.reject(new Error(message.error || '后端请求失败'));
        }
        return;
      }
      if (message && message.event) {
        this.events.fire(message);
        return;
      }
      this.output.appendLine(`[backend] ${text}`);
    });
    proc.stderr.on('data', (chunk: Buffer) => {
      const text = chunk.toString('utf8').trimEnd();
      if (text) this.output.appendLine(`[python] ${text}`);
    });
    proc.on('error', (error) => {
      if (this.proc === proc) {
        this.output.appendLine(`[backend] 进程错误：${errorMessage(error)}`);
        this.events.fire({ event: 'backendExit', message: errorMessage(error) });
      }
    });
    proc.on('exit', (code, signal) => {
      if (this.proc === proc) {
        this.proc = undefined;
        this.rl?.close();
        this.rl = undefined;
        this.failPending(`Python 服务已退出（code ${code ?? '?'}${signal ? `, ${signal}` : ''}）`);
        this.events.fire({ event: 'backendExit', code, signal });
      }
    });
  }

  private failPending(reason: string) {
    for (const [, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error(reason));
    }
    this.pending.clear();
  }

  private stopProcess() {
    const proc = this.proc;
    this.proc = undefined;
    this.rl?.close();
    this.rl = undefined;
    this.failPending('Python 服务已停止');
    if (proc) {
      try {
        proc.stdin.end();
      } catch {
        // ignore
      }
      try {
        proc.kill();
      } catch {
        // ignore
      }
    }
  }

  async restart(): Promise<CheckInfo> {
    this.stopProcess();
    this.checkInfo = undefined;
    this.pythonUsed = undefined;
    return this.ensureStarted();
  }

  private sendRequest<T>(method: string, params: object, timeoutMs: number): Promise<T> {
    const proc = this.proc;
    if (!proc) return Promise.reject(new Error('Python 服务未启动'));
    return new Promise<T>((resolve, reject) => {
      const id = ++this.seq;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} 请求超时（${Math.round(timeoutMs / 1000)} s）`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      proc.stdin.write(JSON.stringify({ id, method, params }) + '\n', (error) => {
        if (error) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(error);
        }
      });
    });
  }

  private requestDirect<T>(method: string, params: object, timeoutMs: number): Promise<T> {
    return this.sendRequest<T>(method, params, timeoutMs);
  }

  async request<T = any>(method: string, params: object = {}, timeoutMs = 120000): Promise<T> {
    if (!this.proc) {
      const info = await this.ensureStarted();
      if (!info.ok || !this.proc) {
        throw new Error(info.error || 'Python 服务未启动');
      }
    }
    return this.sendRequest<T>(method, params, timeoutMs);
  }

  dispose() {
    this.disposed = true;
    this.stopProcess();
    this.events.dispose();
    this.output.dispose();
  }
}

class VaspPanel implements vscode.Disposable {
  private readonly panel: vscode.WebviewPanel;
  private readonly subscription: vscode.Disposable;
  private ready = false;
  private disposed = false;
  private pendingPath?: string;
  private readonly mediaDir: vscode.Uri;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly backend: BackendService,
    initialPath: string | undefined,
    private readonly onClosed: () => void,
  ) {
    this.mediaDir = vscode.Uri.joinPath(context.extensionUri, 'media');
    this.pendingPath = initialPath;
    this.panel = vscode.window.createWebviewPanel(
      PANEL_TYPE,
      'Generate-VASP',
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [this.mediaDir],
      },
    );
    this.panel.iconPath = vscode.Uri.joinPath(this.mediaDir, 'app.png');
    this.panel.webview.html = this.html();
    this.subscription = this.backend.events.event((event) => {
      this.post({ type: event.event, ...event });
    });
    this.panel.webview.onDidReceiveMessage((message) => {
      void this.onMessage(message);
    });
    this.panel.onDidDispose(() => {
      this.disposed = true;
      this.subscription.dispose();
      this.onClosed();
    });
  }

  private html(): string {
    const webview = this.panel.webview;
    const indexPath = path.join(this.context.extensionPath, 'media', 'index.html');
    let template: string;
    try {
      template = fs.readFileSync(indexPath, 'utf8');
    } catch (error) {
      return `<!doctype html><html><body><h2>缺少 media/index.html</h2><pre>${escapeHtml(errorMessage(error))}</pre></body></html>`;
    }
    const uri = (name: string) => webview.asWebviewUri(
      vscode.Uri.joinPath(this.mediaDir, name).with({ query: `v=${Date.now().toString(36)}` }),
    ).toString();
    return template
      .replace(/\{\{cspSource\}\}/g, webview.cspSource)
      .replace(/\{\{styleUri\}\}/g, uri('style.css'))
      .replace(/\{\{mainUri\}\}/g, uri('main.js'))
      .replace(/\{\{viewerUri\}\}/g, uri('viewer.html'))
      .replace(/\{\{iconUri\}\}/g, uri('app.png'));
  }

  private post(message: any) {
    if (this.disposed) return;
    void this.panel.webview.postMessage(message);
  }

  reveal(file?: string) {
    if (file) {
      if (this.ready) {
        this.post({ type: 'openPath', path: file });
      } else {
        this.pendingPath = file;
      }
    }
    this.panel.reveal(vscode.ViewColumn.One);
  }

  onBackendRestarted(check: CheckInfo) {
    this.post({ type: 'backendRestarted', check });
  }

  onSettingsChanged() {
    this.post({ type: 'settingsChanged', settings: this.backend.effectiveSettings() });
  }

  private async sendInit() {
    let check: CheckInfo;
    try {
      check = await this.backend.ensureStarted();
    } catch (error) {
      check = { ok: false, error: errorMessage(error) };
    }
    const structures = structureState();
    this.post({
      type: 'init',
      check,
      settings: this.backend.effectiveSettings(),
      structures,
    });
    this.ready = true;
    const settings = this.backend.effectiveSettings();
    const active = structures.open.find((item) => item.active)?.path ?? structures.open[0]?.path;
    const target = this.pendingPath
      || active
      || (settings.autoLoadLastStructure ? settings.lastStructure : undefined);
    this.pendingPath = undefined;
    if (check.ok && target) {
      this.post({ type: 'openPath', path: target });
    }
  }

  updateStructureTabs(open: StructureTab[]) {
    this.post({ type: 'structureTabs', open, root: workspaceRoot() ?? '' });
  }

  private async onMessage(message: any) {
    if (this.disposed || !message || typeof message.type !== 'string') return;
    switch (message.type) {
      case 'ready':
        await this.sendInit();
        break;
      case 'rpc':
        await this.handleRpc(message);
        break;
      case 'host':
        await this.handleHost(message);
        break;
      case 'log':
        this.backend.output.appendLine(`[webview] ${String(message.message ?? '')}`);
        break;
      default:
        break;
    }
  }

  private async handleRpc(message: any) {
    try {
      const timeout = message.method === 'generate' ? 180000 : 120000;
      const result = await this.backend.request(message.method, message.params || {}, timeout);
      if (message.method === 'loadStructure' && result?.state?.source) {
        await this.backend.rememberStructure(result.state.source);
      }
      this.post({ type: 'rpcResult', id: message.id, ok: true, result });
    } catch (error) {
      this.post({ type: 'rpcResult', id: message.id, ok: false, error: errorMessage(error) });
    }
  }

  private async handleHost(message: any) {
    const reply = (value: any) => this.post({ type: 'hostResult', id: message.id, ok: true, value });
    try {
      switch (message.action) {
        case 'pickStructure': {
          reply(await pickStructureFile() ?? null);
          break;
        }
        case 'getOpenStructures': {
          reply(structureState());
          break;
        }
        case 'listDirectory': {
          const root = workspaceRoot();
          if (!root) throw new Error('没有打开的项目文件夹');
          const rootAbs = path.resolve(root);
          const requested = message.path ? path.resolve(String(message.path)) : rootAbs;
          const rel = path.relative(rootAbs, requested);
          if (rel === '..' || rel.startsWith('..' + path.sep) || path.isAbsolute(rel)) {
            throw new Error('目录不在当前项目内');
          }
          const entries = fs.readdirSync(requested, { withFileTypes: true });
          const dirs: any[] = [];
          const files: any[] = [];
          for (const entry of entries) {
            const full = path.join(requested, entry.name);
            const relative = path.relative(rootAbs, full).replace(/\\/g, '/');
            if (entry.isDirectory()) {
              if (IGNORED_DIRS.has(entry.name)) continue;
              dirs.push({ name: entry.name, path: full, relative });
            } else if (entry.isFile() && isStructureFile(entry.name)) {
              files.push({ name: entry.name, path: full, relative });
            }
          }
          dirs.sort((a, b) => a.name.localeCompare(b.name));
          files.sort((a, b) => a.name.localeCompare(b.name));
          reply({
            root: rootAbs,
            path: requested,
            relative: path.relative(rootAbs, requested).replace(/\\/g, '/'),
            dirs: dirs.slice(0, 300),
            files: files.slice(0, 300),
            truncated: dirs.length > 300 || files.length > 300,
          });
          break;
        }
        case 'pickPotcarDir': {
          const result = await vscode.window.showOpenDialog({
            canSelectMany: false,
            canSelectFiles: false,
            canSelectFolders: true,
            openLabel: '选择赝势库目录',
          });
          reply(result?.[0]?.fsPath ?? null);
          break;
        }
        case 'pickSaveAs': {
          const defaultPath = message.defaultPath
            || path.join(os.homedir(), message.fileName || 'INCAR');
          const result = await vscode.window.showSaveDialog({
            defaultUri: vscode.Uri.file(defaultPath),
            filters: { '所有文件': ['*'] },
            saveLabel: '保存',
          });
          reply(result?.fsPath ?? null);
          break;
        }
        case 'confirm': {
          const confirmLabel = message.confirmLabel || '确定';
          const answer = await vscode.window.showWarningMessage(
            message.message || '确认操作？',
            { modal: true, detail: message.detail },
            confirmLabel,
          );
          reply(answer === confirmLabel);
          break;
        }
        case 'openExternal': {
          if (message.path) {
            await vscode.env.openExternal(vscode.Uri.file(message.path));
          }
          reply(true);
          break;
        }
        case 'setSetting': {
          const allowed = new Set(['potcarDir', 'boundary', 'bonds', 'lastStructure']);
          if (!allowed.has(message.key)) throw new Error(`不允许保存设置 ${message.key}`);
          await this.context.globalState.update(STATE_PREFIX + message.key, message.value);
          reply(true);
          break;
        }
        case 'installDeps': {
          await vscode.commands.executeCommand('generateVasp.installDependencies');
          reply(true);
          break;
        }
        case 'restartBackend': {
          const check = await this.backend.restart();
          this.post({ type: 'backendRestarted', check });
          reply(check);
          break;
        }
        case 'openSettings': {
          await vscode.commands.executeCommand('workbench.action.openSettings', 'generateVasp');
          reply(true);
          break;
        }
        case 'log': {
          this.backend.output.appendLine(`[webview] ${String(message.message ?? '')}`);
          reply(true);
          break;
        }
        default:
          throw new Error(`未知宿主操作：${message.action}`);
      }
    } catch (error) {
      this.post({ type: 'hostResult', id: message.id, ok: false, error: errorMessage(error) });
    }
  }

  dispose() {
    this.panel.dispose();
  }
}

class LauncherProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private readonly mediaDir: vscode.Uri;
  private readonly subscription: vscode.Disposable;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly backend: BackendService,
  ) {
    this.mediaDir = vscode.Uri.joinPath(context.extensionUri, 'media');
    this.subscription = backend.events.event((event) => {
      if (['recentChanged', 'backendExit'].includes(event.event)) {
        this.refresh();
      }
    });
    context.subscriptions.push(this.subscription);
  }

  resolveWebviewView(view: vscode.WebviewView) {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.mediaDir],
    };
    view.webview.onDidReceiveMessage((message) => {
      void this.onMessage(message);
    });
    this.render();
  }

  private async onMessage(message: any) {
    switch (message?.type) {
      case 'open':
        await vscode.commands.executeCommand('generateVasp.open');
        break;
      case 'pick':
        await vscode.commands.executeCommand('generateVasp.openStructure');
        break;
      case 'openPath':
        if (message.path) {
          await vscode.commands.executeCommand(
            'generateVasp.openStructure',
            vscode.Uri.file(message.path),
          );
        }
        break;
      case 'install':
        await vscode.commands.executeCommand('generateVasp.installDependencies');
        break;
      default:
        break;
    }
  }

  refresh() {
    if (this.view) this.render();
  }

  private render() {
    if (!this.view) return;
    const webview = this.view.webview;
    const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
    const check = this.backend.checkInfo;
    const settings = this.backend.effectiveSettings();
    const iconUri = webview.asWebviewUri(vscode.Uri.joinPath(this.mediaDir, 'app.png')).toString();
    const status = this.statusHtml(check);
    const recents = settings.recent.slice(0, 6).map((file) => {
      const name = path.basename(file);
      const dir = path.dirname(file);
      return `<button class="recent" data-path="${escapeHtml(file)}" title="${escapeHtml(file)}">
        <span class="file">${escapeHtml(name)}</span>
        <span class="dir">${escapeHtml(dir)}</span>
      </button>`;
    }).join('');
    const recentBlock = recents
      ? `<div class="label">最近结构</div><div class="recents">${recents}</div>`
      : '';
    const install = check && !check.ok
      ? `<button class="secondary" data-install="1">安装 Python 依赖</button>`
      : '';
    const csp = webview.cspSource;
    this.view.webview.html = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${csp} data:; style-src 'unsafe-inline' ${csp}; script-src 'nonce-${nonce}';">
<style>
  body { padding: 12px 12px 18px; color: var(--vscode-foreground); font-family: var(--vscode-font-family); font-size: 12px; }
  .hero { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
  .hero img { width: 40px; height: 40px; border-radius: 10px; }
  .title { font-size: 14px; font-weight: 700; }
  .sub { color: var(--vscode-descriptionForeground); margin-top: 2px; line-height: 1.45; }
  button { width: 100%; border: 0; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px;
    font-family: inherit; font-size: 12px; cursor: pointer; }
  .primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); font-weight: 600; }
  .primary:hover { background: var(--vscode-button-hoverBackground); }
  .secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
  .secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }
  .status { margin: 10px 0 12px; padding: 8px 10px; border-radius: 6px; line-height: 1.55;
    background: var(--vscode-textBlockQuote-background); border-left: 3px solid var(--vscode-textLink-foreground); }
  .status.bad { border-left-color: var(--vscode-errorForeground); }
  .status .ok { color: var(--vscode-testing-iconPassed); }
  .status .bad { color: var(--vscode-errorForeground); }
  .label { margin: 12px 0 6px; font-weight: 600; color: var(--vscode-descriptionForeground); }
  .recent { text-align: left; background: var(--vscode-list-hoverBackground); color: var(--vscode-foreground);
    border: 1px solid var(--vscode-panel-border); padding: 6px 8px; }
  .recent:hover { background: var(--vscode-list-activeSelectionBackground); color: var(--vscode-list-activeSelectionForeground); }
  .recent .file { display: block; font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
  .recent .dir { display: block; opacity: .72; font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
  .hint { color: var(--vscode-descriptionForeground); margin-top: 12px; line-height: 1.5; }
</style>
</head>
<body>
  <div class="hero">
    <img src="${iconUri}" alt="">
    <div>
      <div class="title">Generate-VASP</div>
      <div class="sub">CIF / POSCAR → 固定原子 → INCAR · KPOINTS · POSCAR · POTCAR</div>
    </div>
  </div>
  <button class="primary" data-open="1">打开生成器</button>
  <button class="secondary" data-pick="1">用结构文件打开…</button>
  ${install}
  ${status}
  ${recentBlock}
  <div class="hint">生成器在主编辑区打开，包含 3D 结构视图、12 个计算项目与可编辑输出文件。</div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.querySelectorAll('[data-open]').forEach(el => el.addEventListener('click', () => vscode.postMessage({type:'open'})));
    document.querySelectorAll('[data-pick]').forEach(el => el.addEventListener('click', () => vscode.postMessage({type:'pick'})));
    document.querySelectorAll('[data-install]').forEach(el => el.addEventListener('click', () => vscode.postMessage({type:'install'})));
    document.querySelectorAll('.recent').forEach(el => el.addEventListener('click', () => vscode.postMessage({type:'openPath', path: el.dataset.path})));
  </script>
</body>
</html>`;
  }

  private statusHtml(check?: CheckInfo): string {
    if (!check) {
      return '<div class="status">正在检查 Python 环境…</div>';
    }
    if (!check.ok) {
      return `<div class="status bad"><b>环境未就绪</b><br>${escapeHtml(check.error || '未知错误')}<br>
        请安装 Python 依赖，或在设置中指定解释器。</div>`;
    }
    const potcar = check.potcarExists
      ? '<span class="ok">✓ 已找到</span>'
      : '<span class="bad">✗ 未找到，可在生成器中指定</span>';
    return `<div class="status">
      <b>环境就绪</b><br>
      Python ${escapeHtml(check.python || '?')} · numpy ${escapeHtml(check.numpy || '?')} · ase ${escapeHtml(check.ase || '?')}<br>
      赝势库 ${potcar}
    </div>`;
  }
}

function installDependencies(backend: BackendService) {
  const cfg = vscode.workspace.getConfiguration('generateVasp');
  const python = backend.pythonUsed
    || (cfg.get<string>('pythonPath', '') || '').trim()
    || (process.platform === 'win32' ? 'python' : 'python3');
  const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const terminal = vscode.window.createTerminal({ name: 'Generate-VASP 依赖', cwd });
  terminal.show();
  terminal.sendText(`${quoteShell(python)} -m pip install numpy ase spglib seekpath`);
  void vscode.window.showInformationMessage(
    '已在新终端中安装 numpy / ase / spglib / seekpath。安装完成后回到生成器点击“重试”，或重新打开面板。',
  );
}

export function activate(context: vscode.ExtensionContext) {
  const backend = new BackendService(context);
  context.subscriptions.push(backend);

  const launcher = new LauncherProvider(context, backend);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(VIEW_ID, launcher, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  let panel: VaspPanel | undefined;
  const openPanel = (file?: string) => {
    if (!panel) {
      panel = new VaspPanel(context, backend, file, () => {
        panel = undefined;
        launcher.refresh();
      });
    } else {
      panel.reveal(file);
    }
  };

  const tabGroups = (vscode.window as any).tabGroups;
  if (tabGroups && typeof tabGroups.onDidChangeTabs === 'function') {
    context.subscriptions.push(tabGroups.onDidChangeTabs(() => {
      panel?.updateStructureTabs(getOpenStructures());
    }));
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('generateVasp.open', (arg?: any) => {
      const file = typeof arg === 'string' ? arg : arg?.fsPath;
      openPanel(file);
      launcher.refresh();
    }),
    vscode.commands.registerCommand('generateVasp.openStructure', async (uri?: vscode.Uri) => {
      const file = uri?.fsPath ?? await pickStructureFile();
      if (file) openPanel(file);
    }),
    vscode.commands.registerCommand('generateVasp.installDependencies', () => installDependencies(backend)),
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(async (event) => {
      if (!event.affectsConfiguration('generateVasp')) return;
      const cfg = vscode.workspace.getConfiguration('generateVasp');
      if (event.affectsConfiguration('generateVasp.potcarPath')) {
        await context.globalState.update(STATE_PREFIX + 'potcarDir', cfg.get<string>('potcarPath', ''));
      }
      if (event.affectsConfiguration('generateVasp.boundary')) {
        await context.globalState.update(STATE_PREFIX + 'boundary', cfg.get<boolean>('boundary', true));
      }
      if (event.affectsConfiguration('generateVasp.bonds')) {
        await context.globalState.update(STATE_PREFIX + 'bonds', cfg.get<string>('bonds', 'half'));
      }
      const needsRestart = event.affectsConfiguration('generateVasp.pythonPath')
        || event.affectsConfiguration('generateVasp.llmConfigPath');
      if (needsRestart) {
        try {
          const check = await backend.restart();
          panel?.onBackendRestarted(check);
        } catch (error) {
          panel?.onBackendRestarted({ ok: false, error: errorMessage(error) });
        }
        launcher.refresh();
      } else {
        panel?.onSettingsChanged();
        launcher.refresh();
      }
    }),
  );

  void backend.ensureStarted()
    .then(() => launcher.refresh())
    .catch((error) => {
      backend.output.appendLine(`[backend] ${errorMessage(error)}`);
      launcher.refresh();
    });
}

export function deactivate() {
  // backend 通过 context.subscriptions 释放
}
