# -*- coding: utf-8 -*-
"""
llm_tool.py —— 把 kpoint.py 包装成 LLM 可直接调用的函数
=====================================================================
配合 tool_schema.json / tool_schema.anthropic.json 使用。
调用 generate_kpoints(...) 等价于执行:

    python kpoint.py <poscar> --json [--mode ...] [--type ...] ...

并返回解析后的 dict (与 --json 输出一致):

    {"status": "ok",
     "mode": "lines", "structure_type": "slab", "detected": true,
     "out_file": "KPOINTS", "mesh": null, "lattice": "HEX2D",
     "backend": "ase", "labels": "Γ-M-K",
     "axis": [{"label": "Γ", "x": 0.0}, ...],
     "warnings": [], "kpoints": "...file content..."}

出错时返回 {"status": "error", "error": "..."}。
"""
import json
import subprocess
import sys
from pathlib import Path

_KPOINT = Path(__file__).resolve().with_name("kpoint.py")


def generate_kpoints(
    poscar_filepath: str,
    mode: str = "mesh",
    structure_type: str = "auto",
    threshold: float = 0.04,
    vacuum_threshold: float = 5.0,
    per_seg: int = 20,
    backend: str = "auto",
    output_path: str = "KPOINTS",
) -> dict:
    """生成 VASP KPOINTS 并返回结构化结果 (与 tool schema 的入参一一对应)。

    参数:
      poscar_filepath : POSCAR/CONTCAR 路径
      mode            : "mesh" | "lines"
      structure_type  : "auto" | "mole" | "slab" | "bulk"
      threshold       : K 点密度阈值 (默认 0.04, 越小越密)
      vacuum_threshold: 体系判别真空层阈值 Å (默认 5.0)
      per_seg         : lines 模式每段插点数 (默认 20)
      backend         : "auto" | "ase" | "seekpath"
      output_path     : KPOINTS 写出路径; "-" 表示不写文件
    """
    cmd = [
        sys.executable, str(_KPOINT), str(poscar_filepath),
        "--json",
        "--mode", str(mode),
        "--type", str(structure_type),
        "--threshold", str(threshold),
        "--vacuum-threshold", str(vacuum_threshold),
        "--per-seg", str(per_seg),
        "--backend", str(backend),
        "--output", str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if not proc.stdout.strip():
        return {"status": "error", "error": proc.stderr.strip() or "no output"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "error": proc.stdout.strip()}


if __name__ == "__main__":
    # 简单自测: python llm_tool.py <POSCAR> [lines]
    _args = sys.argv[1:]
    if not _args:
        print("usage: python llm_tool.py <POSCAR> [mesh|lines]")
        raise SystemExit(2)
    _mode = _args[1] if len(_args) > 1 else "mesh"
    print(json.dumps(generate_kpoints(_args[0], mode=_mode, output_path="-"),
                     ensure_ascii=False, indent=2))
