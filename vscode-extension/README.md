# Generate-VASP for VS Code

把 [Generate-VASP](https://github.com/moyulyy/Generate-vasp) 的完整能力搬进 VS Code：在活动栏打开生成器，在编辑器区域的 Webview 中完成 CIF / POSCAR 读取、3D 固定原子、参数设置、文件编辑与生成。

```
CIF / POSCAR → 固定原子 → INCAR · KPOINTS · POSCAR · POTCAR
```

![Generate-VASP](media/app.png)

## 功能

- **12 个计算项目**：弛豫、DOS、能带 SCF / band、差分电荷、Bader、功函数、ELF、COHP、CI-NEB、频率、AIMD。
- **3D 结构视图**：3Dmol.js 渲染，旋转 / 点选 / 框选三种模式，按层或编号批量固定，撤销 / 重做，周期像与跨边界成键显示，正视 / 侧视 / 俯视。
- **电子结构**：启发式、LLM 工具调用或按元素指定 MAGMOM；默认 U 值或自定义 L / U / J；MIX 与细 FFT 网格可编辑。
- **K 点与赝势**：Γ 网格按密度生成，体相能带路径使用 seekpath / ASE；按元素选择赝势变体并检查 ENCUT。
- **计算条件**：隐式溶剂化、电场、恒压 CP-VASP、并行参数、lobsterin、OPTCELL。
- **可编辑输出**：INCAR / KPOINTS / POSCAR / POTCAR / lobsterin / OPTCELL 带语法高亮与查找；手动修改会保留，之后设置改动仍会合并进来。
- **生成与保存**：写到结构文件所在目录，支持单文件保存、另存为、覆盖确认与打开输出文件夹。
- **侧边栏入口**：活动栏 Generate-VASP 图标、最近结构列表、环境状态；`.cif` / `.vasp` / `POSCAR` / `CONTCAR` 编辑器标题栏可直接打开。

## 环境要求

| 依赖 | 说明 |
| --- | --- |
| VS Code ≥ 1.85 | 需要支持 Webview 与 WebviewView |
| Python ≥ 3.9 | 后端复用仓库中的 `gui/` 与 `script/` |
| `numpy`、`ase` | 必需：结构读写、邻居、K 点 |
| `spglib`、`seekpath` | 可选：原胞与 HPKOT 能带路径 |
| POTCAR 赝势库 | 受 VASP 许可约束，项目不附带 |

本扩展不会自动下载 POTCAR。首次使用请在设置 `generateVasp.potcarPath` 或生成器左侧「赝势库」中指定目录，结构为 `赝势库/标签/POTCAR`（如 `Ni_pv/POTCAR`）。

## 开发与运行

```bash
cd vscode-extension
npm install          # 安装 TypeScript 依赖；prepare 会同步 python/resources 与 media 资源
npm run compile      # 编译到 out/
```

用 VS Code 打开 `vscode-extension/` 目录，按 `F5` 启动「运行 Generate-VASP 扩展」调试配置。宿主会按以下顺序寻找 Python：

1. 设置 `generateVasp.pythonPath`；
2. 工作区 `.venv` / `venv` 中的解释器；
3. `python3` / `python`（Windows 还包括 `py`）。

依赖缺失时，生成器会显示错误浮层，可点击「安装依赖」，或执行：

```bash
python -m pip install numpy ase spglib seekpath
```

### 运行测试

```bash
npm run check        # Webview JS 语法
npm run compile      # tsc 严格模式编译
```

## 使用

1. 点击活动栏的 Generate-VASP 图标，在侧边栏点击「打开生成器」；或按 `Ctrl+Alt+V`（macOS `Cmd+Alt+V`）。
2. 点击 / 拖入 CIF、POSCAR 或 CONTCAR。CIF 会自动转换为 POSCAR，同种元素不连续时会重排。
3. 选择计算项目、体系类型、自旋 / DFT+U、K 点密度、赝势与计算条件。
4. 在「结构」页固定原子（点选、框选、底部 N 层、编号输入），或使用全部固定 / 弛豫、撤销 / 重做。
5. 在文件页检查并编辑输出；顶部显示「自动生成 / 手动编辑」与「已保存 / 未保存」。
6. 点击「生成作业文件」（`Ctrl+Enter`）。文件写到结构文件所在目录；底部开关决定写哪些文件。

### 快捷键

| 快捷键 | 操作 |
| --- | --- |
| `Ctrl+O` | 选择结构文件 |
| `Ctrl+S` | 保存当前文件页；在「结构」页时生成全部文件 |
| `Ctrl+Enter` | 生成作业文件 |
| `Ctrl+F`，`F3` / `Shift+F3` | 查找，下一个 / 上一个 |
| `Ctrl+Z` / `Ctrl+Y` | 结构页撤销 / 重做固定操作；编辑器内为文本撤销 / 重做 |
| `1` / `2` / `3`，`Esc` | 3D 视图旋转 / 点选 / 框选，回到旋转 |

## 设置

| 设置 | 默认 | 说明 |
| --- | --- | --- |
| `generateVasp.pythonPath` | 空 | 指定 Python 解释器；留空自动探测 |
| `generateVasp.potcarPath` | 空 | 赝势库目录 |
| `generateVasp.llmConfigPath` | 空 | `llm_config.json` 路径；留空查找工作区根目录 |
| `generateVasp.boundary` | `true` | 显示边界等价原子 |
| `generateVasp.bonds` | `half` | 跨边界成键：`off` / `half` / `ghost` |
| `generateVasp.autoLoadLastStructure` | `false` | 打开面板时自动载入上次结构 |

赝势库、显示选项与最近结构保存在扩展的 `globalState` 中，不写入工作区文件。

### LLM 自旋分析

「LLM 分析」调用 `add-spin` 的 LLM 工具调用流程。连接配置只来自环境变量或 `llm_config.json`，界面不显示也不能修改：

```json
{
  "api_key": "sk-...",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat"
}
```

环境变量 `LLM_API_KEY`（或 `OPENAI_API_KEY` / `DEEPSEEK_API_KEY`）、`LLM_BASE_URL`、`LLM_MODEL` 优先。LLM 只是初猜，仍需比较不同磁序的总能。

## 目录结构

```
vscode-extension/
├── src/extension.ts       扩展宿主：面板、Python 进程、RPC、侧边栏
├── media/
│   ├── index.html         生成器页面骨架
│   ├── main.js            状态同步、设置卡片、编辑器、3D 桥
│   ├── style.css          iOS 浅色主题
│   ├── viewer.html/.js    3Dmol iframe 视图
│   └── 3Dmol-min.js       本地 3Dmol（同步复制）
├── python/
│   ├── service.py         stdio JSON-RPC 常驻服务
│   ├── controller.py      headless 业务状态机
│   └── resources/         同步自仓库 gui/、script/、模板（打包时使用）
├── scripts/sync-resources.js
└── package.json
```

开发模式下 `service.py` 直接回退到仓库根目录的 `gui/`、`script/`、`IncarTemplates/`、`ConditionsTemplates/`；执行 `npm run sync` 后优先使用 `python/resources/`。`out/` 与 `python/resources/` 都是生成产物。

## 打包 VSIX

```bash
cd vscode-extension
npm install
npx @vscode/vsce package
```

`vscode:prepublish` 会先同步资源再编译。打包结果 `generate-vasp-<版本>.vsix` 可用「扩展 → … → 从 VSIX 安装」安装。发行包不包含 `llm_config.json`、POTCAR 或 Python 运行时。

## 已知限制

- 需要本机可用的 Python 与 `numpy`/`ase`；扩展不会替代 VASP 或 POTCAR。
- INCAR 参数来自模板，提交计算前请确认 ENCUT、EDIFF、ISMEAR 等适合体系。
- 体相能带路径在未安装 seekpath 时回退 ASE；表面路径要求真空沿 c。
- LLM 分析需要联网和 API Key，每次分析会产生调用费用。
- 在 3D 视图中修改固定状态会重新生成 POSCAR，手动添加的注释与格式不会保留。
- 无信任工作区（Restricted Mode）中扩展不启用，因为它需要运行本地 Python 并写入文件。

## 许可

MIT。3D 视图使用 [3Dmol.js](https://3dmol.csb.pitt.edu/)（BSD）。POTCAR 受 VASP 许可约束，不随本项目分发。
