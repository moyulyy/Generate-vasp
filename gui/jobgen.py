# -*- coding: utf-8 -*-
"""INCAR / KPOINTS / POTCAR 生成；复用 script/ 下的现有脚本。"""
from __future__ import annotations

import importlib.util
import re
import sys
from functools import lru_cache
from pathlib import Path

# 便携版（PyInstaller）：模板与 script/ 放在 exe 旁边，可直接修改
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
INCAR_TPL = ROOT / "IncarTemplates"
COND_TPL = ROOT / "ConditionsTemplates"
SCRIPT = ROOT / "script"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 解析注解时需要模块已注册
    spec.loader.exec_module(module)
    return module


spin = _load("add_spin", SCRIPT / "add-spin" / "add-spin.py")
dftu = _load("add_u", SCRIPT / "add-u" / "add-u.py")
cpvasp = _load("add_cp_vasp", SCRIPT / "add-cp-vasp.py")
efield = _load("add_efield", SCRIPT / "add-efield.py")
parallel = _load("add_parallel", SCRIPT / "add-parallel.py")
kpoint = _load("kpoint", SCRIPT / "mk-KPOINTS" / "kpoint.py")
mkpot = _load("mk_potcar", SCRIPT / "mk-POTCAR" / "mk_potcar.py")

CP_REF = -4.43
# (编号, 名称, 模板名；None = 按体系选 relax-bulk / relax-slab / relax-mole)
CALCS = [
    ("01", "弛豫", None), ("02", "态密度", "dos"), ("03", "能带 SCF", "band-scf"), ("04", "能带 band", "band-band"),
    ("05", "差分电荷", "chargediff"), ("06", "Bader 电荷", "bader"), ("07", "功函数", "workfunction"),
    ("08", "ELF 图像", "elf"), ("09", "COHP", "cohp"), ("10", "CI-NEB", "neb"), ("11", "频率", "frequency"),
    ("12", "AIMD", "aimd"),
]
NOTES = {
    "band-band": "非自洽能带（ICHARG=11）：需先完成能带 SCF，并把 CHGCAR 复制到本目录",
    "neb": "IMAGES=3：需另行准备 00–04 各镜像目录的 POSCAR",
    "frequency": "有限差分只位移未固定的原子，可在右侧结构视图中设置",
    "workfunction": "已开启偶极修正 LDIPOL / IDIPOL=3，真空层应沿 c 方向",
}
SUMMARY_TAGS = {"IBRION", "ISIF", "NSW", "ICHARG", "LORBIT", "NEDOS", "IMAGES", "MDALGO", "TEBEG",
                "LAECHG", "LELF", "LVHAR", "NFREE"}
TAG_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=")


def read_tpl(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def merge(base: str, block: str) -> str:
    """追加参数块，并把 base 中被重新定义的标签注释掉，避免 INCAR 出现重复键。"""
    tags = {m.group(1).upper() for line in block.splitlines() if (m := TAG_RE.match(line))}
    lines = []
    for line in base.splitlines():
        m = TAG_RE.match(line)
        if m and m.group(1).upper() in tags:
            line = re.sub(r"^(\s*)", r"\1#", line, count=1)
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n\n" + block.strip("\n") + "\n"


def rebase(edited: str, old: str, new: str) -> str:
    """把自动生成内容的改动（old → new，来自左侧选项）合并进手动编辑的文本 edited。

    按行三方合并：用户没碰过的区域直接跟随选项；同一区域两边都改了时以选项为准，
    但保留用户在该区域新增的、选项未涉及的参数行。新写入的参数若在别处已有生效的同名行，
    像 merge() 一样把旧行注释掉，INCAR 不会出现重复键。
    """
    from difflib import SequenceMatcher
    if edited == old:
        return new
    o, e, n = ([l.rstrip() for l in t.splitlines()] for t in (old, edited, new))  # merge() 会去掉行尾空白
    # old 行号 → edited 行号（未改动的行）
    pos = {}
    for a, b, size in SequenceMatcher(None, o, e, autojunk=False).get_matching_blocks():
        for k in range(size):
            pos[a + k] = b + k

    def tags(lines):
        return {m.group(1).upper() for line in lines if (m := TAG_RE.match(line))}

    out, cursor, added = [], 0, []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, o, n, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        # edited 中对应 old[i1:i2] 的区域：前一个未改动行之后，到后一个未改动行之前
        lo = next((pos[k] + 1 for k in range(i1 - 1, -1, -1) if k in pos), 0)
        hi = next((pos[k] for k in range(i2, len(o)) if k in pos), len(e))
        lo = max(lo, cursor)
        if hi < lo:
            hi = lo
        region = e[lo:hi]
        out += e[cursor:lo]
        span, before, after = o[i1:i2], [], []
        k = next((k for k in range(len(region) - len(span) + 1) if region[k:k + len(span)] == span), -1) if span else -1
        if op == "insert":            # 纯插入：该位置上用户写的内容原样保留在前
            before = region
        elif k >= 0:                  # 用户只在这段周围增删了行：原段替换为新内容，周围的行保留
            before, after = region[:k], region[k + len(span):]
        elif region:                  # 两边都改了：以选项为准，保留用户新增、选项未涉及的参数行
            owned = tags(span) | tags(n[j1:j2])
            before = [l for l in region
                      if l not in span and (m := TAG_RE.match(l)) and m.group(1).upper() not in owned]
        out += before
        added += range(len(out), len(out) + j2 - j1)
        out += n[j1:j2] + after
        cursor = hi
    out += e[cursor:]

    new_tags = tags(out[i] for i in added)
    fresh = set(added)
    for i, line in enumerate(out):
        m = TAG_RE.match(line)
        if i not in fresh and m and m.group(1).upper() in new_tags:
            out[i] = re.sub(r"^(\s*)", r"\1#", line, count=1)
    return "\n".join(out) + ("\n" if new.endswith("\n") or edited.endswith("\n") else "")


def tag_value(text: str, tag: str) -> str | None:
    m = re.search(rf"^\s*{tag}\s*=\s*([^#!\s]+)", text, re.M | re.I)
    return m.group(1) if m else None


def tpl_summary(text: str) -> str:
    found = []
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z_]\w*)\s*=\s*([^#!\s]+)", line)
        if m and m.group(1).upper() in SUMMARY_TAGS:
            found.append(f"{m.group(1).upper()}={m.group(2)}")
    return " · ".join(found[:5])


def conditions(text: str, *, solvation=False, efield_value=None, idipol=3, cp_mu=None, kpar_npar=None) -> str:
    if solvation and cp_mu is None:  # CP-VASP 模板已含溶剂化参数
        text = merge(text, read_tpl(COND_TPL / "solvation.incar"))
    if efield_value is not None:
        text = merge(text, efield.modify_efield(read_tpl(COND_TPL / "electric-field.incar"), efield_value, idipol))
    if cp_mu is not None:
        text = merge(text, cpvasp.modify_targetmu(read_tpl(COND_TPL / "cp-vasp.incar"), cp_mu))
    if kpar_npar is not None:
        text = merge(text, parallel.modify_parallel_params(read_tpl(COND_TPL / "parallel.incar"), *kpar_npar))
    return text


# ------------------------------------------------------------------ KPOINTS
def kpoints(atoms, tpl: str, systype: str, vac_axis: int, density: float, per_seg: int):
    """返回 (KPOINTS 文本, 摘要, 警告列表)。能带 band 用高对称路径，其余用 Γ 中心网格。"""
    if tpl == "band-band":
        if systype == "mole":
            raise ValueError("分子体系没有周期性，无法生成能带路径")
        if systype == "slab" and vac_axis != 2:
            raise ValueError("表面能带路径要求真空层沿 c 方向")
        path = kpoint.build_band_path(atoms, systype, backend="auto")
        warnings = list(path["warnings"])
        if systype == "bulk" and path["backend"] == "ase":
            prim = kpoint._primitive_atoms(atoms)
            if len(prim) < len(atoms):
                warnings.append(f"当前结构不是原胞（原胞 {len(prim)} 个原子），路径坐标按原胞给出；能带请用原胞结构计算")
        text = kpoint.format_band_kpoints(path["segments"], path["labels"], per_seg)
        return text, f"高对称路径 {kpoint.band_path_summary(path['labels'])} · {path['lattice']} · 每段 {per_seg} 点", warnings
    mesh = list(kpoint.mesh_from_atoms(atoms, density))
    if systype == "mole":
        mesh = [1, 1, 1]
    elif systype == "slab":
        mesh[vac_axis] = 1
    return kpoint.format_kpoints(mesh) + "\n", "Γ 中心网格 " + " × ".join(map(str, mesh)), []


# ------------------------------------------------------------------ POTCAR
def default_potcar_dir() -> str:
    """mk_potcar.py 中配置的赝势库；不存在时（如便携版）默认为根目录下的 POTCAR 文件夹。"""
    return mkpot.POTCAR_DIR if Path(mkpot.POTCAR_DIR).is_dir() else str(ROOT / "POTCAR")


def default_label(el: str) -> str:
    return mkpot.ELEMENTS_DICT.get(el, el)


def potcar_variants(potcar_dir: str, el: str) -> list[str]:
    """赝势库中该元素的可选标签（Fe, Fe_pv, Fe_sv …），默认标签排第一。"""
    root = Path(potcar_dir)
    found = []
    if root.is_dir():
        found = sorted(p.name for p in root.iterdir()
                       if (p.name == el or p.name.startswith(el + "_")) and (p / "POTCAR").is_file())
    default = default_label(el)
    return [default] + [v for v in found if v != default]


@lru_cache(maxsize=64)
def _read_potcar(path: str, _mtime: float) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def potcar_entry(potcar_dir: str, label: str, clean: bool = False) -> tuple[str, dict]:
    """读取单个赝势，返回 (内容, {titel, enmax, zval})。"""
    f = Path(potcar_dir) / label / "POTCAR"
    if not f.is_file():
        raise ValueError(f"找不到 {f}")
    text = _read_potcar(str(f), f.stat().st_mtime)
    if clean:
        text = "\n".join(l for l in text.split("\n") if "SHA256" not in l and "COPYR" not in l)
    titel = re.search(r"TITEL\s*=\s*(.+)", text)
    e = re.search(r"ENMAX\s*=\s*([\d.]+)", text)
    z = re.search(r"ZVAL\s*=\s*([\d.]+)", text)
    v = re.search(r"VRHFIN\s*=(.*)", text)
    info = {"titel": titel.group(1).strip() if titel else "?", "label": label,
            "enmax": float(e.group(1)) if e else 0.0, "zval": float(z.group(1)) if z else 0.0,
            "vrhfin": v.group(1).strip() if v else ""}
    return (text if text.endswith("\n") else text + "\n"), info


def potcar(species, labels: list[str], potcar_dir: str, clean: bool = False) -> tuple[str, list[dict]]:
    """按 POSCAR 元素块顺序拼接，返回 (POTCAR 全文, 每块的信息)。"""
    if len(labels) != len(species):
        raise ValueError(f"需要 {len(species)} 个赝势标签（与 POSCAR 元素块一一对应），当前 {len(labels)} 个")
    parts, infos = [], []
    for (el, _), label in zip(species, labels):
        if label != el and not label.startswith(el + "_"):
            raise ValueError(f"标签 {label} 与 POSCAR 元素 {el} 不符")
        text, info = potcar_entry(potcar_dir, label, clean)
        parts.append(text)
        infos.append(info)
    return "".join(parts), infos


def potcar_composition(species, labels, infos, potcar_dir: str, tail: str = "") -> str:
    """POTCAR 页的可编辑文本：每行一个标签，其后为注释信息（不显示赝势全文）。"""
    lines = ["# POTCAR 组成：每行一个赝势标签，顺序与 POSCAR 元素块一致，可直接修改（如 Ni → Ni_pv）",
             "# 赝势全文不在此显示；保存 / 生成时按标签从赝势库拼接",
             f"# 赝势库：{potcar_dir}", ""]
    for (el, n), label, info in zip(species, labels, infos):
        lines.append(f"{label:<12}# {el} ×{n} · {info['titel']} · ENMAX {info['enmax']:.1f} · ZVAL {info['zval']:g}")
    if tail:
        lines += ["", f"# {tail}"]
    return "\n".join(lines) + "\n"


def parse_potcar_labels(text: str) -> list[str]:
    return [line.split("#", 1)[0].split()[0] for line in text.splitlines()
            if line.split("#", 1)[0].strip()]


# ------------------------------------------------------------------ 可编辑数值的模板（MIX / NGXF）
VALUE_RE = re.compile(r"^(\s*)#?\s*([A-Za-z_]\w*)(\s*=\s*)([^\s#!]+)(.*)$")


def template_values(name: str) -> list[tuple[str, str]]:
    """模板中的 (标签, 默认值)，包括被注释掉的行（如 parameters-NGXF 中的 #NGXF）。"""
    out = []
    for line in read_tpl(COND_TPL / name).splitlines():
        m = VALUE_RE.match(line)
        if m:
            out.append((m.group(2), m.group(4)))
    return out


def apply_values(name: str, values: dict[str, str]) -> str:
    """取消注释并写入数值，返回可追加到 INCAR 的参数块。"""
    lines = []
    for line in read_tpl(COND_TPL / name).splitlines():
        m = VALUE_RE.match(line)
        if m and m.group(2) in values:
            indent, tag, eq, _, rest = m.groups()
            lines.append(f"{indent}{tag}{eq}{values[tag]}{rest}")
        elif line.strip():
            lines.append(line)
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ OPTCELL
def optcell_default() -> list[list[int]]:
    rows = [l.strip() for l in read_tpl(COND_TPL / "OPTCELL.incar").splitlines()
            if l.strip() and not l.lstrip().startswith("#")]
    return [[int(c) for c in r[:3]] for r in rows[:3]]


def optcell_text(grid: list[list[int]]) -> str:
    """OPTCELL 文件：三行 0/1，依次对应晶格矢量 a、b、c 的 x、y、z 分量，1 = 允许变化。"""
    return "\n".join("".join(str(int(v)) for v in row) for row in grid) + "\n"


# ------------------------------------------------------------------ LLM 自旋分析（在后台线程中调用）
# 连接配置（API Key / 接口地址 / 模型）只来自配置：环境变量 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL
# 优先，其次为项目根目录（便携版为 exe 所在目录）的 llm_config.json。界面不显示、也不允许修改这些信息。
def llm_configured() -> bool:
    import os
    return bool(next((os.environ[k] for k in ("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
                      if os.environ.get(k)), spin.LLM_API_KEY))


def _redact(text: str, client) -> str:
    """去掉错误信息中的接口地址、模型名与密钥。"""
    for secret, alias in ((client.api_key, "***"), (client.base_url, "LLM 接口"), (client.model, "所配置的模型")):
        if secret:
            text = text.replace(secret.rstrip("/"), alias)
    return text


def llm_magmom(spos, poscar: str, hint: str = "", progress=None) -> dict:
    """调用 add-spin 的 LLM 工具调用流程；失败时抛出异常（不静默回退）。"""
    import argparse
    if not llm_configured():
        raise RuntimeError(f"未配置 LLM：请把 llm_config.example.json 复制为 {ROOT / 'llm_config.json'} 并填写 api_key，"
                           "或设置环境变量 LLM_API_KEY")
    args = argparse.Namespace(api_key=None, base_url=None, model=None,
                              temperature=spin.LLM_TEMPERATURE, timeout=spin.LLM_TIMEOUT,
                              max_retries=spin.LLM_MAX_RETRIES)
    client = spin.build_client_from_args(args)
    raw_chat, step = client.chat, [0]

    def chat(*a, **k):
        step[0] += 1
        if progress:
            progress(f"第 {step[0]} 轮请求")
        return raw_chat(*a, **k)

    client.chat = chat
    try:
        runtime = spin.ToolRuntime(spos)
        plan = spin.run_agent(client, runtime, spin.build_user_prompt(spos, poscar, hint), spin.LLM_MAX_STEPS,
                              verbose=False)
        if not plan:
            raise RuntimeError(f"LLM 在 {step[0]} 轮内未提交有效方案")
        moments, warnings = spin.plan_from_llm_args(spos, runtime.report, plan)
    except Exception as exc:
        raise RuntimeError(_redact(str(exc), client)) from None
    return {"moments": moments, "ispin": spin.resolve_ispin(moments, plan.get("ispin")),
            "rationale": plan.get("rationale", ""), "warnings": warnings, "steps": step[0]}
