# -*- coding: utf-8 -*-
"""
根据 POSCAR 第 6 行的元素字串（或直接给元素字串）生成 VASP 的 POTCAR 文件。

命令行用法:
    # 直接给元素字串
    python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR

    # 给 POSCAR 文件，自动读取第 6 行元素
    python mk_potcar.py D:/work/POSCAR -o D:/work

    # 指定赝势数据库路径（默认用脚本里的 POTCAR_DIR）
    python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR --potcar-dir D:/fast-vasp/script/POTCAR

    # 生成时删除各元素 POTCAR 中的 SHA256 / COPYR 行（--clean）
    python mk_potcar.py "Bi Mo O" -o D:/work/POTCAR --clean

也可以导入调用:
    from mk_potcar import make_potcar
    make_potcar("Bi Mo O", "D:/work/POTCAR")
    make_potcar("Bi Mo O", "D:/work/POTCAR", clean=True)
"""

import argparse
import os

# POTCAR 数据库路径：里面每个元素一个文件夹，结构是  {POTCAR_DIR}\标签\POTCAR
POTCAR_DIR = r"D:\fast-vasp\script\POTCAR"

# 元素 -> POTCAR 标签 的字典（和 fast-vasp 项目里的映射表一致）
ELEMENTS_DICT = {
    'H': 'H', 'He': 'He', 'Li': 'Li_sv', 'Be': 'Be', 'B': 'B', 'C': 'C',
    'N': 'N', 'O': 'O', 'F': 'F', 'Ne': 'Ne', 'Na': 'Na_pv', 'Mg': 'Mg',
    'Al': 'Al', 'Si': 'Si', 'P': 'P', 'S': 'S', 'Cl': 'Cl', 'Ar': 'Ar',
    'K': 'K_sv', 'Ca': 'Ca_sv', 'Sc': 'Sc_sv', 'Ti': 'Ti_sv', 'V': 'V_sv',
    'Cr': 'Cr_pv', 'Mn': 'Mn_pv', 'Fe': 'Fe', 'Co': 'Co', 'Ni': 'Ni',
    'Cu': 'Cu', 'Zn': 'Zn', 'Ga': 'Ga_d', 'Ge': 'Ge_d', 'As': 'As',
    'Se': 'Se', 'Br': 'Br', 'Kr': 'Kr', 'Rb': 'Rb_sv', 'Sr': 'Sr_sv',
    'Y': 'Y_sv', 'Zr': 'Zr_sv', 'Nb': 'Nb_sv', 'Mo': 'Mo_sv', 'Tc': 'Tc_pv',
    'Ru': 'Ru_pv', 'Rh': 'Rh_pv', 'Pd': 'Pd', 'Ag': 'Ag', 'Cd': 'Cd',
    'In': 'In_d', 'Sn': 'Sn_d', 'Sb': 'Sb', 'Te': 'Te', 'I': 'I',
    'Xe': 'Xe', 'Cs': 'Cs_sv', 'Ba': 'Ba_sv', 'La': 'La', 'Ce': 'Ce',
    'Pr': 'Pr_3', 'Nd': 'Nd_3', 'Pm': 'Pm_3', 'Sm': 'Sm_3', 'Eu': 'Eu_2',
    'Gd': 'Gd_3', 'Tb': 'Tb_3', 'Dy': 'Dy_3', 'Ho': 'Ho_3', 'Er': 'Er_3',
    'Tm': 'Tm_3', 'Yb': 'Yb_2', 'Lu': 'Lu_3', 'Hf': 'Hf_pv', 'Ta': 'Ta_pv',
    'W': 'W_sv', 'Re': 'Re', 'Os': 'Os', 'Ir': 'Ir', 'Pt': 'Pt', 'Au': 'Au',
    'Hg': 'Hg', 'Tl': 'Tl_d', 'Pb': 'Pb_d', 'Bi': 'Bi_d', 'Po': 'Po_d',
    'At': 'At', 'Rn': 'Rn', 'Fr': 'Fr_sv', 'Ra': 'Ra_sv', 'Ac': 'Ac',
    'Th': 'Th', 'Pa': 'Pa', 'U': 'U', 'Np': 'Np', 'Pu': 'Pu', 'Am': 'Am',
    'Cm': 'Cm',
}


def make_potcar(elements_string, output_path, potcar_dir=None, clean=False):
    """
    按顺序拼接各元素的赝势，生成 POTCAR 文件。

    elements_string: 元素字串，例如 POSCAR 第 6 行的 "Bi Mo O"
    output_path:     输出路径。既可以是文件，例如 "D:/work/POTCAR"；
                     也可以是文件夹，例如 "D:/work/gama"（会自动写成里面的 POTCAR）
    potcar_dir:      赝势数据库路径，默认用脚本里的 POTCAR_DIR
    clean:           True 时，删除各元素 POTCAR 中所有包含 SHA256 或 COPYR 的行
    """
    if potcar_dir is None:
        potcar_dir = POTCAR_DIR
    # 1. 把字串切开，得到元素列表，例如 ["Bi", "Mo", "O"]
    #    strip() 去掉两边的空格；split() 不带参数时，
    #    不管元素之间是 1 个空格、2 个空格还是制表符，都能正确切开
    elements_string = elements_string.strip()
    elements = elements_string.split()

    # 2. 把每个元素换成 POTCAR 数据库里的标签
    labels = []
    for element in elements:
        if element not in ELEMENTS_DICT:
            print("错误：字典里没有元素 " + element)
            return
        label = ELEMENTS_DICT[element]
        labels.append(label)

    # 3. 按标签顺序，把数据库里的 POTCAR 内容一段一段读出来拼在一起
    content = ""
    for label in labels:
        potcar_file = os.path.join(potcar_dir, label, "POTCAR")
        try:
            f = open(potcar_file, "r", encoding="utf8")
            text = f.read()
            f.close()
        except FileNotFoundError:
            print("错误：找不到文件 " + potcar_file)
            return
        # clean=True 时，删除本元素 POTCAR 中所有包含 SHA256 或 COPYR 的行
        if clean:
            text = "\n".join(
                line for line in text.split("\n")
                if "SHA256" not in line and "COPYR" not in line
            )
        content = content + text
        print(label + " 读取完毕！")

    # 4. 确定真正的输出文件路径
    #    如果传进来的是文件夹（例如 ...\gama），就在它里面生成 POTCAR；
    #    如果传进来的是文件，就直接写到该文件。
    if os.path.isdir(output_path) or output_path.endswith("\\") or output_path.endswith("/"):
        output_path = os.path.join(output_path, "POTCAR")

    # 5. 把拼好的内容写到输出路径
    try:
        f = open(output_path, "w", encoding="utf8")
        f.write(content)
        f.close()
    except FileNotFoundError:
        print("错误：输出文件夹不存在，请先建好文件夹 " + os.path.dirname(output_path))
        return

    print("POTCAR 生成完毕！输出文件：" + output_path)


# ---------------- 命令行入口 ----------------
def _read_poscar_elements(poscar_path):
    """读取 POSCAR 第 6 行的元素字串。"""
    with open(poscar_path, "r", encoding="utf8") as f:
        lines = f.readlines()
    if len(lines) < 6:
        raise ValueError("POSCAR 文件少于 6 行，无法读取元素行")
    return lines[5]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="根据元素字串或 POSCAR 文件生成 VASP 的 POTCAR 文件。"
    )
    parser.add_argument(
        "input",
        help="元素字串（如 \"Bi Mo O\"）或 POSCAR 文件路径",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="输出路径：文件或文件夹（文件夹会自动生成里面的 POTCAR）",
    )
    parser.add_argument(
        "--potcar-dir",
        default=None,
        help="赝势数据库路径，默认使用脚本里的 POTCAR_DIR",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="生成时删除各元素 POTCAR 中所有包含 SHA256 或 COPYR 的行",
    )
    args = parser.parse_args(argv)

    # 如果 input 是一个存在的文件，就当作 POSCAR 读取第 6 行；否则当作元素字串
    if os.path.isfile(args.input):
        elements_string = _read_poscar_elements(args.input)
        print("从 POSCAR 读取元素字串：" + elements_string.strip())
    else:
        elements_string = args.input

    make_potcar(elements_string, args.output, args.potcar_dir, clean=args.clean)


if __name__ == "__main__":
    main()
