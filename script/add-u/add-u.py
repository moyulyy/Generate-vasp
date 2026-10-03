# -*- coding: utf-8 -*-
"""
add-u.py —— 为 INCAR 追加 DFT+U 参数 (LDAU / LDAUL / LDAUU / LDAUJ)

CLI:
    python add-u.py                 # 读 POSCAR, 打印并追加到 INCAR
    python add-u.py -l              # 列出 U 值库
    python add-u.py -u Co=2         # 指定 Co 用第 2 号候选 (可重复)
    python add-u.py -c Fe:2,2.8,1.2 # 临时自定义 (LDAUL,U,J)
    python add-u.py -n              # 多候选时直接用默认, 不交互
    python add-u.py -d              # 只打印, 不写入 INCAR

模块:
    from add_u import parse_poscar, build_dftu, append_dftu, U, DEFAULT_U
"""
import re
import sys
import argparse

ORB = {"p": 1, "d": 2, "f": 3}          # 轨道 -> LDAUL
OPTIONAL = {"Ga", "Pb", "As"}           # 仅 --u 显式选择时才施加 U

# 元素 -> [(轨道, U, J, DOI, 备注), ...]
#   第 0 条 = add-u.py 原有默认值; 其余 = References-out 的文献补充值 (Ueff = U - J)。
#   注: 内层方括号中的值为 U 与 J, LDAUU 写入 U, LDAUJ 写入 J。
U = {
    "Sc": [("d", 2.5, 0, ""), ("d", 2.11, 0, "10.1021/acscatal.2c01011")],
    "Ti": [("d", 2.5, 0, ""),
           ("d", 2.58, 0, "10.1021/acscatal.2c01011"), ("d", 3.5, 0, "10.1039/D2CP05631C"),
           ("d", 4.0, 0, "10.1021/ct400235w"), ("d", 8.0, 0, "10.1016/j.surfin.2023.102751")],
    "V":  [("d", 2.7, 0, ""), ("d", 2.72, 0, "10.1021/acscatal.2c01011"), ("d", 3.0, 0, "10.1021/ct400235w")],
    "Cr": [("d", 2.8, 0, ""), ("d", 2.79, 0, "10.1021/acscatal.2c01011")],
    "Mn": [("d", 3.1, 0, ""), ("d", 3.06, 0, "10.1021/acscatal.2c01011"),
           ("d", 3.7, 0, "10.1021/acs.jpcc.5b03169"), ("d", 2.8, 1.2, "10.1016/j.chempr.2022.07.002")],
    "Fe": [("d", 3.3, 0, ""), ("d", 3.29, 0, "10.1021/acscatal.2c01011"),
           ("d", 4.0, 0, "10.1021/ct400235w"), ("d", 2.56, 0, "10.1021/acs.jpcc.5b03169")],
    "Co": [("d", 3.4, 0, ""), ("d", 3.42, 0, "10.1021/acscatal.2c01011"),
           ("d", 2.5, 0, "10.1002/cphc.202001033"), ("d", 3.5, 0, "10.1021/acs.jpcc.5b03169"),
           ("d", 4.0, 0, "10.1021/acs.jpcc.9b04683")],
    "Ni": [("d", 3.4, 0, ""), ("d", 3.40, 0, "10.1021/acscatal.2c01011"),
           ("d", 4.0, 0, "10.1021/ct400235w"), ("d", 5.5, 0, "10.1021/acscatal.7b00999"),
           ("d", 5.2, 0, "10.1021/acs.jpcc.5b03169"), ("d", 5.9, 0, "10.1038/s41524-020-00446-9")],
    "Cu": [("d", 2.5, 0, ""), ("d", 3.87, 0, "10.1021/acscatal.2c01011"),
           ("d", 6.0, 0, "10.1016/j.surfin.2023.102751")],
    "Zn": [("d", 2.5, 0, ""), ("d", 4.12, 0, "10.1021/acscatal.2c01011"),
           ("d", 9.3, 0, "10.1088/1361-648X/aaa441"), ("d", 13.0, 0, "10.1088/1361-648X/aaa441"),
           ("d", 8.0, 0, "10.1007/s00214-016-1927-4")],
    "Y":  [("d", 2.0, 0, "")], "Zr": [("d", 2.0, 0, "")], "Nb": [("d", 2.0, 0, "")],
    "Mo": [("d", 2.2, 0, ""), ("d", 4.0, 0, "10.1021/ct400235w"), ("d", 3.3, 0, "10.1021/acsami.2c00501")],
    "Tc": [("d", 2.3, 0, "")], "Ru": [("d", 2.4, 0, "")], "Rh": [("d", 2.8, 0, "")],
    "Pd": [("d", 3.3, 0, "")], "Ag": [("d", 2.0, 0, "")],
    "Cd": [("d", 2.0, 0, ""), ("d", 8.0, 0, "10.1007/s00214-016-1927-4")],
    "Hf": [("d", 2.7, 0, "")], "Ta": [("d", 2.1, 0, "")],
    "W":  [("d", 2.2, 0, ""), ("d", 4.0, 0, "10.1021/ct400235w")],
    "Re": [("d", 2.3, 0, "")], "Os": [("d", 2.2, 0, "")], "Ir": [("d", 2.3, 0, "")],
    "Pt": [("d", 2.4, 0, "")],
    "Ce": [("f", 5.0, 0, ""), ("f", 4.5, 0, "10.1021/acscatal.6b01907"),
           ("f", 5.3, 0, "10.1021/acscatal.6b01907"), ("f", 3.5, 0, "10.1021/acscatal.6b01907")],
    "Nd": [("f", 6.0, 0, "")],
    "Eu": [("f", 7.1, 0, "10.1038/s41524-020-00446-9"), ("f", 5.5, 0, "10.1038/s41524-020-00446-9")],
    "U":  [("f", 7.0, 0, "")],
    "In": [("d", 7.0, 0, ""), ("p", 0.7, 0, "10.1038/s41524-020-00446-9"),
           ("p", -0.5, 0, "10.1038/s41524-020-00446-9")],
    "Ga": [("p", 4.0, 0, "10.1021/acs.jpcc.9b04683")],
    "Pb": [("p", 4.0, 0, "10.1021/acs.jpcc.9b04683")],
    "As": [("p", 3.3, 0, "10.1038/s41524-020-00446-9"), ("p", -7.5, 0, "10.1038/s41524-020-00446-9")],
}

# 默认施加 U 的元素 -> (轨道, LDAUL, LDAUU, LDAUJ); 保留原 add-u.py 取值, 新增 Eu
DEFAULT_U = {el: (r[0][0], ORB[r[0][0]], r[0][1], r[0][2])
             for el, r in U.items() if el not in OPTIONAL}


def parse_poscar(path="POSCAR"):
    """POSCAR -> [(元素, 数量), ...], 解析失败返回 []。"""
    lines = open(path, encoding="utf-8").read().splitlines()
    if len(lines) < 7:
        return []
    els = re.findall(r"[A-Z][a-z]*", lines[5])
    cnt = list(map(int, re.findall(r"\d+", lines[6])))
    return list(zip(els, cnt)) if els and len(els) == len(cnt) else []


def prompt(el, recs):
    """交互选择: 展示【元素、Ueff、U、J、DOI】。"""
    print(f"\n元素 {el}: {len(recs)} 个候选 U 值")
    print(f"  {'#':>2}  {'Ueff/eV':>7}  {'U/eV':>6}  {'J/eV':>5}  DOI")
    for i, (_, u, j, doi, _n) in enumerate(recs):
        print(f"  {i:>2}  {u - j:>7g}  {u:>6g}  {j:>5g}  {doi or '内置默认'}")
    while True:
        try:
            s = input(f"请选择 {el} 的 U 值 [0-{len(recs) - 1}] (回车=0): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not s:
            return 0
        if s.isdigit() and int(s) < len(recs):
            return int(s)
        print("输入无效, 请重试")


def _row(name, vals):
    return f"   {name:<9}=  " + "".join(f"{v:<6g} " for v in vals)


def build_dftu(pairs, choices=None, custom=None, chooser=None):
    """生成 DFT-U 参数块 (末尾附 #References)。全部元素都不加 U 时返回 ""。"""
    choices, custom = choices or {}, custom or {}
    ldaul, ldauu, ldauj, refs = [], [], [], []
    for el, _ in pairs:
        if el in custom:
            l, u, j, doi = custom[el] + ("custom",)
        elif el in U:
            if el in choices:
                rec = U[el][choices[el]]
            elif el in DEFAULT_U:
                rec = U[el][chooser(el, U[el])] if chooser and len(U[el]) > 1 else U[el][0]
            else:
                rec = None
            l, u, j, doi = (ORB[rec[0]], rec[1], rec[2], rec[3]) if rec else (-1, 0.0, 0.0, "")
        else:
            l, u, j, doi = -1, 0.0, 0.0, ""
        ldaul, ldauu, ldauj = ldaul + [l], ldauu + [u], ldauj + [j]
        if l >= 0:
            refs.append((el, u - j, u, j, doi))

    if max(ldaul) < 0:
        return ""
    head = "   #Element :  " + "".join(f"{e:<6} " for e, _ in pairs)
    out = ["DFT-U parameter", "   LDAU     =  .TRUE.", "   LDAUTYPE =  2",
           f"   LMAXMIX  =  {2 * max(ldaul)}", head,
           _row("LDAUL", ldaul), _row("LDAUU", ldauu), _row("LDAUJ", ldauj)]
    if refs:
        out += ["#References", "#   element Ueff/eV  U/eV     J/eV    DOI"]
        out += [f"#   {e:<7} {ue:<8g} {u:<8g} {j:<7g} {d or 'N/A (built-in)'}" for e, ue, u, j, d in refs]
    return "\n".join(out) + "\n"


def append_dftu(path="POSCAR", incar="INCAR", choices=None, custom=None, interactive=False):
    """解析 POSCAR 并向 INCAR 追加, 成功返回 True。"""
    pairs = parse_poscar(path)
    if not pairs:
        return False
    chooser = prompt if interactive and sys.stdin.isatty() else None
    msg = build_dftu(pairs, choices, custom, chooser)
    if not msg:
        return False
    with open(incar, "a", encoding="utf-8") as f:
        f.write("\n" + msg)
    return True


def main(argv=None):
    p = argparse.ArgumentParser(description="为 INCAR 追加 DFT+U 参数")
    p.add_argument("-p", "--poscar", default="POSCAR", help="POSCAR 路径")
    p.add_argument("-i", "--incar", default="INCAR", help="INCAR 路径")
    p.add_argument("-u", action="append", default=[], metavar="EL=IDX",
                   help="指定元素使用的候选序号, 可重复, 如 -u Co=2")
    p.add_argument("-c", "--custom", default="", metavar="EL:L,U,J[;...]",
                   help="自定义 U 值, 如 -c Fe:2,2.8,1.2")
    p.add_argument("-n", "--no-interactive", action="store_true", help="不交互, 直接用默认值")
    p.add_argument("-d", "--dry-run", action="store_true", help="只打印, 不写入 INCAR")
    p.add_argument("-l", "--list", action="store_true", help="列出 U 值库后退出")
    args = p.parse_args(argv)

    if args.list:
        for el, recs in U.items():
            for i, (o, u, j, doi) in enumerate(recs):
                tag = "默认" if (el in DEFAULT_U and i == 0) else ("可选" if el in OPTIONAL else "文献")
                print(f"{el:<3} [{i}] {tag}  {o}  Ueff={u - j:g}  U={u:g}  J={j:g}  {doi or '-'}")
        return

    pairs = parse_poscar(args.poscar)
    if not pairs:
        sys.exit(f"无法解析 {args.poscar}")
    choices = {el: int(i) for el, _, i in (s.partition("=") for s in args.u)}
    custom = {}
    for item in filter(None, args.custom.split(";")):
        el, vals = item.split(":")
        custom[el] = tuple(map(float, vals.split(",")))
    chooser = None if args.no_interactive else (prompt if sys.stdin.isatty() else None)

    msg = build_dftu(pairs, choices, custom, chooser)
    if not msg:
        sys.exit("(体系元素均无 U 参数)")
    print(msg, end="")
    if not args.dry_run:
        with open(args.incar, "a", encoding="utf-8") as f:
            f.write("\n" + msg)


if __name__ == "__main__":
    main()
