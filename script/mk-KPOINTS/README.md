<div align="center">

# mk-KPOINTS

**从 POSCAR 一键生成 VASP `KPOINTS`** —— Gamma 网格 · 高对称能带路径 · 画图横轴坐标

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![ASE](https://img.shields.io/badge/deps-ase%20%7C%20spglib%20%7C%20seekpath-orange.svg)](requirements.txt)
[![VASP](https://img.shields.io/badge/output-VASP%20KPOINTS-9cf.svg)](https://www.vasp.at/)

[功能](#-功能特性) · [安装](#-安装) · [快速开始](#-快速开始) · [典型示例](#-典型示例) · [原理](#-工作原理) · [API](#-作为模块调用) · [LLM 工具](#-作为-llm-工具调用)

</div>

---

## ✨ 功能特性

- **两种模式**
  - `mesh`：按 K 点密度阈值生成 Gamma 中心网格（`mole` / `slab` / `bulk` 自动约束）。
  - `lines`：自动生成高对称能带路径，输出 VASP 官方 **line mode** 格式。
- **体系自动判别**：按 a/b/c 三方向的真空层厚度自动识别 `mole` / `slab` / `bulk`。
- **路径有依据**
  - `bulk`：默认用 [SeeK-path](https://seekpath.readthedocs.io/)（HPKOT 标准路径，基于 spglib 空间群）。
  - `slab`：用 ASE 二维 Bravais 点阵（源自 **Setyawan–Curtarolo (2010)**，只取 kz=0 面），与 VASPKIT 的二维结果一致。
- **画图友好**：直接输出能带图横轴坐标（等价 VASPKIT 的 `KLABELS`）。
- **纯命令行**：没有独立配置文件，所有参数都在命令行给出。

## 📦 安装

```bash
pip install -r requirements.txt
```

`requirements.txt` = `ase` + `spglib` + `seekpath`。

| 依赖 | 用途 |
| --- | --- |
| `ase` | 必需：读取结构、二维点阵、平面波路径 |
| `spglib` + `seekpath` | **bulk** 的 `lines` 模式（HPKOT 标准路径）|
| — | **slab 的二维路径不需要** spglib/seekpath |

> 未安装 `seekpath` 时 bulk 会自动回退 ASE，并在摘要里提示。

## 🚀 快速开始

```bash
# 1) Gamma 网格（自动判别体系类型）
python kpoint.py POSCAR

# 2) 指定体系类型 + 输出文件
python kpoint.py POSCAR --type slab -o KPOINTS

# 3) 能带高对称路径
python kpoint.py POSCAR --mode lines --per-seg 30

# 4) 生成能带图横轴坐标（等价 VASPKIT KLABELS）
python kpoint.py POSCAR --mode lines --axis-file KLABELS
```

摘要写到 **stderr**，KPOINTS 正文写到 **stdout**，因此可安全重定向：

```bash
python kpoint.py POSCAR -m lines > KPOINTS
```

## 🧰 命令行参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `poscar` | POSCAR 路径（位置参数） | 必填 |
| `-i, --poscar-filepath` | POSCAR 路径（兼容旧用法，与位置参数二选一） | — |
| `-t, --type` | 体系类型 `auto` / `mole` / `slab` / `bulk` | `auto` |
| `-d, --threshold` | K 点密度阈值，越小越密 | `0.04` |
| `--vacuum-threshold` | 自动判别的真空层阈值 (Å) | `5.0` |
| `-o, --output` | 输出文件，`-` 表示只打印 | `KPOINTS` |
| `-m, --mode` | `mesh`（Gamma 网格）/ `lines`（能带路径） | `mesh` |
| `--per-seg` | `lines` 模式每段插点数 | `20` |
| `--backend` | 能带后端 `auto` / `ase` / `seekpath` | `auto` |
| `--print-axis` | 打印高对称点横轴坐标到 stderr | — |
| `--axis-file FILE` | 把横轴坐标写入文件 | — |
| `--json` | 以 JSON 输出结果（供 LLM/程序调用） | — |
| `--print-only` | 只打印，不写文件 | — |
| `-q, --quiet` | 不打印摘要 | — |

---

## 📚 典型示例

### 示例 1 · fcc Cu 体相网格

```bash
$ python kpoint.py Cu_bulk.vasp
[kpoint] 输入     : Cu_bulk.vasp
[kpoint] 体系类型 : bulk (自动判别)
[kpoint] 网格     : 10 x 10 x 10 (阈值 0.0400)
[kpoint] 输出     : KPOINTS
```

生成的 `KPOINTS`：

```
Automatic mesh
0
Gamma
10   10   10
0   0   0
```

### 示例 2 · Pt(111) 表面网格（自动识别 slab）

```bash
$ python kpoint.py Pt111_slab.vasp --threshold 0.03
[kpoint] 体系类型 : slab (自动判别)
[kpoint] 网格     : 13 x 13 x 1 (阈值 0.0300)
[kpoint] 真空层   : a=0.92 b=0.92 c=24.00 Å
```

表面沿真空方向自动取 `1`：`13   13   1`。

### 示例 3 · bcc Fe 体相能带（seekpath 后端）

```bash
$ python kpoint.py Fe_bcc.vasp -m lines --print-axis
[kpoint] 模式     : lines (后端 seekpath, 点阵 cI1, 高对称路径 Γ-H-N-P)
# band x-axis  backend=seekpath lattice=cI1
# label         x(1/Angstrom)
Γ                 0.000000
H                 2.189263
N                 3.737306
Γ                 5.285349
P                 7.181306
H                 9.077264
P                 9.077264
N                10.171895
```

生成的 `KPOINTS` 片段：

```
k-points along high symmetry path
20
line mode
Reciprocal
0.00000000 0.00000000 0.00000000  ! Γ
0.50000000 -0.50000000 0.50000000  ! H

0.50000000 -0.50000000 0.50000000  ! H
0.00000000 0.00000000 0.50000000  ! N
...
```

### 示例 4 · MoS₂ 单层能带（二维 HEX2D）

```bash
$ python kpoint.py MoS2.vasp -m lines --print-axis
[kpoint] 模式     : lines (后端 ase, 点阵 HEX2D, 高对称路径 Γ-M-K)
# label         x(1/Angstrom)
Γ                 0.000000
M                 1.140754
K                 1.799369
Γ                 3.116599
```

六方二维表面得到标准的 **Γ-M-K-Γ**，横轴坐标可直接用作能带图的 xticks。

### 示例 5 · 斜方表面 γ=75.25°（二维 OBL）

```bash
$ python kpoint.py Pt_oblique.vasp -m lines --print-axis
[kpoint] 模式     : lines (后端 ase, 点阵 OBL, 高对称路径 Γ-Y-H-C-H1-X)
# label         x(1/Angstrom)
Γ                 0.000000
Y                 0.828737
H                 1.822498
C                 2.151947
H1                2.481395
X                 3.029808
Γ                 4.201819
```

`a≠b` 且 `γ≠90°` 时自动识别为斜方 `OBL`，给出 `Γ-Y-H-C-H1-X-Γ`。

---

## 🔍 工作原理

### 体系类型判别

读取 POSCAR 后，计算 a/b/c 三个方向的最大周期空隙（真空层厚度），阈值 `--vacuum-threshold`（默认 5.0 Å）：

| 条件 | 类型 |
| --- | --- |
| a、b、c 都有真空 | `mole` |
| a、b 无真空，c 有真空 | `slab` |
| 三个方向都无真空 | `bulk` |
| 其它 | `unknown`（回退按 bulk 处理） |

### mesh 规则

对每个晶格方向求最小整数 `k`，使 `1 / 晶格长度 / k <= 阈值`：

- `mole → (1, 1, 1)`
- `slab → (ka, kb, 1)`
- `bulk → (ka, kb, kc)`

### 能带路径后端

| 体系 | 默认后端 | 依赖 |
| --- | --- | --- |
| `slab` / 二维材料 | ASE 二维点阵 | 无 |
| `bulk` | **seekpath**（HPKOT 标准路径） | spglib + seekpath |
| `bulk` 回退 | ASE 三维点阵 | spglib |

> 二维/表面一律走 ASE；即使 `--backend seekpath` 也会自动回退并警告（seekpath 不支持二维）。

### slab 二维高对称点（依据）

把第三个方向设为非周期（`pbc=[True, True, False]`），只在 **kz=0** 面内识别二维 Bravais 点阵：

| 面内几何 | 点阵 | 路径 |
| --- | --- | --- |
| a=b, γ=90° | `SQR` | M-Γ-X-M |
| a≠b, γ=90° | `RECT` | Γ-X-S-Y-Γ |
| a=b, γ=60°/120° | `HEX2D` | Γ-M-K-Γ |
| a≠b, γ≠90° | `OBL` | Γ-Y-H-C-H1-X-Γ |
| a=b, γ 其它 | `CRECT` | Γ-X-A1-Y-Γ |

**出处（可追溯）**：

- 数据源为 `ase/dft/kpoints.py` 的 `sc_special_points`，其源码注释注明遵循 **Setyawan & Curtarolo**, *High-throughput electronic band structure calculations: Challenges and tools*, Comp. Mat. Sci. **49**, 299–312 (2010)，DOI [10.1016/j.commatsci.2010.05.010](https://doi.org/10.1016/j.commatsci.2010.05.010)。
- 二维点阵定义在 `ase/lattice/__init__.py`：`HEX2D/RECT/SQR` 取三维数据在 kz=0 面内的子集（`get_subset_points(...)`）；`OBL/CRECT` 由解析公式给出（`η=(1−a·cosγ/b)/(2sin²γ)`，`ν=1/2−η·b·cosγ/a`）。
- 与 **VASPKIT** 一致：VASPKIT 的 2D K-path（task 302）对石墨烯/MoS₂ 同样给出 `Γ-M-K-Γ`；VASPKIT 文档亦指出 pymatgen/seeK-path 仅支持 3D。

> 前提：真空沿 **c 轴**（与 VASPKIT 二维一致）。做能带建议使用原胞（超胞会得到折叠后的较小 BZ）。

### 能带图横轴坐标

横轴为路径上的累积倒空间距离（1/Å，含 2π）：

```
x_0 = 0,   x_i = x_{i-1} + |k_i − k_{i-1}|
```

语义等价于 VASPKIT 的 `KLABELS`（`LABEL value`）与 `BAND_REFORMATTED.dat` 首列。

```bash
# 打印到 stderr
python kpoint.py POSCAR -m lines --print-axis
# 或写入文件
python kpoint.py POSCAR -m lines --axis-file KLABELS
```

### 输出格式

`lines` 模式严格遵循 VASP 官方格式（第 3 行 `line mode`，第 4 行坐标系）：

```
k-points along high symmetry path
 20
line mode
Reciprocal
 0.00000000 0.00000000 0.00000000  ! Γ
 0.00000000 -0.50000000 0.00000000  ! M
 ...
```

---

## 🐍 作为模块调用

```python
from kpoint import (
    create_vasp_kpoints,      # mesh: 生成并写出, 返回字符串
    compute_kpoint_mesh,      # (ka, kb, kc)
    detect_structure_type,    # "mole" / "slab" / "bulk" / "unknown"
    build_band_path,          # lines: dict(segments/labels/lattice/axis/backend/warnings)
    format_axis,              # [(label, x)] -> 文本表 (KLABELS)
    generate_kpoints,         # 统一入口, 返回 (content, info)
)

# mesh
content = create_vasp_kpoints("POSCAR", structure_type="bulk", output_path="KPOINTS")

# lines + 横轴坐标
path = build_band_path(atoms, backend="auto")
print(path["backend"], path["lattice"], path["labels"])
for label, x in path["axis"]:
    print(label, x)
```

## 🤖 作为 LLM 工具调用

本工具可直接被 LLM（OpenAI / Anthropic 等）作为 function/tool 调用，仓库提供：

| 文件 | 说明 |
| --- | --- |
| [`tool_schema.json`](tool_schema.json) | OpenAI function-calling 格式的工具定义 |
| [`tool_schema.anthropic.json`](tool_schema.anthropic.json) | Anthropic tool 格式（`input_schema`） |
| [`llm_tool.py`](llm_tool.py) | Python 包装函数 `generate_kpoints(...)`，返回结构化 dict |

### 工具 schema（节选）

```json
{
  "type": "function",
  "function": {
    "name": "generate_kpoints",
    "description": "根据 POSCAR 生成 VASP KPOINTS ...",
    "parameters": {
      "type": "object",
      "properties": {
        "poscar_filepath": { "type": "string", "description": "POSCAR 路径" },
        "mode": { "type": "string", "enum": ["mesh", "lines"], "default": "mesh" },
        "structure_type": { "type": "string", "enum": ["auto", "mole", "slab", "bulk"], "default": "auto" },
        "threshold": { "type": "number", "default": 0.04 },
        "vacuum_threshold": { "type": "number", "default": 5.0 },
        "per_seg": { "type": "integer", "default": 20 },
        "backend": { "type": "string", "enum": ["auto", "ase", "seekpath"], "default": "auto" },
        "output_path": { "type": "string", "default": "KPOINTS" }
      },
      "required": ["poscar_filepath"]
    }
  }
}
```

### 调用与返回

`kpoint.py --json` 输出结构化结果；`llm_tool.generate_kpoints(...)` 直接返回该 dict：

```python
from llm_tool import generate_kpoints

res = generate_kpoints("POSCAR", mode="lines", output_path="-")
print(res["backend"], res["lattice"], res["labels"])   # ase HEX2D Γ-M-K
for p in res["axis"]:
    print(p["label"], p["x"])                          # 能带图 xticks
print(res["kpoints"])                                  # KPOINTS 正文
```

返回结构：

```json
{
  "status": "ok",
  "mode": "lines",
  "structure_type": "slab",
  "detected": true,
  "out_file": null,
  "mesh": null,
  "lattice": "HEX2D",
  "backend": "ase",
  "labels": "Γ-M-K",
  "axis": [{"label": "Γ", "x": 0.0}, {"label": "M", "x": 1.3087}],
  "warnings": [],
  "kpoints": "k-points along high symmetry path\n20\nline mode\n..."
}
```

> 在 pi 等 agent 中，可把 `tool_schema.json` 的 `parameters` 作为该工具的参数 schema，执行时运行 `python kpoint.py ... --json` 并解析 stdout 即可。

## 📄 许可

[MIT](LICENSE) © 2026 lyy