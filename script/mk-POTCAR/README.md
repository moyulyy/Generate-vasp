<div align="center">

# mk-POTCAR

根据元素列表或 `POSCAR` 文件，按顺序拼接 VASP 赝势，生成 `POTCAR` 文件。

![Python](https://img.shields.io/badge/Python-3.6+-3776AB?style=flat-square&logo=python&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)

命令行 / Python 导入 / LLM 工具调用，三种方式任选

</div>

---

> ⚠️ **许可说明**：`POTCAR` 赝势文件是 VASP 的专有数据，受 VASP 许可协议约束。本仓库**不包含、也不分发**任何 `POTCAR` 文件，请从你自己的 VASP 安装中获取赝势数据库。

## ✨ 功能

- 支持直接给元素字串（如 POSCAR 第 6 行的 `"Bi Mo O"`）
- 支持直接给 `POSCAR` 文件路径，自动读取第 6 行元素
- 元素顺序决定 `POTCAR` 拼接顺序
- 输出可以是文件，也可以是文件夹（自动在文件夹内生成 `POTCAR`）
- 可选清理：生成时删除各元素的 `SHA256` / `COPYR` 行
- 零第三方依赖（仅标准库 `argparse`、`os`）

## 🚀 快速开始

### 1. 准备赝势数据库

按「标签 → POTCAR」组织你的赝势数据库（本仓库不提供），每个标签一个文件夹：

```
POTCAR_DIR/
├── Bi_d/
│   └── POTCAR
├── Mo_sv/
│   └── POTCAR
└── O/
    └── POTCAR
```

在 `mk_potcar.py` 顶部配置默认数据库路径：

```python
POTCAR_DIR = r"D:\fast-vasp\script\POTCAR"
```

### 2. 生成 POTCAR

```bash
python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR
```

## 📖 使用方式

### 1. 命令行（CLI）

```bash
# 直接给元素字串
python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR

# 给 POSCAR 文件，自动读取第 6 行元素
python mk_potcar.py D:/work/POSCAR -o D:/work

# 指定赝势数据库路径（覆盖脚本内置的 POTCAR_DIR）
python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR --potcar-dir D:/fast-vasp/script/POTCAR

# 生成时删除各元素 POTCAR 中的 SHA256 / COPYR 行
python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR --clean
```

> 默认生成的 `POTCAR` **不做清理**（保留各元素的 `SHA256` / `COPYR` 行）；只有在明确加 `--clean` 时才删除这些行。

| 参数 | 说明 |
| --- | --- |
| `input`（位置参数） | 元素字串（如 `"Bi Mo O"`）或 POSCAR 文件路径 |
| `-o, --output` | 输出路径：文件或文件夹（文件夹会自动生成其中的 `POTCAR`） |
| `--potcar-dir` | 可选，赝势数据库根路径，默认用脚本里的 `POTCAR_DIR` |
| `--clean` | 可选，加此参数时才删除各元素 POTCAR 中所有包含 `SHA256` 或 `COPYR` 的行 |

### 2. Python 导入调用

```python
from mk_potcar import make_potcar

# 直接给元素字串
make_potcar("Bi Mo O", "D:/work/POTCAR")

# 指定赝势数据库路径
make_potcar("Bi Mo O", "D:/work/POTCAR", potcar_dir="D:/fast-vasp/script/POTCAR")

# 生成时删除 SHA256 / COPYR 行（仅明确传 clean=True 时才清理）
make_potcar("Bi Mo O", "D:/work/POTCAR", clean=True)
```

```python
def make_potcar(elements_string, output_path, potcar_dir=None, clean=False):
    """
    elements_string: 元素字串，例如 "Bi Mo O"
    output_path:     输出文件或文件夹路径
    potcar_dir:      赝势数据库路径，默认用脚本里的 POTCAR_DIR
    clean:           True 时删除各元素 POTCAR 中所有包含 SHA256 或 COPYR 的行
    """
```

### 3. LLM 工具调用（function calling）

工具的函数为 `make_potcar`，对应的 JSON Schema 已放在 [`mk_potcar_schema.json`](./mk_potcar_schema.json)，可直接作为 LLM 的 tool 定义使用。

```json
{
  "type": "function",
  "function": {
    "name": "make_potcar",
    "description": "根据 VASP 计算所需的元素列表按顺序拼接各元素的赝势，生成 POTCAR 文件。",
    "parameters": {
      "type": "object",
      "properties": {
        "elements_string": { "type": "string", "description": "元素字串，如 'Bi Mo O'" },
        "output_path": { "type": "string", "description": "输出文件或文件夹路径" },
        "potcar_dir": { "type": "string", "description": "可选，赝势数据库根路径" },
        "clean": { "type": "boolean", "description": "可选，默认 false（不清理）。仅当明确为 true 时才删除 SHA256 / COPYR 行" }
      },
      "required": ["elements_string", "output_path"],
      "additionalProperties": false
    }
  }
}
```

收到 LLM 返回的调用参数后，直接调用函数：

```python
import json
from mk_potcar import make_potcar

call = tool_calls[0]                     # LLM 返回的调用
args = json.loads(call.function.arguments)

make_potcar(
    args["elements_string"],
    args["output_path"],
    args.get("potcar_dir"),
    args.get("clean", False),
)
```

## 🧹 清理 SHA256 / COPYR 行

默认生成 `POTCAR` 时**不清理**，完整保留各元素的 `SHA256` 和 `COPYR` 行；只有明确加 `--clean`（或 `clean=True`）时才删除这些行，且不会改动原始赝势数据库文件。

```bash
# 不清理（默认行为）
python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR

# 明确清理
python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR --clean
```

## ⚠️ 注意事项

- `elements_string` 中的元素必须存在于 `ELEMENTS_DICT`，否则报错并跳过。
- 输出为文件夹时，该文件夹需已存在（脚本不会自动创建）。
- 若数据库缺少某元素的 `POTCAR` 文件，会打印错误并中止。
- 元素到标签的映射表 `ELEMENTS_DICT` 与 fast-vasp 项目保持一致（如 `Bi → Bi_d`、`Mo → Mo_sv`、`Na → Na_pv` 等）。

## 📄 许可

本项目代码采用 [MIT License](./LICENSE)。`POTCAR` 赝势文件属 VASP 专有数据，不在本仓库中提供。
