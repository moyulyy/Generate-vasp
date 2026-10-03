# -*- coding: utf-8 -*-
"""
kpoint.py —— 根据 POSCAR 生成 VASP KPOINTS (命令行工具)
=====================================================================
自包含, 无独立配置文件; 所有参数均在命令行给出。

K 点密度规则 (--threshold, 默认 0.04):
  对每个晶格方向求最小整数 k, 使 1/晶格长度/k <= 阈值
体系类型约束:
  mole -> (1,1,1)          slab -> (ka,kb,1)          bulk -> (ka,kb,kc)
类型为 auto 时按真空层厚度自动判别 (--vacuum-threshold, 默认 5.0 Å)。

用法示例:
  python kpoint.py POSCAR
  python kpoint.py POSCAR --type slab -o KPOINTS
  python kpoint.py POSCAR --threshold 0.03 --print-only
  python kpoint.py POSCAR --mode lines --per-seg 30      # 能带高对称路径
  python kpoint.py POSCAR --mode lines --print-axis      # 打印横轴坐标 (供画图)
  python kpoint.py POSCAR --mode lines --backend seekpath  # bulk 用 seekpath
  python kpoint.py --help

作为模块:
  from kpoint import create_vasp_kpoints, compute_kpoint_mesh, detect_structure_type
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
from ase.io import read
from ase.dft.kpoints import parse_path_string

# ---------------------------------------------------------------------------
# 默认参数 (全部通过命令行覆盖, 不使用单独的配置文件)
# ---------------------------------------------------------------------------

DEFAULT_KPOINT_DENSITY = 0.04    # K 点密度阈值 (--threshold)
DEFAULT_VACUUM_THRESHOLD = 5.0   # 体系类型判别的真空层阈值 Å (--vacuum-threshold)
_MAX_K = 49
_EPS = 1e-8


def get_kpoint_density_threshold():
    """默认 K 点密度阈值 (0.04); 命令行 --threshold 可覆盖。"""
    return DEFAULT_KPOINT_DENSITY


def get_vacuum_threshold():
    """默认真空层阈值 (5.0 Å); 命令行 --vacuum-threshold 可覆盖。"""
    return DEFAULT_VACUUM_THRESHOLD


# ---------------------------------------------------------------------------
# K 点网格
# ---------------------------------------------------------------------------

def calculate_kpoints_density(lattice_parameter, threshold=0.04):
    """求满足 1/晶格长度/k <= threshold 的最小整数 k (1~49)。"""
    for k_value in range(1, _MAX_K + 1):
        if 1.0 / lattice_parameter / k_value <= threshold:
            return k_value
    raise ValueError(
        f"无法为晶格参数 {lattice_parameter} 确定 K 点网格 (阈值 {threshold})。"
    )


def compute_kpoint_mesh(poscar_filepath, threshold=None):
    """读取 POSCAR (ASE), 返回三个晶格方向的 k 网格 (ka, kb, kc)。"""
    structure = read(poscar_filepath, format="vasp")
    return mesh_from_atoms(structure, threshold)


def mesh_from_atoms(atoms, threshold=None):
    """由 ASE Atoms 计算 k 网格。"""
    if threshold is None:
        threshold = get_kpoint_density_threshold()
    lengths = atoms.cell.lengths()
    return tuple(calculate_kpoints_density(length, threshold) for length in lengths)


def apply_type_constraints(mesh, structure_type):
    """按体系类型约束网格: mole->1x1x1, slab->z=1, bulk->完整网格。"""
    if structure_type == "mole":
        return (1, 1, 1)
    if structure_type == "slab":
        return (mesh[0], mesh[1], 1)
    return mesh


# ---------------------------------------------------------------------------
# 体系类型判别 (真空层)
# ---------------------------------------------------------------------------

def largest_periodic_gap(fractional_coordinates):
    """一维分数坐标中最大的周期性空隙 (分数)。"""
    wrapped = sorted(c % 1.0 for c in fractional_coordinates)
    if not wrapped:
        return 1.0
    largest = wrapped[0] + 1.0 - wrapped[-1]
    for left, right in zip(wrapped, wrapped[1:]):
        largest = max(largest, right - left)
    return largest


def vacuum_gaps(atoms):
    """返回 a/b/c 三个方向的真空层厚度 (Å)。"""
    scaled = atoms.get_scaled_positions(wrap=True)
    lengths = atoms.cell.lengths()
    return {
        axis: largest_periodic_gap([p[idx] for p in scaled]) * float(lengths[idx])
        for idx, axis in enumerate("abc")
    }


def detect_structure_type(atoms, vacuum_threshold=None):
    """按真空层判别体系类型 (mole / slab / bulk / unknown)。"""
    if vacuum_threshold is None:
        vacuum_threshold = get_vacuum_threshold()
    gaps = vacuum_gaps(atoms)
    has = {a: gaps[a] > vacuum_threshold + _EPS for a in gaps}
    if has["a"] and has["b"] and has["c"]:
        return "mole", gaps
    if (not has["a"]) and (not has["b"]) and has["c"]:
        return "slab", gaps
    if (not has["a"]) and (not has["b"]) and (not has["c"]):
        return "bulk", gaps
    return "unknown", gaps


# ---------------------------------------------------------------------------
# 生成 / 写出
# ---------------------------------------------------------------------------

def format_kpoints(mesh):
    ka, kb, kc = mesh
    return f"Automatic mesh\n0\nGamma\n{ka}   {kb}   {kc}\n0   0   0"


def write_kpoints(output_path, content):
    Path(output_path).write_text(content, encoding="utf8")
    return content


# ---------------------------------------------------------------------------
# 能带: 高对称路径 KPOINTS (lines 模式)
#   slab/二维 -> 二维点阵 (kz=0), 无需 spglib;  bulk -> 三维路径 (需 spglib)
# ---------------------------------------------------------------------------

_GREEK = {
    "G": "Γ", "GAMMA": "Γ",
    "SIGMA": "Σ", "LAMBDA": "Λ", "DELTA": "Δ",
    "THETA": "Θ", "PI": "Π", "OMEGA": "Ω",
}


def display_label(label):
    """标签显示名 (seekpath 的 GAMMA 也映射为 Γ)。"""
    return _GREEK.get(label, label)


def _fmt_pt(v):
    return f"{float(v):.8f}"


def _primitive_atoms(atoms):
    try:
        import spglib
        from ase import Atoms

        lat, pos, nums = spglib.standardize_cell(
            (atoms.cell.array,
             atoms.get_scaled_positions(wrap=True),
             atoms.get_atomic_numbers()),
            to_primitive=True, symprec=1e-5)
        if lat is None:
            return atoms
        return Atoms(cell=lat, scaled_positions=pos, numbers=nums)
    except Exception:
        return atoms


def _segments_from_bandpath(bp):
    """把 ASE BandPath 拆成 (segments, labels)。

    用 ASE 官方 parse_path_string 切分, 正确处理 H1/X1/A1/L1 等带数字标签。
    """
    special = bp.special_points
    segments, labels = [], []
    for names in parse_path_string(bp.path):
        for a, b in zip(names, names[1:]):
            if a not in special or b not in special:
                raise ValueError(
                    f"高对称点标签 {a!r}/{b!r} 不在 special_points 中: {sorted(special)}")
            segments.append((special[a], special[b]))
            labels.append((a, b))
    if not segments:
        raise ValueError("未能从结构识别出高对称 K 路径, 请检查结构或手动替换 KPOINTS。")
    return segments, labels


def _axis_from_segments(segments, labels, cell):
    """由路径段端点手工计算高对称点横轴坐标 (累积倒空间距离, 含 2π, 单位 1/Å)。

    - 比 ASE 的 get_linear_kpoint_axis 更稳 (后者对超胞会算错)
    - 不连续处 (上一段终点 != 下一段起点) 保持同一 x, 便于画竖线
    返回 [(label, x), ...]。
    """
    recip = 2 * np.pi * np.asarray(cell.reciprocal(), dtype=float)

    def cart(kfrac):
        return np.asarray(kfrac, dtype=float) @ recip

    axis = []
    x = 0.0
    prev_end = None
    for (ka, kb), (la, lb) in zip(segments, labels):
        ka = np.asarray(ka, dtype=float)
        kb = np.asarray(kb, dtype=float)
        if prev_end is None or not np.allclose(ka, prev_end):
            axis.append((la, x))          # 不连续: 新段起点, x 不变
        x += float(np.linalg.norm(cart(kb) - cart(ka)))
        axis.append((lb, x))
        prev_end = kb
    return axis


def _seekpath_available():
    try:
        import seekpath  # noqa: F401
        return True
    except Exception:
        return False


def _path_ase(atoms, stype):
    """ASE 后端: slab 用二维点阵, bulk 用三维(优先 spglib 约化原胞)。"""
    if stype == "slab":
        pbc = [True, True, False]           # 真空沿 c, 二维点阵
        try:
            lat = atoms.cell.get_bravais_lattice(pbc=pbc)
            bp = atoms.cell.bandpath(pbc=pbc)
        except Exception as exc:
            raise ValueError(
                "无法获取表面二维高对称路径: " + str(exc)[:200]) from exc
    else:
        prim = _primitive_atoms(atoms)
        try:
            lat = prim.cell.get_bravais_lattice()
            bp = prim.cell.bandpath()
        except Exception as exc:
            raise ValueError(
                "无法自动获取高对称 K 路径 (需要 spglib 且晶体可识别): "
                + str(exc)[:200]) from exc
    segments, labels = _segments_from_bandpath(bp)
    return {"segments": segments, "labels": labels, "lattice": lat.name,
            "axis": _axis_from_segments(segments, labels, bp.cell),
            "backend": "ase", "warnings": []}


def _path_seekpath(atoms, reference_distance=0.02):
    """SeekPath 后端 (仅三维): 用 spglib 空间群 + HPKOT 标准路径。

    用 get_explicit_k_path_orig_cell, 保留输入胞的倒格基, 使 KPOINTS 与 POSCAR 匹配。
    """
    import numpy as np
    import seekpath

    structure = (atoms.cell.array,
                 atoms.get_scaled_positions(wrap=True),
                 atoms.get_atomic_numbers())
    res = seekpath.get_explicit_k_path_orig_cell(
        structure, reference_distance=reference_distance)
    rel = np.asarray(res["explicit_kpoints_rel"], dtype=float)
    labs = list(res["explicit_kpoints_labels"])

    segments, labels = [], []
    for start, stop in res["explicit_segments"]:
        segments.append((rel[start], rel[stop - 1]))
        labels.append((labs[start], labs[stop - 1]))
    axis = _axis_from_segments(segments, labels, atoms.cell)
    lat = res.get("bravais_lattice_extended") or res.get("bravais_lattice") or "?"

    warnings = []
    if res.get("is_supercell"):
        warnings.append("输入为超胞: 能带路径标签失去原义, 建议用原始(小)胞计算能带。")
    return {"segments": segments, "labels": labels, "lattice": lat,
            "axis": axis, "backend": "seekpath", "warnings": warnings}


def build_band_path(atoms, structure_type="auto", vacuum_threshold=None,
                    backend="auto"):
    """构建能带高对称路径。

    返回 dict: segments / labels / lattice / axis / backend / warnings。
    backend: auto | ase | seekpath。seekpath 仅用于 bulk, 二维/表面一律走 ASE。
    """
    if structure_type and structure_type != "auto":
        stype = structure_type
        detected = False
    else:
        stype, _ = detect_structure_type(atoms, vacuum_threshold)
        detected = True
    if stype == "mole":
        raise ValueError("分子体系无周期性, 无法生成能带路径, 请改用 mesh 模式。")

    warnings = []
    if stype == "slab":
        if backend == "seekpath":
            warnings.append("seekpath 不支持二维/表面, 已改用 ASE 二维路径。")
        result = _path_ase(atoms, "slab")
    else:
        seek_ok = _seekpath_available()
        want = backend == "seekpath" or (backend == "auto" and seek_ok)
        if want:
            try:
                result = _path_seekpath(atoms)
            except Exception as exc:
                warnings.append("seekpath 失败, 回退 ASE: " + str(exc)[:160])
                result = _path_ase(atoms, "bulk")
        else:
            if backend == "auto" and not seek_ok:
                warnings.append(
                    "未安装 seekpath, bulk 已回退 ASE; 建议 pip install seekpath。")
            result = _path_ase(atoms, "bulk")
    result["structure_type"] = stype
    result["detected"] = detected
    result["warnings"] = warnings + list(result.get("warnings") or [])
    return result


def band_path_segments(atoms, structure_type="auto", vacuum_threshold=None):
    """兼容旧接口: 返回 (segments, labels, lattice_name), 使用 ASE 后端。"""
    r = build_band_path(atoms, structure_type, vacuum_threshold, backend="ase")
    return r["segments"], r["labels"], r["lattice"]


def format_band_kpoints(segments, labels, per_seg=20):
    """VASP lines 模式 KPOINTS。

    格式 (见 VASP wiki "KPOINTS"): 第3行必须为 "line mode"(首字符 L/l),
    第4行才是坐标系 (Cartesian 或 fractional/Reciprocal)。
    """
    lines = ["k-points along high symmetry path",
             str(int(per_seg)),
             "line mode",
             "Reciprocal"]
    for (ka, kb), (la, lb) in zip(segments, labels):
        x1, y1, z1 = (float(v) for v in ka)
        x2, y2, z2 = (float(v) for v in kb)
        lines.append(f"{_fmt_pt(x1)} {_fmt_pt(y1)} {_fmt_pt(z1)}  ! {display_label(la)}")
        lines.append(f"{_fmt_pt(x2)} {_fmt_pt(y2)} {_fmt_pt(z2)}  ! {display_label(lb)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def band_path_summary(labels):
    names, seen = [], set()
    for a, b in labels:
        for lab in (a, b):
            if lab not in seen:
                seen.add(lab)
                names.append(display_label(lab))
    return "-".join(names)


def format_axis(axis, title=None):
    """把 [(label, x), ...] 格式化为文本表 (供能带图设置 xticks)。"""
    out = []
    if title:
        out.append("# " + title)
    out.append("# label         x(1/Angstrom)")
    for lab, x in axis:
        out.append(f"{display_label(lab):<13s} {x:12.6f}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 顶层入口
# ---------------------------------------------------------------------------

def generate_kpoints(
    poscar_filepath,
    structure_type="auto",
    output_path="KPOINTS",
    threshold=None,
    vacuum_threshold=None,
    mode="mesh",
    per_seg=20,
    backend="auto",
    write=True,
    verbose=True,
):
    """生成 KPOINTS, 返回 (content, info)。

    info = {"structure_type", "mesh", "detected", "gaps", "labels",
            "lattice", "backend", "axis", "warnings"}
    write=False 时不写文件; verbose=True 时向 stderr 打印摘要。
    """
    atoms = read(poscar_filepath, format="vasp")
    info = {"structure_type": structure_type, "mesh": None, "detected": False,
            "gaps": None, "labels": None, "lattice": None,
            "backend": None, "axis": None, "warnings": []}

    if mode == "lines":
        path = build_band_path(atoms, structure_type, vacuum_threshold, backend)
        content = format_band_kpoints(path["segments"], path["labels"], per_seg)
        info["structure_type"] = path.get("structure_type", structure_type)
        info["detected"] = bool(path.get("detected"))
        info["labels"] = band_path_summary(path["labels"])
        info["lattice"] = path["lattice"]
        info["backend"] = path["backend"]
        info["axis"] = path["axis"]
        info["warnings"] = path["warnings"]
    else:
        mesh = mesh_from_atoms(atoms, threshold)
        stype = structure_type
        if stype == "auto":
            stype, gaps = detect_structure_type(atoms, vacuum_threshold)
            info["gaps"] = gaps
            info["detected"] = True
            if stype == "unknown":
                stype = "bulk"
        info["structure_type"] = stype
        mesh = apply_type_constraints(mesh, stype)
        info["mesh"] = mesh
        content = format_kpoints(mesh)

    if write:
        write_kpoints(output_path, content)

    if verbose:
        _print_summary(poscar_filepath, output_path, content, info, mode,
                       threshold, write)
    return content, info


def create_vasp_kpoints(poscar_filepath, structure_type="bulk",
                        output_path="KPOINTS", threshold=None):
    """兼容旧接口: 生成并写出 KPOINTS, 返回文件内容 (字符串)。"""
    content, _ = generate_kpoints(
        poscar_filepath=poscar_filepath,
        structure_type=structure_type,
        output_path=output_path,
        threshold=threshold,
        write=True,
        verbose=False,
    )
    return content


def _print_summary(poscar_filepath, output_path, content, info, mode,
                   threshold, write):
    th = threshold if threshold is not None else get_kpoint_density_threshold()
    lines = [f"[kpoint] 输入     : {poscar_filepath}"]
    if mode == "lines":
        lines.append(
            f"[kpoint] 模式     : lines (后端 {info.get('backend')}, "
            f"点阵 {info.get('lattice') or '?'}, 高对称路径 {info['labels']})")
        for w in info.get("warnings") or []:
            lines.append(f"[kpoint] 警告     : {w}")
    else:
        tag = "自动判别" if info["detected"] else "用户指定"
        lines.append(f"[kpoint] 体系类型 : {info['structure_type']} ({tag})")
        ka, kb, kc = info["mesh"]
        lines.append(f"[kpoint] 网格     : {ka} x {kb} x {kc} (阈值 {th:.4f})")
        if info["gaps"]:
            g = info["gaps"]
            lines.append(
                f"[kpoint] 真空层   : a={g['a']:.2f} b={g['b']:.2f} c={g['c']:.2f} Å")
    lines.append(f"[kpoint] 输出     : {output_path}" if write else "[kpoint] 输出     : (仅打印)")
    print("\n".join(lines), file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kpoint",
        description="根据 POSCAR 生成 VASP KPOINTS (Gamma 中心网格 / 能带路径)。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python kpoint.py POSCAR\n"
            "  python kpoint.py POSCAR --type slab -o KPOINTS\n"
            "  python kpoint.py POSCAR --threshold 0.03 --print-only\n"
            "  python kpoint.py POSCAR --mode lines --per-seg 30\n"
        ),
    )
    parser.add_argument("poscar", nargs="?", help="POSCAR 文件路径 (位置参数)")
    parser.add_argument(
        "-i", "--poscar-filepath", "--poscar_filepath", dest="poscar_opt",
        metavar="FILE",
        help="POSCAR 文件路径 (与位置参数二选一, 兼容旧用法)",
    )
    parser.add_argument(
        "-t", "--type", dest="structure_type",
        choices=("auto", "mole", "slab", "bulk"), default="auto",
        help="体系类型约束 (默认 auto: 按真空层自动判别)",
    )
    parser.add_argument(
        "-d", "--threshold", type=float, default=DEFAULT_KPOINT_DENSITY,
        help=f"K 点密度阈值 (默认 {DEFAULT_KPOINT_DENSITY})",
    )
    parser.add_argument(
        "--vacuum-threshold", type=float, default=DEFAULT_VACUUM_THRESHOLD,
        help=f"自动判别体系类型的真空层阈值 Å (默认 {DEFAULT_VACUUM_THRESHOLD})",
    )
    parser.add_argument(
        "-o", "--output", default="KPOINTS",
        help="输出文件路径, '-' 表示只打印不写文件 (默认 KPOINTS)",
    )
    parser.add_argument(
        "-m", "--mode", choices=("mesh", "lines"), default="mesh",
        help="mesh=Gamma 网格 (默认); lines=高对称能带路径",
    )
    parser.add_argument(
        "--per-seg", type=int, default=20,
        help="lines 模式每段插点数 (默认 20)",
    )
    parser.add_argument(
        "--backend", choices=("auto", "ase", "seekpath"), default="auto",
        help="能带路径后端: auto=bulk 默认 seekpath(需安装), slab 用 ASE",
    )
    parser.add_argument(
        "--print-axis", action="store_true",
        help="打印高对称点横轴坐标 (1/Å), 供能带图设置 xticks",
    )
    parser.add_argument(
        "--axis-file", default=None, metavar="FILE",
        help="把高对称点横轴坐标写入文件",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="以 JSON 输出结果 (供 LLM/程序调用; 含 KPOINTS 正文与元数据)",
    )
    parser.add_argument(
        "--print-only", action="store_true",
        help="只打印结果, 不写文件",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="不打印摘要")
    parser.add_argument("--version", action="version", version="kpoint 1.0")
    return parser


def _json_payload(content, info, args, output, write):
    """构造供 LLM / 程序调用的结构化结果。"""
    return {
        "status": "ok",
        "mode": args.mode,
        "structure_type": info.get("structure_type"),
        "detected": bool(info.get("detected")),
        "out_file": output if write else None,
        "mesh": list(info["mesh"]) if info.get("mesh") else None,
        "lattice": info.get("lattice"),
        "backend": info.get("backend"),
        "labels": info.get("labels"),
        "axis": [{"label": display_label(l), "x": x} for l, x in (info.get("axis") or [])],
        "warnings": info.get("warnings") or [],
        "kpoints": content,
    }


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    def fail(msg, code=1):
        if args.json:
            print(json.dumps({"status": "error", "error": msg},
                             ensure_ascii=False, indent=2))
        else:
            print(f"[kpoint] 错误: {msg}", file=sys.stderr)
        return code

    poscar = args.poscar_opt or args.poscar
    if not poscar:
        return fail("缺少 POSCAR 文件路径 (位置参数或 -i/--poscar-filepath)")
    if not os.path.isfile(poscar):
        return fail(f"POSCAR 文件不存在: {poscar}")

    output = args.output
    write = not args.print_only and output != "-"

    try:
        content, info = generate_kpoints(
            poscar_filepath=poscar,
            structure_type=args.structure_type,
            output_path=output,
            threshold=args.threshold,
            vacuum_threshold=args.vacuum_threshold,
            mode=args.mode,
            per_seg=args.per_seg,
            backend=args.backend,
            write=write,
            verbose=not args.quiet and not args.json,
        )
    except (ValueError, OSError) as exc:
        return fail(str(exc))

    if args.mode == "lines" and (args.print_axis or args.axis_file):
        title = f"band x-axis  backend={info['backend']} lattice={info['lattice']}"
        table = format_axis(info["axis"] or [], title=title)
        if args.print_axis:
            print(table, file=sys.stderr)
        if args.axis_file:
            Path(args.axis_file).write_text(table + "\n", encoding="utf8")

    if args.json:
        print(json.dumps(_json_payload(content, info, args, output, write),
                         ensure_ascii=False, indent=2))
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
