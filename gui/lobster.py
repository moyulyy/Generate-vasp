# -*- coding: utf-8 -*-
"""lobster.in 生成：按 POTCAR 的价电子数（ZVAL）推断 basisfunctions。

做法：按构造原理得到中性原子的电子排布，从最外层（主量子数大者优先，同 n 时 l 大者优先）
向内逐壳层累加电子数，直到等于 ZVAL；被计入的壳层即 POTCAR 的价层，作为基函数。
这与 pymatgen 的 LOBSTER 标准基组一致（如 Ni → 4s 3d，Ni_pv → 3p 4s 3d，Fe_sv → 3s 3p 4s 3d）。
"""
from __future__ import annotations

import re

from ase.data import atomic_numbers

L_NAME = "spdf"
DEGENERACY = {"s": 1, "p": 3, "d": 5, "f": 7}
MADELUNG = ["1s", "2s", "2p", "3s", "3p", "4s", "3d", "4p", "5s", "4d", "5p", "6s",
            "4f", "5d", "6p", "7s", "5f", "6d", "7p"]
CAPACITY = {"s": 2, "p": 6, "d": 10, "f": 14}
LANTHANIDES = set("La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu".split())
ACTINIDES = set("Ac Th Pa U Np Pu Am Cm".split())
# 镧系 / 锕系中基态含 d 电子的元素：(n-1)f 少一个、(n)d 多一个
D_EXCEPTIONS = {"La": 1, "Ce": 1, "Gd": 1, "Ac": 1, "Th": 2, "Pa": 1, "U": 1, "Np": 1, "Cm": 1}


def configuration(el: str) -> dict[str, int]:
    """构造原理电子排布 {壳层: 电子数}，修正镧系 / 锕系的 d 电子。"""
    z = atomic_numbers[el]
    conf: dict[str, int] = {}
    for shell in MADELUNG:
        if z <= 0:
            break
        n = min(z, CAPACITY[shell[1]])
        conf[shell] = n
        z -= n
    if el in D_EXCEPTIONS:
        f, d = ("4f", "5d") if el in LANTHANIDES else ("5f", "6d")
        moved = min(D_EXCEPTIONS[el], conf.get(f, 0))
        conf[f] = conf.get(f, 0) - moved
        conf[d] = conf.get(d, 0) + moved
    return {k: v for k, v in conf.items() if v > 0}


def _outer_first(shell: str) -> tuple[int, int]:
    return -int(shell[0]), -L_NAME.index(shell[1])


def basis(el: str, label: str, zval: float, vrhfin: str = "") -> tuple[list[str], str]:
    """返回 (基函数壳层列表, 说明)。说明非空表示需要用户核对。"""
    conf = configuration(el)
    f_core = el in LANTHANIDES | ACTINIDES and re.search(r"_[23]$", label)
    if f_core:  # f 电子冻结在芯内的赝势（如 Gd_3、Eu_2）
        f = "4f" if el in LANTHANIDES else "5f"
        conf.pop(f, None)
        conf["5d" if el in LANTHANIDES else "6d"] = 1 if label.endswith("_3") else 0
    target = int(round(zval))
    shells, total = [], 0
    for shell in sorted(conf, key=_outer_first):
        if total >= target:
            break
        shells.append(shell)
        total += conf[shell]
    note = "" if total == target else f"按 ZVAL={zval:g} 无法恰好划分价层（累计 {total}），请核对"
    if el in LANTHANIDES | ACTINIDES:  # 赝势中总含 d 通道；f 价层赝势还含 f 通道
        d = "5d" if el in LANTHANIDES else "6d"
        f = "4f" if el in LANTHANIDES else "5f"
        shells += [s for s in ([d] if f_core else [d, f]) if s not in shells]
    elif re.search(r"p0", vrhfin):  # Mg、Be 等：赝势显式给出空 p 通道
        s_shell = next((s for s in shells if s[1] == "s" and s == max(shells, key=lambda x: int(x[0]))), None)
        if s_shell:
            p = f"{s_shell[0]}p"
            if p not in shells:
                shells.append(p)
    shells.sort(key=lambda s: (int(s[0]), L_NAME.index(s[1])))
    return shells, note


def n_functions(shells: list[str]) -> int:
    return sum(DEGENERACY[s[1]] for s in shells)


def parse_pairs(text: str, n_atoms: int) -> list[tuple[int, int]]:
    """'37-38, 12 15; 3-4' → [(37, 38), (12, 15), (3, 4)]（1 基编号）。"""
    pairs = []
    for chunk in re.split(r"[,，;；\n]+", text.strip()):
        if not chunk.strip():
            continue
        nums = re.findall(r"\d+", chunk)
        if len(nums) != 2 or re.search(r"[^\d\s\-–]", chunk):
            raise ValueError(f"无法识别原子对“{chunk.strip()}”，格式如 37-38, 12-15")
        a, b = int(nums[0]), int(nums[1])
        if not (1 <= a <= n_atoms and 1 <= b <= n_atoms):
            raise ValueError(f"原子编号应在 1–{n_atoms} 之间：{a}-{b}")
        if a == b:
            raise ValueError(f"原子对的两个原子不能相同：{a}-{b}")
        if (a, b) not in pairs:
            pairs.append((a, b))
    return pairs


def lobsterin(template: str, basis_lines: list[tuple[str, list[str]]], pairs: list[tuple[int, int]]) -> str:
    """以 ConditionsTemplates/lobster.incar 为模板，替换 basisfunctions 与 cohpbetween 行。"""
    out, placed_basis, placed_pairs = [], False, False
    for line in template.splitlines():
        key = line.strip().lower()
        if key.startswith("basisfunctions"):
            if not placed_basis:
                out += [f"basisfunctions {el:<3} {' '.join(shells)}" for el, shells in basis_lines]
                placed_basis = True
            continue
        if key.startswith("cohpbetween"):
            if not placed_pairs:
                out += [f"cohpbetween atom {a} and atom {b}  orbitalwise" for a, b in pairs]
                placed_pairs = True
            continue
        out.append(line)
    if not placed_basis:
        out += [f"basisfunctions {el:<3} {' '.join(shells)}" for el, shells in basis_lines]
    if not placed_pairs:
        out += [f"cohpbetween atom {a} and atom {b}  orbitalwise" for a, b in pairs]
    text = "\n".join(out).strip("\n")
    return re.sub(r"\n{3,}", "\n\n", text) + "\n"
