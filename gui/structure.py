# -*- coding: utf-8 -*-
"""结构读写（ASE）：CIF / POSCAR 输入，带 Selective dynamics 的 POSCAR 输出。

mask[i] = [fx, fy, fz]，True 表示该方向固定（POSCAR 中的 F）。
"""
from __future__ import annotations

import io
from itertools import product
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.constraints import FixAtoms, FixScaled
from ase.data import atomic_numbers, covalent_radii
from ase.data.colors import jmol_colors
from ase.io import read, write
from ase.neighborlist import neighbor_list

FILE_FILTER = "结构文件 (*.cif *.vasp *.poscar POSCAR* CONTCAR*);;所有文件 (*)"


def constraint_mask(atoms: Atoms) -> np.ndarray:
    mask = np.zeros((len(atoms), 3), dtype=bool)
    for c in atoms.constraints:
        if isinstance(c, FixAtoms):
            mask[c.index] = True
        elif isinstance(c, FixScaled):
            mask[c.index] |= c.mask
        else:
            raise ValueError(f"不支持的约束类型 {type(c).__name__}")
    return mask


def _checked(atoms: Atoms) -> tuple[Atoms, np.ndarray]:
    if not len(atoms) or not np.isfinite(atoms.positions).all():
        raise ValueError("结构为空或坐标无效")
    if atoms.cell.rank != 3 or abs(atoms.cell.volume) < 1e-6:
        raise ValueError("结构缺少完整的三维晶胞")
    mask = constraint_mask(atoms)
    atoms.set_constraint()
    atoms.pbc = True
    return atoms, mask


def load(path: str | Path) -> tuple[Atoms, np.ndarray, list[str]]:
    """返回 (atoms, mask, 提示)。同种元素不连续时按元素首次出现顺序稳定重排，保证 POTCAR 与 POSCAR 一一对应。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".cif":
        fmt = "cif"
    elif suffix in (".vasp", ".poscar") or not suffix or path.name.upper().startswith(("POSCAR", "CONTCAR")):
        fmt = "vasp"
    else:
        raise ValueError("仅支持 CIF 和 POSCAR / CONTCAR（.vasp）文件")
    atoms, mask = _checked(read(str(path), format=fmt, index=0))
    notes = []
    if fmt == "cif":
        notes.append("CIF 已转换为 POSCAR")
    symbols = atoms.get_chemical_symbols()
    first = {el: i for i, el in reversed(list(enumerate(symbols)))}
    blocks = [el for i, el in enumerate(symbols) if i == 0 or symbols[i - 1] != el]
    if len(blocks) != len(set(blocks)):
        order = np.argsort([first[el] for el in symbols], kind="stable")
        atoms, mask = atoms[order], mask[order]
        notes.append("同种元素不连续，已按元素重排原子顺序")
    return atoms, mask, notes


def parse_poscar(text: str) -> tuple[Atoms, np.ndarray]:
    """解析编辑器中的 POSCAR 文本；保持原子顺序（重复的元素块由 POTCAR 按块拼接）。"""
    try:
        atoms = read(io.StringIO(text), format="vasp")
    except Exception as exc:
        raise ValueError(f"POSCAR 无法解析：{exc}") from None
    return _checked(atoms)


def same(a: Atoms, b: Atoms) -> bool:
    return (len(a) == len(b) and a.get_chemical_symbols() == b.get_chemical_symbols()
            and np.allclose(a.cell.array, b.cell.array) and np.allclose(a.positions, b.positions))


def species(atoms: Atoms) -> list[tuple[str, int]]:
    out: list[list] = []
    for el in atoms.get_chemical_symbols():
        if out and out[-1][0] == el:
            out[-1][1] += 1
        else:
            out.append([el, 1])
    return [(el, n) for el, n in out]


def poscar_text(atoms: Atoms, mask: np.ndarray) -> str:
    """始终写 Selective dynamics 与逐原子 T/F（无固定原子时全为 T）。"""
    out = atoms.copy()
    full = mask.all(axis=1)
    cons = [FixAtoms(indices=np.flatnonzero(full))]
    cons += [FixScaled(int(i), mask=mask[i]) for i in np.flatnonzero(mask.any(axis=1) & ~full)]
    out.set_constraint(cons)
    buf = io.StringIO()
    write(buf, out, format="vasp", direct=True, sort=False, vasp5=True)
    return buf.getvalue()


def color(symbol: str) -> str:
    rgb = jmol_colors[atomic_numbers[symbol]]
    return "#" + "".join(f"{round(float(v) * 255):02x}" for v in rgb)


def viewer_payload(atoms: Atoms, boundary: bool = True, bonds: str = "half", tol: float = 1e-3) -> dict:
    """3Dmol 显示用的原子实例（原胞原子 + 周期像）与键。

    boundary: 显示落在晶胞面 / 棱 / 角上的等价原子（分数坐标 0 ↔ 1）。
    bonds:    跨周期边界的键 —— "off" 不画；"half" 对方不在视图中时画到中点的半键；
              "ghost" 另外显示与胞内原子成键的胞外原子（浅色）。
    每个实例带 src（胞内原子编号），点选 / 框选都作用到 src。
    """
    n = len(atoms)
    cell = atoms.cell.array
    pos = atoms.positions
    frac = atoms.cell.scaled_positions(pos)
    symbols = atoms.get_chemical_symbols()
    keys: dict[tuple, int] = {}
    inst: list[tuple[int, tuple, str]] = []

    def add(src, shift, kind):
        k = (src, *shift)
        if k not in keys:
            keys[k] = len(inst)
            inst.append((src, shift, kind))

    for i in range(n):
        add(i, (0, 0, 0), "atom")
    if boundary:
        for i in range(n):
            opts = [[0] + ([1] if abs(f) < tol else []) + ([-1] if abs(f - 1) < tol else []) for f in frac[i]]
            for sh in product(*opts):
                if any(sh):
                    add(i, sh, "image")

    neigh = [[] for _ in range(n)]
    for i, j, sh in zip(*neighbor_list("ijS", atoms, 1.15 * covalent_radii[atoms.numbers])):
        neigh[int(i)].append((int(j), tuple(int(v) for v in sh)))
    if bonds == "ghost":
        for i in range(n):
            for j, sh in neigh[i]:
                if any(sh):
                    add(j, sh, "ghost")

    xyz = [pos[src] + np.dot(sh, cell) for src, sh, _ in inst]
    bond_lists = [[] for _ in inst]
    half = []
    for idx, (src, sh, kind) in enumerate(inst):
        for j, s in neigh[src]:
            if any(s) and bonds == "off":
                continue
            target = (sh[0] + s[0], sh[1] + s[1], sh[2] + s[2])
            partner = keys.get((j, *target))
            if partner is not None:
                if kind != "ghost" or inst[partner][2] != "ghost":
                    bond_lists[idx].append(partner)
            elif kind != "ghost" and bonds != "off":
                end = (xyz[idx] + pos[j] + np.dot(target, cell)) / 2
                half.append([*map(float, xyz[idx]), *map(float, end), symbols[src]])

    records = [{"index": k, "serial": k, "src": src, "kind": kind, "elem": symbols[src],
                "x": float(p[0]), "y": float(p[1]), "z": float(p[2]),
                "bonds": bond_lists[k], "bondOrder": [1] * len(bond_lists[k])}
               for k, ((src, _, kind), p) in enumerate(zip(inst, xyz))]
    els = dict.fromkeys(symbols)
    return {"atoms": records, "half": half, "cell": cell.tolist(),
            "colors": {el: color(el) for el in els},
            "radii": {el: round(0.25 + 0.35 * float(covalent_radii[atomic_numbers[el]]), 3) for el in els}}


def layers(atoms: Atoms, axis: int, tol: float = 0.6) -> list[np.ndarray]:
    """沿晶格 axis 方向按高度分层（自底向上）；以最大周期空隙为起点，避免跨边界的层被拆开。"""
    frac = atoms.get_scaled_positions(wrap=True)[:, axis]
    spacing = 1.0 / np.linalg.norm(np.linalg.inv(atoms.cell.array)[:, axis])
    s = np.sort(frac)
    gaps = np.diff(np.append(s, s[0] + 1.0))
    start = s[(int(np.argmax(gaps)) + 1) % len(s)]
    height = ((frac - start) % 1.0) * spacing
    order = np.argsort(height)
    groups, current = [], [order[0]]
    for a, b in zip(order, order[1:]):
        if height[b] - height[a] > tol:
            groups.append(np.array(current))
            current = []
        current.append(b)
    groups.append(np.array(current))
    return groups


def parse_indices(text: str, n: int) -> list[int]:
    """'1-8, 12 16' → 0 基编号列表。"""
    ids: set[int] = set()
    for token in text.replace("，", ",").replace(",", " ").split():
        lo, _, hi = token.partition("-")
        if not lo.isdigit() or (hi and not hi.isdigit()):
            raise ValueError("请输入编号或范围，例如 1-8, 12")
        a, b = int(lo), int(hi or lo)
        if not 1 <= a <= b <= n:
            raise ValueError(f"编号应在 1–{n} 之间，且范围从小到大")
        ids.update(range(a - 1, b))
    return sorted(ids)
