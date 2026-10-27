# -*- coding: utf-8 -*-
"""Headless 版 VASP 作业文件生成器。

把 gui/incar_gui.py 中与界面无关的状态与逻辑集中到这里，供 VS Code 扩展的
Python 常驻服务调用。结构分析与生成逻辑完全复用 gui/jobgen.py、
gui/structure.py、gui/lobster.py 以及 script/ 下的脚本。
"""
from __future__ import annotations

import html
import re
import threading
import time
from pathlib import Path

import numpy as np

import jobgen as jg
import lobster as lb
import structure as st
from camera_views import standard_view

FILES = ["INCAR", "KPOINTS", "POSCAR", "POTCAR"]
EXTRA = ["lobsterin", "OPTCELL"]
ALL_FILES = FILES + EXTRA
MERGED = ["INCAR", "KPOINTS", *EXTRA]
SYSTEMS = [("auto", "自动判别"), ("mole", "分子"), ("slab", "表面"), ("bulk", "体相")]
SPIN_MODES = [("off", "关闭"), ("default", "启发式"), ("llm", "LLM 分析"), ("custom", "按元素指定")]
U_MODES = [("off", "关闭"), ("default", "默认 U 值"), ("custom", "自定义 L / U / J")]
BOND_MODES = [("off", "不显示"), ("half", "半键"), ("ghost", "胞外原子")]
DETECTED = {"bulk": "体相 bulk", "slab": "表面 slab", "mole": "分子 mole", "unknown": "未确定 · 按 slab 处理"}

RED = "#FF3B30"
AMBER = "#B26A00"
GREEN = "#34C759"
BLUE = "#007AFF"
TEXT_3 = "#8E8E93"


def _hint(text: str = "", color: str = "") -> dict:
    return {"text": text, "color": color}


def _as_bool(value, default=False) -> bool:
    return bool(value) if isinstance(value, (bool, int)) else default


def _as_float(value, default: float, lo: float | None = None, hi: float | None = None) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _as_int(value, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


class Controller:
    """与 PySide6 界面无关的完整业务状态机。"""

    def __init__(self, emit=None, potcar_dir: str = "", boundary: bool = True, bonds: str = "half"):
        self.emit = emit or (lambda event: None)
        self.lock = threading.RLock()

        self.atoms = None
        self.mask = np.zeros((0, 3), dtype=bool)
        self.spos = None
        self.detected = None
        self.source: Path | None = None
        self.generation = 0
        self.structure_revision = 0
        self._pending_view = None
        self._pending_keep_view = False
        self._pending_clear = False

        self.undo_stack: list[np.ndarray] = []
        self.redo_stack: list[np.ndarray] = []
        self._spin_plan = None
        self._kp_cache: dict = {}

        self.auto = {f: "" for f in ALL_FILES}
        self.manual = {f: False for f in ALL_FILES}
        self.base = {f: "" for f in ALL_FILES}
        self.text = {f: "" for f in ALL_FILES}
        self.errors = {f: [] for f in ALL_FILES}
        self.notes = {f: "" for f in ALL_FILES}
        self.hints = {k: _hint() for k in ("tpl", "sys", "spin", "u", "mix", "ngxf", "k", "kline",
                                           "pot", "clean", "sol", "lob", "opt", "load")}

        self.pot_infos: list[dict] = []
        self.potcar_full = ""
        self.pot_manual: list[str] | None = None
        self._variants: dict[str, list[str]] = {}
        self.edit_errors = {"POSCAR": [], "POTCAR": []}
        self.disk: dict[str, str] = {}
        self.load_notes: list[str] = []
        self._clean_before = False

        self._llm = None
        self._llm_running = False
        self._llm_run = 0
        self._llm_gen = -1
        self._llm_t0 = 0.0
        self._llm_msg = ""
        self._llm_note = ""

        self.options = self._default_options(potcar_dir, boundary, bonds)

    # ================================================================ 选项
    def _default_options(self, potcar_dir: str, boundary: bool, bonds: str) -> dict:
        mix = {tag: value for tag, value in jg.template_values("parameters-MIX.incar")}
        ngxf = {tag: value for tag, value in jg.template_values("parameters-NGXF.incar")}
        cp_mu = -4.6
        return {
            "calc": 0,
            "system": "auto",
            "spin": "off",
            "spinCustom": {},
            "u": "off",
            "uCustom": {},
            "mix": {"on": False, "values": mix},
            "ngxf": {"on": False, "values": ngxf},
            "kdens": 0.04,
            "perSeg": 90,
            "solvation": False,
            "efield": {"on": False, "value": -0.1, "idipol": 3},
            "cp": {"on": False, "mu": cp_mu, "u": round(jg.CP_REF - cp_mu, 3)},
            "parallel": {"on": False, "kpar": 1, "npar": 1},
            "lobster": {"on": False, "pairs": ""},
            "optcell": {"on": False, "grid": jg.optcell_default()},
            "potcarDir": potcar_dir.strip() if potcar_dir else jg.default_potcar_dir(),
            "potcarLabels": {},
            "clean": False,
            "boundary": bool(boundary),
            "bonds": bonds if bonds in ("off", "half", "ghost") else "half",
            "include": {f: True for f in ALL_FILES},
        }

    def apply_options(self, incoming) -> None:
        if not isinstance(incoming, dict):
            raise ValueError("options 必须是对象")
        opt = self.options
        old_dir = opt["potcarDir"]
        old_boundary, old_bonds = opt["boundary"], opt["bonds"]
        old_lobster = opt["lobster"]["on"]

        if "calc" in incoming:
            opt["calc"] = _as_int(incoming["calc"], opt["calc"], 0, len(jg.CALCS) - 1)
        if "system" in incoming and incoming["system"] in {k for k, _ in SYSTEMS}:
            opt["system"] = incoming["system"]
        if "spin" in incoming and incoming["spin"] in {k for k, _ in SPIN_MODES}:
            opt["spin"] = incoming["spin"]
        if "u" in incoming and incoming["u"] in {k for k, _ in U_MODES}:
            opt["u"] = incoming["u"]

        if isinstance(incoming.get("spinCustom"), dict):
            for el, value in incoming["spinCustom"].items():
                opt["spinCustom"][str(el)] = _as_float(value, jg.spin.default_element_moment(str(el)),
                                                       -10.0, 10.0)
        if isinstance(incoming.get("uCustom"), dict):
            for el, value in incoming["uCustom"].items():
                if not isinstance(value, dict):
                    continue
                current = opt["uCustom"].get(str(el), self._u_default(str(el)))
                current = dict(current)
                current["orb"] = _as_int(value.get("orb", current["orb"]), current["orb"], -1, 3)
                current["u"] = _as_float(value.get("u", current["u"]), current["u"], -10.0, 20.0)
                current["j"] = _as_float(value.get("j", current["j"]), current["j"], 0.0, 10.0)
                opt["uCustom"][str(el)] = current

        for key in ("mix", "ngxf"):
            if isinstance(incoming.get(key), dict):
                rec = incoming[key]
                opt[key]["on"] = _as_bool(rec.get("on", opt[key]["on"]), opt[key]["on"])
                if isinstance(rec.get("values"), dict):
                    for tag, value in rec["values"].items():
                        if tag in opt[key]["values"]:
                            opt[key]["values"][tag] = str(value)

        if "kdens" in incoming:
            opt["kdens"] = _as_float(incoming["kdens"], opt["kdens"], 0.005, 0.5)
        if "perSeg" in incoming:
            opt["perSeg"] = _as_int(incoming["perSeg"], opt["perSeg"], 5, 200)
        if "solvation" in incoming:
            opt["solvation"] = _as_bool(incoming["solvation"], opt["solvation"])

        if isinstance(incoming.get("efield"), dict):
            rec = incoming["efield"]
            opt["efield"]["on"] = _as_bool(rec.get("on", opt["efield"]["on"]), opt["efield"]["on"])
            opt["efield"]["value"] = _as_float(rec.get("value", opt["efield"]["value"]),
                                               opt["efield"]["value"], -1.0, 1.0)
            opt["efield"]["idipol"] = _as_int(rec.get("idipol", opt["efield"]["idipol"]),
                                              opt["efield"]["idipol"], 1, 3)
        if isinstance(incoming.get("cp"), dict):
            rec = incoming["cp"]
            opt["cp"]["on"] = _as_bool(rec.get("on", opt["cp"]["on"]), opt["cp"]["on"])
            opt["cp"]["mu"] = _as_float(rec.get("mu", opt["cp"]["mu"]), opt["cp"]["mu"], -10.0, 0.0)
            opt["cp"]["u"] = _as_float(rec.get("u", opt["cp"]["u"]), opt["cp"]["u"], -5.0, 5.0)
        if isinstance(incoming.get("parallel"), dict):
            rec = incoming["parallel"]
            opt["parallel"]["on"] = _as_bool(rec.get("on", opt["parallel"]["on"]), opt["parallel"]["on"])
            opt["parallel"]["kpar"] = _as_int(rec.get("kpar", opt["parallel"]["kpar"]),
                                              opt["parallel"]["kpar"], 1, 512)
            opt["parallel"]["npar"] = _as_int(rec.get("npar", opt["parallel"]["npar"]),
                                              opt["parallel"]["npar"], 1, 512)
        if isinstance(incoming.get("lobster"), dict):
            rec = incoming["lobster"]
            opt["lobster"]["on"] = _as_bool(rec.get("on", opt["lobster"]["on"]), opt["lobster"]["on"])
            if "pairs" in rec:
                opt["lobster"]["pairs"] = str(rec.get("pairs") or "")
        if isinstance(incoming.get("optcell"), dict):
            rec = incoming["optcell"]
            opt["optcell"]["on"] = _as_bool(rec.get("on", opt["optcell"]["on"]), opt["optcell"]["on"])
            grid = rec.get("grid")
            if isinstance(grid, list) and len(grid) == 3 and all(isinstance(r, list) and len(r) == 3 for r in grid):
                opt["optcell"]["grid"] = [[1 if _as_int(v, 0) else 0 for v in row] for row in grid]

        if "potcarDir" in incoming:
            opt["potcarDir"] = str(incoming["potcarDir"] or "").strip()
        if isinstance(incoming.get("potcarLabels"), dict):
            for el, label in incoming["potcarLabels"].items():
                opt["potcarLabels"][str(el)] = str(label)
        if "clean" in incoming:
            opt["clean"] = _as_bool(incoming["clean"], opt["clean"])
        if "boundary" in incoming:
            opt["boundary"] = _as_bool(incoming["boundary"], opt["boundary"])
        if "bonds" in incoming and incoming["bonds"] in ("off", "half", "ghost"):
            opt["bonds"] = incoming["bonds"]
        if isinstance(incoming.get("include"), dict):
            for f, value in incoming["include"].items():
                if f in ALL_FILES:
                    opt["include"][f] = _as_bool(value, True)

        # LOBSTER 读取不了含 SHA256 / COPYR 的 POTCAR：开启时强制清理，关闭时恢复之前的选择
        if opt["lobster"]["on"] and not old_lobster:
            self._clean_before = opt["clean"]
        if opt["lobster"]["on"]:
            opt["clean"] = True
        elif old_lobster and not opt["lobster"]["on"]:
            opt["clean"] = self._clean_before

        if opt["potcarDir"] != old_dir and self.atoms is not None:
            self._rebuild_potcar()
        if opt["boundary"] != old_boundary or opt["bonds"] != old_bonds:
            self.structure_revision += 1
            self._pending_keep_view = True
        self.refresh()

    def set_potcar_label(self, el: str, label: str) -> None:
        if self.atoms is None:
            return
        self.options["potcarLabels"][str(el)] = str(label)
        self.pot_manual = None
        self.manual["POTCAR"] = False
        self.refresh()

    # ================================================================ 结构
    def _elements(self) -> list[str]:
        if self.atoms is None:
            return []
        return list(dict.fromkeys(el for el, _ in st.species(self.atoms)))

    def _u_default(self, el: str) -> dict:
        if el in jg.dftu.DEFAULT_U:
            _, l_val, u_val, j_val = jg.dftu.DEFAULT_U[el]
        elif el in jg.dftu.U:
            l_val, u_val, j_val = -1, jg.dftu.U[el][0][1], jg.dftu.U[el][0][2]
        else:
            l_val, u_val, j_val = -1, 0.0, 0.0
        return {"orb": int(l_val), "u": float(u_val), "j": float(j_val)}

    def _rebuild_potcar(self) -> None:
        elements = self._elements()
        self._variants = {el: jg.potcar_variants(self.options["potcarDir"], el) for el in elements}
        self.options["potcarLabels"] = {
            el: (self._variants[el][0] if self._variants[el] else jg.default_label(el))
            for el in elements
        }
        self.pot_manual = None
        self.manual["POTCAR"] = False

    def _rebuild_editors(self) -> None:
        elements = self._elements()
        if not elements:
            self.options["spinCustom"] = {}
            self.options["uCustom"] = {}
            self.options["potcarLabels"] = {}
            self._variants = {}
            self.pot_manual = None
            return
        self.options["spinCustom"] = {el: jg.spin.default_element_moment(el) for el in elements}
        self.options["uCustom"] = {el: self._u_default(el) for el in elements}
        self._rebuild_potcar()

    def _set_structure(self, atoms, mask) -> None:
        spos = jg.spin.parse_poscar_text(st.poscar_text(atoms, mask), "POSCAR")
        detected = jg.spin.detect_system_type(spos)
        self.atoms, self.mask, self.spos, self.detected = atoms, mask, spos, detected
        self.generation += 1
        self.structure_revision += 1
        self.cancel_llm("结构已改变，已取消分析，请重新分析", refresh=False)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._spin_plan = None
        self._kp_cache.clear()
        self._pending_view = None
        self._pending_keep_view = False

    def load_structure(self, path: str) -> dict:
        p = Path(path)
        if not p.is_file():
            raise ValueError(f"找不到结构文件：{p}")
        atoms, mask, notes = st.load(p)
        self._set_structure(atoms, mask)
        self.source = p.resolve()
        self.load_notes = list(notes)
        self.hints["load"] = _hint(" · ".join(notes))
        self.options["lobster"]["pairs"] = ""
        self.manual = {f: False for f in ALL_FILES}
        self.edit_errors = {"POSCAR": [], "POTCAR": []}
        self.pot_manual = None
        self.disk.clear()
        self._rebuild_editors()
        if self.detected and self.detected[0] == "slab":
            self.options["efield"]["idipol"] = self.vac_axis() + 1
        self.show_structure()
        self.refresh()
        return self.state(known_revision=None)

    def clear_structure(self) -> dict:
        self.atoms = None
        self.spos = None
        self.detected = None
        self.source = None
        self.mask = np.zeros((0, 3), dtype=bool)
        self.generation += 1
        self.structure_revision += 1
        self._pending_clear = True
        self._pending_view = None
        self._pending_keep_view = False
        self.load_notes = []
        self.hints["load"] = _hint()
        self.cancel_llm("已移除结构", refresh=False)
        self.edit_errors = {"POSCAR": [], "POTCAR": []}
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.manual = {f: False for f in ALL_FILES}
        self.pot_manual = None
        self.disk.clear()
        self._rebuild_editors()
        self.refresh()
        return self.state(known_revision=None)

    def show_structure(self, keep_view: bool = False) -> None:
        view = None
        if (not keep_view and self.detected and self.detected[0] == "slab"
                and self.vac_axis() == 2):
            try:
                view = standard_view(self.atoms.cell.array, "front")["quaternion"]
            except Exception:
                view = None
        self._pending_view = view
        self._pending_keep_view = keep_view
        self.structure_revision += 1

    def vac_axis(self) -> int:
        return int(np.argmax([self.detected[1][a] for a in "abc"])) if self.detected else 2

    def layer_axis(self) -> int:
        return self.vac_axis() if self.system_type() == "slab" else 2

    def system_type(self) -> str:
        choice = self.options["system"]
        if choice != "auto":
            return choice
        if self.detected is None:
            return "bulk"
        return "slab" if self.detected[0] == "unknown" else self.detected[0]

    def calc_tpl(self) -> tuple[str, str]:
        key = jg.CALCS[self.options["calc"]][2]
        return (key or "relax"), (key or f"relax-{self.system_type()}")

    def optcell_allowed(self) -> bool:
        return self.calc_tpl()[0] == "relax" and self.system_type() == "bulk"

    def active_files(self) -> list[str]:
        extra = {"lobsterin": self.options["lobster"]["on"],
                 "OPTCELL": self.options["optcell"]["on"] and self.optcell_allowed()}
        return FILES + [f for f in EXTRA if extra[f]]

    def selected_files(self) -> list[str]:
        return [f for f in self.active_files() if self.options["include"].get(f, True)]

    def set_view(self, name: str) -> dict:
        if self.atoms is None:
            raise ValueError("请先加载结构")
        view = standard_view(self.atoms.cell.array, name)
        view["generation"] = self.generation
        return view

    # ================================================================ 固定原子
    def edit_mask(self, indices, action: str) -> None:
        if self.atoms is None or not indices:
            return
        ids = sorted({int(i) for i in indices if 0 <= int(i) < len(self.atoms)})
        if not ids:
            return
        new = self.mask.copy()
        if action == "toggle":
            new[ids] = (~new[ids].all(axis=1))[:, None]
        else:
            new[ids] = action == "fix"
        self._push_mask(new)

    def _push_mask(self, new) -> None:
        if np.array_equal(new, self.mask):
            return
        self.undo_stack = (self.undo_stack + [self.mask.copy()])[-100:]
        self.redo_stack.clear()
        self._set_mask(new)

    def _set_mask(self, mask) -> None:
        self.mask = mask.copy()
        self.manual["POSCAR"] = False
        self.refresh()

    def edit_all(self, action: str) -> None:
        if self.atoms is not None:
            self.edit_mask(list(range(len(self.atoms))), action)

    def edit_typed(self, text: str, action: str) -> str:
        if self.atoms is None:
            raise ValueError("请先加载结构")
        ids = st.parse_indices(text, len(self.atoms))
        self.edit_mask(ids, action)
        return f"已{'固定' if action == 'fix' else '弛豫'} {len(ids)} 个原子"

    def fix_bottom_layers(self, count: int) -> str:
        if self.atoms is None:
            raise ValueError("请先加载结构")
        groups = st.layers(self.atoms, self.layer_axis())
        n = min(_as_int(count, 1, 1, 99), len(groups))
        bottom = np.concatenate(groups[:n]).tolist()
        new = np.zeros_like(self.mask)
        new[bottom] = True
        self._push_mask(new)
        return f"已固定底部 {n} 层（{len(bottom)} 个原子），其余原子弛豫"

    def undo(self) -> None:
        if self.undo_stack:
            self.redo_stack.append(self.mask.copy())
            self._set_mask(self.undo_stack.pop())

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(self.mask.copy())
            self._set_mask(self.redo_stack.pop())

    # ================================================================ 自动生成
    def _default_moments(self):
        if self._spin_plan is None:
            try:
                self._spin_plan = jg.spin.heuristic_plan(self.spos)
            except Exception as exc:
                self._spin_plan = exc
        if isinstance(self._spin_plan, Exception):
            raise self._spin_plan
        return self._spin_plan

    def _spin_moments(self, mode: str, errors: list):
        s = self.spos
        if mode == "default":
            moments, reason = self._default_moments()
            self.hints["spin"] = _hint("内置启发式（未调用 LLM）：" + reason.replace("[回退] ", "", 1))
            return moments, None
        if mode == "custom":
            self.hints["spin"] = _hint("按 POSCAR 原子顺序展开为 MAGMOM，同一元素取相同初值")
            return [self.options["spinCustom"].get(el, jg.spin.default_element_moment(el))
                    for el in s.symbols], None
        r = self._llm if self._llm and self._llm["gen"] == self.generation else None
        if self._llm_running:
            errors.append("自旋：LLM 分析进行中")
            self.hints["spin"] = _hint("LLM 分析进行中，完成后自动写入 MAGMOM")
            return None, None
        if r is None:
            errors.append("自旋：请先点击“开始分析”运行 LLM")
            self.hints["spin"] = _hint("LLM 结合结构分析、元素与材料知识库给出磁矩方案，经校验后写入 MAGMOM")
            return None, None
        if "error" in r:
            errors.append("自旋：LLM 调用失败")
            self.hints["spin"] = _hint("LLM 调用失败，详见下方；可重试，或改用启发式 / 按元素指定", RED)
            return None, None
        if r["warnings"]:
            self.hints["spin"] = _hint("已按 LLM 方案写入 MAGMOM，有告警，详见下方", AMBER)
        else:
            self.hints["spin"] = _hint("已按 LLM 方案写入 MAGMOM，依据详见下方")
        return r["moments"], r["ispin"]

    def build_incar(self, tpl: str, errors: list) -> str:
        text = jg.read_tpl(jg.INCAR_TPL / f"{tpl}.incar")
        note = jg.NOTES.get(tpl, "")
        self.hints["tpl"] = _hint(f"模板 {tpl}.incar  ·  {jg.tpl_summary(text)}" + (f"\n{note}" if note else ""))
        s = self.spos

        self.hints["spin"] = _hint()
        mode = self.options["spin"]
        if mode != "off":
            if s is None:
                errors.append("自旋参数需要先加载结构")
            else:
                try:
                    moments, ispin = self._spin_moments(mode, errors)
                    if moments is not None:
                        text = jg.merge(text, jg.spin.format_magmom(s, moments, ispin=ispin))
                except Exception as exc:
                    errors.append(f"自旋：{exc}")
                    self.hints["spin"] = _hint(f"{exc}；可改用 LLM 分析或“按元素指定”", RED)

        self.hints["u"] = _hint()
        mode = self.options["u"]
        if mode != "off":
            if s is None:
                errors.append("DFT+U 需要先加载结构")
            else:
                custom = None
                if mode == "custom":
                    custom = {}
                    for el in dict.fromkeys(s.species):
                        rec = self.options["uCustom"].get(el) or self._u_default(el)
                        custom[el] = (int(rec.get("orb", -1)), float(rec.get("u", 0.0)),
                                      float(rec.get("j", 0.0)))
                else:
                    applied = [f"{el}（{jg.dftu.DEFAULT_U[el][0]}, U={jg.dftu.DEFAULT_U[el][2]:g}）"
                               for el in dict.fromkeys(s.species) if el in jg.dftu.DEFAULT_U]
                    if applied:
                        self.hints["u"] = _hint("加 U：" + "、".join(applied))
                block = jg.dftu.build_dftu(list(zip(s.species, s.counts)), custom=custom)
                if block:
                    text = jg.merge(text, block)
                else:
                    self.hints["u"] = _hint("体系中没有需要加 U 的元素，未写入 DFT+U 参数")

        for key, hint_key, tpl_name, head, kind in (
                ("mix", "mix", "parameters-MIX.incar", "## Mixing", float),
                ("ngxf", "ngxf", "parameters-NGXF.incar", "## FFT grid", int)):
            self.hints[hint_key] = _hint()
            if not self.options[key]["on"]:
                continue
            values = self.options[key]["values"]
            bad = []
            for tag, raw in values.items():
                raw = str(raw).strip()
                try:
                    num = float(raw) if kind is float else int(raw)
                    if num < 0 or (kind is int and num == 0):
                        raise ValueError
                except ValueError:
                    bad.append(tag)
            if bad:
                errors.append(f"{'、'.join(bad)} 数值无效")
                self.hints[hint_key] = _hint(
                    f"{'、'.join(bad)} 需要{'正整数' if kind is int else '非负数'}", RED)
                continue
            text = jg.merge(text, head + "\n" + jg.apply_values(tpl_name, values))
            if key == "mix" and self.options["spin"] == "off":
                self.hints[hint_key] = _hint("AMIX_MAG / BMIX_MAG 只在自旋极化（ISPIN=2）时起作用")
            elif key == "ngxf":
                self.hints[hint_key] = _hint(
                    "细网格 FFT 点数，通常取 NGX/NGY/NGZ 的 2 倍；可参照同体系 OUTCAR 中的 NGXF 一行")

        cp_on = self.options["cp"]["on"]
        self.hints["sol"] = _hint("CP-VASP 模板已包含溶剂化参数" if cp_on else "")
        return jg.conditions(
            text,
            solvation=self.options["solvation"],
            efield_value=self.options["efield"]["value"] if self.options["efield"]["on"] else None,
            idipol=self.options["efield"]["idipol"],
            cp_mu=self.options["cp"]["mu"] if cp_on else None,
            kpar_npar=(self.options["parallel"]["kpar"], self.options["parallel"]["npar"])
            if self.options["parallel"]["on"] else None,
        )

    def build_kpoints(self, key: str, errors: list) -> str:
        band = key == "band-band"
        self.hints["k"] = _hint()
        self.hints["kline"] = _hint()
        hint_key = "kline" if band else "k"
        if self.atoms is None:
            self.hints[hint_key] = _hint("加载结构后计算")
            return ""
        args = (key, self.system_type(), self.vac_axis(),
                self.options["kdens"], self.options["perSeg"])
        cache_key = (self.generation,) + (args if band else ("mesh",) + args[1:4])
        if cache_key not in self._kp_cache:
            try:
                self._kp_cache[cache_key] = jg.kpoints(self.atoms, *args)
            except Exception as exc:
                self._kp_cache[cache_key] = exc
        result = self._kp_cache[cache_key]
        if isinstance(result, Exception):
            errors.append(f"KPOINTS：{result}")
            self.hints[hint_key] = _hint(str(result), RED)
            return ""
        text, summary, warnings = result
        self.hints[hint_key] = _hint("；".join([summary] + warnings), RED if warnings else "")
        return text

    def pot_labels(self) -> list[str]:
        if self.pot_manual is not None:
            return self.pot_manual
        return [self.options["potcarLabels"].get(el) or jg.default_label(el)
                for el, _ in st.species(self.atoms)]

    def build_potcar(self, incar: str, errors: list) -> str:
        self.hints["pot"] = _hint()
        self.potcar_full = ""
        self.pot_infos = []
        if self.atoms is None:
            return ""
        species, labels = st.species(self.atoms), self.pot_labels()
        try:
            self.potcar_full, infos = jg.potcar(
                species, labels, self.options["potcarDir"], self.options["clean"])
            self.pot_infos = infos
        except Exception as exc:
            errors.append(f"POTCAR：{exc}")
            self.hints["pot"] = _hint(str(exc), RED)
            return "\n".join(["# 无法生成 POTCAR：" + str(exc), ""] + labels) + "\n"
        enmax = max(i["enmax"] for i in infos)
        encut = jg.tag_value(incar, "ENCUT")
        msg = f"最大 ENMAX = {enmax:.1f} eV · INCAR ENCUT = {encut}"
        try:
            warn = encut is not None and float(encut) < 1.3 * enmax
        except ValueError:
            warn = False
        if warn:
            msg += f"（建议 ≥ 1.3 × ENMAX ≈ {1.3 * enmax:.0f} eV）"
        self.hints["pot"] = _hint(msg, AMBER if warn else "")
        return jg.potcar_composition(species, labels, infos, self.options["potcarDir"],
                                     f"{msg} · 完整 POTCAR {len(self.potcar_full.splitlines())} 行")

    def build_optcell(self, incar: str, errors: list) -> str:
        allowed = self.optcell_allowed()
        if not allowed:
            if self.options["optcell"]["on"]:
                self.options["optcell"]["on"] = False
            self.hints["opt"] = _hint(
                f"仅在体相弛豫时可用（当前：{jg.CALCS[self.options['calc']][1]} · "
                f"{DETECTED.get(self.system_type(), self.system_type())}）")
            return ""
        grid = [[int(v) for v in row] for row in self.options["optcell"]["grid"]]
        isif = jg.tag_value(incar, "ISIF")
        warn = [] if isif == "3" else [f"OPTCELL 需要 ISIF = 3（当前 {isif}）"]
        if not any(map(any, grid)):
            warn.append("全部为 0 时晶格不变，相当于 ISIF = 2")
        self.hints["opt"] = _hint("；".join(warn) if warn else "三行依次对应 a、b、c 矢量的 x、y、z 分量",
                                  AMBER if warn else "")
        if not self.options["optcell"]["on"]:
            return ""
        self.notes["OPTCELL"] = "三行依次对应 a、b、c 矢量的 x y z 分量：1 可变 / 0 固定"
        return jg.optcell_text(grid)

    def build_lobster(self, incar: str, errors: list) -> str:
        self.hints["lob"] = _hint()
        if self.atoms is None or not self.pot_infos:
            errors.append("lobsterin 需要先成功生成 POTCAR")
            return ""
        basis_lines, notes, seen = [], [], {}
        for (el, _), info in zip(st.species(self.atoms), self.pot_infos):
            if el in seen:
                if seen[el] != info["label"]:
                    notes.append(f"{el} 出现在多个元素块且赝势不同，basisfunctions 以 {seen[el]} 为准")
                continue
            seen[el] = info["label"]
            shells, note = lb.basis(el, info["label"], info["zval"], info["vrhfin"])
            basis_lines.append((el, shells))
            if note:
                notes.append(f"{el}：{note}")
        try:
            pairs = lb.parse_pairs(self.options["lobster"]["pairs"], len(self.atoms))
        except ValueError as exc:
            errors.append(f"lobsterin：{exc}")
            self.hints["lob"] = _hint(str(exc), RED)
            return lb.lobsterin(jg.read_tpl(jg.COND_TPL / "lobster.incar"), basis_lines, [])
        if not pairs:
            errors.append("lobsterin：请指定至少一组 cohpbetween 原子对")
        symbols = self.atoms.get_chemical_symbols()
        far, shown = [], []
        for a, b in pairs:
            d = self.atoms.get_distance(a - 1, b - 1, mic=True)
            shown.append(f"{a}–{b} {symbols[a - 1]}–{symbols[b - 1]} {d:.2f} Å")
            if d > 4.0:
                far.append(f"{a}–{b}")
        per_atom = {el: lb.n_functions(sh) for el, sh in basis_lines}
        n_basis = sum(per_atom[el] for el in symbols)
        msg = ["basisfunctions 由 POTCAR 价层推断：" + "，".join(f"{el} {' '.join(sh)}" for el, sh in basis_lines)]
        if shown:
            msg.append("原子对：" + "；".join(shown))
        warn = list(notes)
        if far:
            warn.append(f"{'、'.join(far)} 距离超过 4 Å（按最小像），请确认")
        lwave, isym = jg.tag_value(incar, "LWAVE"), jg.tag_value(incar, "ISYM")
        nbands = jg.tag_value(incar, "NBANDS")
        if not (lwave and lwave.strip(".").upper().startswith("T")):
            warn.append("INCAR 需 LWAVE = .TRUE.")
        if isym not in ("0", "-1"):
            warn.append("INCAR 需 ISYM = 0 或 -1")
        try:
            if nbands is None or int(nbands) < n_basis:
                warn.append(f"NBANDS 应 ≥ 基函数总数 {n_basis}（当前 {nbands or '未设置'}）")
        except ValueError:
            pass
        if self.calc_tpl()[0] != "cohp" and warn:
            warn.append("建议配合 09 COHP 项目使用")
        self.hints["lob"] = _hint("\n".join(msg + (["⚠ " + "；".join(warn)] if warn else [])),
                                  AMBER if warn else "")
        self.notes["lobsterin"] = f"基函数 {n_basis} 个 · POTCAR 已自动去除 SHA256 / COPYR 行"
        return lb.lobsterin(jg.read_tpl(jg.COND_TPL / "lobster.incar"), basis_lines, pairs)

    def _sync_editor(self, f: str) -> None:
        auto = self.auto[f]
        if not self.manual[f]:
            self.text[f] = auto
        elif not auto:
            return
        elif auto != self.base[f]:
            merged = jg.rebase(self.text[f], self.base[f], auto) if self.base[f] else self.text[f]
            self.text[f] = merged
            self.manual[f] = merged != auto
        self.base[f] = auto

    def refresh(self) -> None:
        key, tpl = self.calc_tpl()
        errors = {f: [] for f in ALL_FILES}
        self.auto["INCAR"] = self.build_incar(tpl, errors["INCAR"])
        self.auto["KPOINTS"] = self.build_kpoints(key, errors["KPOINTS"])
        self.auto["POSCAR"] = st.poscar_text(self.atoms, self.mask) if self.atoms is not None else ""
        self._sync_editor("INCAR")
        incar_now = self.text["INCAR"]
        self.auto["POTCAR"] = self.build_potcar(incar_now, errors["POTCAR"])
        self.auto["OPTCELL"] = self.build_optcell(incar_now, errors["OPTCELL"])
        self.auto["lobsterin"] = (self.build_lobster(incar_now, errors["lobsterin"])
                                  if self.options["lobster"]["on"] else "")
        self.hints["clean"] = _hint(
            "LOBSTER 无法读取含这些行的 POTCAR，已自动开启" if self.options["lobster"]["on"] else "")
        active = self.active_files()
        for f in EXTRA:
            if f not in active:
                errors[f] = []
        for f in MERGED:
            if f != "INCAR" and (f in FILES or f in active):
                self._sync_editor(f)
            if self.manual[f] and not self.auto[f]:
                errors[f] = []
        if self.manual["lobsterin"] and re.search(r"^\s*cohpbetween\b", self.text["lobsterin"], re.M | re.I):
            errors["lobsterin"] = [e for e in errors["lobsterin"] if "cohpbetween" not in e]
        if self.manual["POSCAR"]:
            errors["POSCAR"] = list(self.edit_errors["POSCAR"])
        if self.manual["POTCAR"]:
            errors["POTCAR"] = list(self.edit_errors["POTCAR"]) or errors["POTCAR"]
        self.errors = errors
        for f in ("POSCAR", "POTCAR"):
            if not self.manual[f]:
                self.text[f] = self.auto[f]

    # ================================================================ 文件编辑
    def content(self, f: str) -> str:
        return self.potcar_full if f == "POTCAR" else self.text[f]

    def edit_file(self, f: str, text: str) -> None:
        if f not in ALL_FILES:
            raise ValueError(f"未知文件：{f}")
        self.text[f] = str(text)
        self.manual[f] = self.text[f] != self.auto[f]
        if f == "POSCAR":
            self._apply_poscar_edit()
        elif f == "POTCAR":
            self._apply_potcar_edit()
        elif f == "INCAR":
            self.refresh()

    def _apply_poscar_edit(self) -> None:
        self.edit_errors["POSCAR"] = []
        if not self.manual["POSCAR"]:
            self.refresh()
            return
        try:
            atoms, mask = st.parse_poscar(self.text["POSCAR"])
        except ValueError as exc:
            self.edit_errors["POSCAR"] = [str(exc)]
            self.refresh()
            return
        if self.atoms is not None and st.same(atoms, self.atoms):
            if not np.array_equal(mask, self.mask):
                self.undo_stack = (self.undo_stack + [self.mask.copy()])[-100:]
                self.redo_stack.clear()
                self.mask = mask.copy()
        else:
            old = [el for el, _ in st.species(self.atoms)] if self.atoms is not None else None
            same_count = self.atoms is not None and len(atoms) == len(self.atoms)
            self._set_structure(atoms, mask)
            if old != [el for el, _ in st.species(atoms)]:
                self.pot_manual = None
                self._rebuild_editors()
            self.show_structure(keep_view=same_count)
        self.refresh()

    def _apply_potcar_edit(self) -> None:
        self.edit_errors["POTCAR"] = []
        if not self.manual["POTCAR"]:
            self.pot_manual = None
            self.refresh()
            return
        if self.atoms is None:
            return
        labels = jg.parse_potcar_labels(self.text["POTCAR"])
        species = st.species(self.atoms)
        try:
            jg.potcar(species, labels, self.options["potcarDir"])
        except Exception as exc:
            self.pot_manual = None
            self.edit_errors["POTCAR"] = [str(exc)]
            self.refresh()
            return
        self.pot_manual = labels
        for (el, _), label in zip(species, labels):
            self.options["potcarLabels"][el] = label
        self.refresh()

    def revert_file(self, f: str) -> None:
        self.manual[f] = False
        if f == "POTCAR":
            self.pot_manual = None
        if f in self.edit_errors:
            self.edit_errors[f] = []
        self.refresh()

    # ================================================================ 输出
    def output_dir(self) -> Path | None:
        return self.source.parent if self.source else None

    def out_path(self, f: str) -> Path | None:
        out = self.output_dir()
        return out / f if out else None

    def _conflicts(self, paths: list[Path]) -> list[Path]:
        return [p for p in paths if p.exists() and self.disk.get(str(p)) is None]

    def _write(self, items: list[tuple[Path, str]]) -> list[str]:
        try:
            for path, text in items:
                path.write_text(text, encoding="utf-8", newline="\n")
                self.disk[str(path)] = text
        except OSError as exc:
            raise RuntimeError(f"写入失败：{exc}") from None
        return [str(p) for p, _ in items]

    def save_file(self, f: str, force: bool = False) -> dict:
        path = self.out_path(f)
        if path is None:
            raise ValueError("请先上传结构文件")
        if self.errors.get(f):
            raise ValueError(f"{f} 有问题，未保存：{self.errors[f][0]}")
        if not force:
            exist = self._conflicts([path])
            if exist:
                return {"needConfirm": [str(p) for p in exist],
                        "sourceConflict": self.source in exist,
                        "message": f"{exist[0].parent} 中已存在 {path.name}，是否覆盖？"}
        written = self._write([(path, self.content(f))])
        return {"written": written, "message": f"已保存 {path}"}

    def save_file_as(self, f: str, path: str) -> dict:
        if not self.content(f):
            raise ValueError(f"{f} 为空")
        p = Path(path)
        if p.is_dir():
            p = p / f
        written = self._write([(p, self.content(f))])
        return {"written": written, "message": f"已保存 {p}"}

    def generate(self, files=None, force: bool = False) -> dict:
        out = self.output_dir()
        if out is None:
            raise ValueError("请先上传结构文件")
        active = self.active_files()
        if files:
            selected = [f for f in active if f in set(files)]
        else:
            selected = self.selected_files()
        if not selected:
            raise ValueError("请至少选择一个要写入的文件")
        problems = [f"{f}：{e}" for f in selected for e in self.errors[f]]
        if problems:
            raise ValueError("；".join(problems))
        paths = [out / f for f in selected]
        if not force:
            exist = self._conflicts(paths)
            if exist:
                return {"needConfirm": [str(p) for p in exist],
                        "sourceConflict": self.source in exist,
                        "message": f"{exist[0].parent} 中已存在 {', '.join(p.name for p in exist)}，是否覆盖？"}
        written = self._write([(out / f, self.content(f)) for f in selected])
        return {"written": written, "message": f"已写入 {out} · {' '.join(selected)}"}

    # ================================================================ LLM
    def _llm_progress(self, run: int, msg: str) -> None:
        with self.lock:
            if run != self._llm_run or not self._llm_running:
                return
            self._llm_msg = msg
        self.emit({"event": "llmProgress", "msg": msg})

    def _llm_finish(self, run: int, result: dict) -> bool:
        with self.lock:
            if run != self._llm_run or not self._llm_running:
                return False
            stale = self._llm_gen != self.generation
            if stale:
                self._llm_note = "结构已改变，请重新分析"
            else:
                self._llm = {"gen": self._llm_gen, "secs": time.monotonic() - self._llm_t0, **result}
            self._llm_running = False
            self.refresh()
        return not stale

    def _llm_done(self, run: int, result: dict) -> None:
        if self._llm_finish(run, result):
            self.emit({"event": "llmDone", "message": "LLM 自旋分析完成，已写入 INCAR"})

    def _llm_failed(self, run: int, message: str) -> None:
        self._llm_finish(run, {"error": message})
        self.emit({"event": "llmFailed", "message": message})

    def start_llm(self, hint: str = "") -> None:
        if self._llm_running:
            self.cancel_llm("已取消")
            return
        if self.spos is None:
            raise ValueError("请先加载结构")
        self._llm_run += 1
        run, spos = self._llm_run, self.spos
        poscar = st.poscar_text(self.atoms, self.mask)
        hint = (hint or "").strip()
        self._llm_running = True
        self._llm_gen = self.generation
        self._llm_t0 = time.monotonic()
        self._llm_msg = "正在连接"
        self._llm_note = ""

        def work():
            try:
                result = jg.llm_magmom(
                    spos, poscar, hint,
                    progress=lambda m: self._llm_progress(run, m))
                self._llm_done(run, result)
            except Exception as exc:
                self._llm_failed(run, str(exc))

        threading.Thread(target=work, daemon=True).start()
        self.refresh()

    def cancel_llm(self, note: str, refresh: bool = True) -> None:
        if not self._llm_running:
            return
        self._llm_run += 1
        self._llm_running = False
        self._llm_note = note
        if refresh:
            self.refresh()

    # ================================================================ 状态
    def _summary(self) -> dict:
        if self.atoms is None:
            return {"loaded": False}
        gaps = self.detected[1] if self.detected else {}
        return {
            "loaded": True,
            "formula": "".join(f"{el}{n}" for el, n in st.species(self.atoms)),
            "nAtoms": len(self.atoms),
            "detected": self.detected[0] if self.detected else "unknown",
            "detectedLabel": DETECTED.get(self.detected[0] if self.detected else "unknown", ""),
            "system": self.system_type(),
            "gaps": {a: round(float(gaps.get(a, 0.0)), 2) for a in "abc"},
            "notes": list(self.load_notes),
            "source": str(self.source) if self.source else None,
            "sourceName": self.source.name if self.source else None,
            "sourceDir": str(self.source.parent) if self.source else None,
        }

    def _defaults(self) -> dict:
        elements = self._elements()
        return {
            "calcs": [{"no": no, "name": name, "tpl": tpl} for no, name, tpl in jg.CALCS],
            "systems": [{"key": k, "label": v} for k, v in SYSTEMS],
            "spinModes": [{"key": k, "label": v} for k, v in SPIN_MODES],
            "uModes": [{"key": k, "label": v} for k, v in U_MODES],
            "bondModes": [{"key": k, "label": v} for k, v in BOND_MODES],
            "mixValues": dict(self.options["mix"]["values"]),
            "ngxfValues": dict(self.options["ngxf"]["values"]),
            "optcellDefault": jg.optcell_default(),
            "cpRef": jg.CP_REF,
            "elements": elements,
            "spinDefaults": {el: jg.spin.default_element_moment(el) for el in elements},
            "uDefaults": {el: self._u_default(el) for el in elements},
            "potcarVariants": {el: list(v) for el, v in self._variants.items()},
        }

    def _potcar_state(self) -> dict:
        enmax = max((i["enmax"] for i in self.pot_infos), default=0.0)
        encut = jg.tag_value(self.text["INCAR"], "ENCUT") if self.text["INCAR"] else None
        return {
            "dir": self.options["potcarDir"],
            "variants": {el: list(v) for el, v in self._variants.items()},
            "labels": dict(self.options["potcarLabels"]),
            "infos": list(self.pot_infos),
            "enmax": float(enmax),
            "encut": encut,
            "hint": self.hints["pot"],
            "clean": bool(self.options["clean"]),
            "fullLines": len(self.potcar_full.splitlines()) if self.potcar_full else 0,
            "ready": bool(self.potcar_full),
        }

    def _file_state(self, f: str, active: list[str]) -> dict:
        text = self.text[f]
        path = self.out_path(f)
        saved = bool(text) and path is not None and self.disk.get(str(path)) == self.content(f)
        errors = list(self.errors.get(f, []))
        info = ""
        if errors:
            info = "；".join(errors)
        elif self.manual[f] and text != self.auto[f]:
            info = {
                "POSCAR": "已同步到 3D 视图；在视图中修改固定状态会按当前结构重新生成",
                "POTCAR": "已按标签重新拼接",
            }.get(f, "已手动编辑 · 左侧设置的改动会合并进来，同一参数以最后一次改动为准")
        elif f == "POTCAR" and self.potcar_full:
            info = "仅显示组成，不显示赝势全文"
        elif f == "POSCAR" and text:
            info = "Selective dynamics：T 弛豫 / F 固定"
        elif f in EXTRA and text:
            info = self.notes[f]
        return {
            "text": text,
            "manual": bool(self.manual[f]),
            "saved": bool(saved),
            "errors": errors,
            "error": bool(errors),
            "info": info,
            "visible": f in active or f in FILES,
            "active": f in active,
            "included": bool(self.options["include"].get(f, True)),
            "stateChip": ("手动编辑" if self.manual[f] else "自动生成") + (" · 已保存" if saved else " · 未保存"),
            "revert": bool(self.manual[f]),
        }

    def _status_state(self) -> dict:
        out = self.output_dir()
        selected = self.selected_files()
        problems = [e for f in selected for e in self.errors[f]]
        if self.atoms is None:
            text, color = "请先上传结构文件", ""
        elif not selected:
            text, color = "请至少选择一个要写入的文件", ""
        elif problems:
            text, color = "；".join(problems), RED
        else:
            text = f"将写入 {' '.join(selected)}"
            edited = [f for f in selected if self.manual[f]]
            if edited:
                text += f"（{' '.join(edited)} 为手动编辑）"
            if out is not None and self.source and self.source.name in selected and self.source.parent == out:
                text += f" · 将覆盖输入文件 {self.source.name}"
            color = ""
        return {
            "text": text,
            "color": color,
            "canGenerate": self.atoms is not None and bool(selected) and not problems,
            "selected": selected,
            "outdir": str(out) if out is not None else None,
        }

    def _fix_state(self) -> dict:
        if self.atoms is None:
            return {"loaded": False, "text": "未加载结构", "layerText": "",
                    "canUndo": False, "canRedo": False}
        full = int(self.mask.all(axis=1).sum())
        part = int((self.mask.any(axis=1) & ~self.mask.all(axis=1)).sum())
        free = len(self.atoms) - full - part
        axis = self.layer_axis()
        layers = len(st.layers(self.atoms, axis))
        text = f"共 {len(self.atoms)} 个原子 · 固定 {full} · 弛豫 {free}"
        if part:
            text += f" · 部分固定 {part}"
        return {
            "loaded": True, "text": text, "layerText": f"沿 {'abc'[axis]} 方向共 {layers} 层",
            "layerAxis": axis, "layerCount": layers,
            "canUndo": bool(self.undo_stack), "canRedo": bool(self.redo_stack),
        }

    def _system_hint(self) -> dict:
        rule = f"弛豫使用 relax-{self.system_type()}.incar · 决定 K 点约束"
        if self.options["system"] == "auto":
            detected = (f"检测为 {DETECTED[self.detected[0]]} · " if self.detected
                        else "未加载结构，暂按体相 · ")
            rule = detected + rule
        return _hint(rule)

    def _llm_state(self) -> dict:
        running = self._llm_running
        elapsed = time.monotonic() - self._llm_t0 if running else 0.0
        result = self._llm if self._llm and self._llm["gen"] == self.generation else None
        configured = False
        try:
            configured = jg.llm_configured()
        except Exception:
            configured = False
        button = "取消" if running else ("重新分析" if result else "开始分析")
        base = {"buttonText": button, "running": running, "elapsed": elapsed,
                "configured": configured, "status": "idle", "title": "", "titleColor": "",
                "body": "", "magmom": "", "rationale": "", "warnings": [], "msg": self._llm_msg}
        if running:
            base.update({
                "status": "running",
                "title": f"分析中 · {self._llm_msg} · 已用 {elapsed:.0f} s",
                "titleColor": BLUE,
                "body": "正在分析结构、查询元素与材料知识库，完成后自动写入 MAGMOM",
            })
            return base
        if result is None:
            base.update({
                "status": "idle",
                "title": self._llm_note or "尚未分析",
                "body": "点击“开始分析”：LLM 结合结构分析、元素与材料知识库给出磁矩方案，"
                        "经 add-spin 校验后写入 MAGMOM；补充说明会一并发送给 LLM",
            })
            return base
        if "error" in result:
            base.update({
                "status": "error",
                "title": "分析失败",
                "titleColor": RED,
                "body": result["error"],
                "warnings": ["可重试，或改用启发式 / 按元素指定"],
            })
            return base
        ispin = "ISPIN = 2" if result["ispin"] == 2 else "ISPIN = 1（非磁性）"
        block = jg.spin.format_magmom(self.spos, result["moments"], ispin=result["ispin"]) if self.spos else ""
        magmom = next((line.strip() for line in block.splitlines()
                       if line.strip().upper().startswith("MAGMOM")), "")
        base.update({
            "status": "done",
            "title": f"完成 · {result.get('steps', 0)} 轮 · {result.get('secs', 0):.0f} s · {ispin}",
            "titleColor": AMBER if result.get("warnings") else GREEN,
            "magmom": magmom,
            "rationale": result.get("rationale") or "",
            "warnings": list(result.get("warnings") or []),
        })
        return base

    def state(self, known_revision=None) -> dict:
        structure = None
        view = None
        keep_view = False
        if self.atoms is not None and known_revision != self.structure_revision:
            structure = st.viewer_payload(
                self.atoms, self.options["boundary"], self.options["bonds"])
            view = self._pending_view
            keep_view = self._pending_keep_view
            self._pending_view = None
            self._pending_keep_view = False
        clear_viewer = self._pending_clear
        self._pending_clear = False
        active = self.active_files()
        status = self._status_state()
        return {
            "source": str(self.source) if self.source else None,
            "summary": self._summary(),
            "options": self.options,
            "defaults": self._defaults(),
            "potcar": self._potcar_state(),
            "files": {f: self._file_state(f, active) for f in ALL_FILES},
            "activeFiles": active,
            "outdir": status["outdir"],
            "status": status,
            "hints": {**self.hints, "sys": self._system_hint()},
            "fix": self._fix_state(),
            "llm": self._llm_state(),
            "canUndo": bool(self.undo_stack),
            "canRedo": bool(self.redo_stack),
            "generation": self.generation,
            "structureRevision": self.structure_revision,
            "masks": self.mask.tolist(),
            "structure": structure,
            "view": view,
            "keepView": keep_view,
            "clearViewer": clear_viewer,
        }
