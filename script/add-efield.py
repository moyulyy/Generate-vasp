# -*- coding: utf-8 -*-
"""
add-efield.py —— 为 INCAR 追加电场 (EFIELD) 参数

CLI:
    python add-efield.py                       # 打印 electric-field.incar 模板内容
    python add-efield.py -f -0.1               # 指定 EFIELD 值（V/Angstrom）
    python add-efield.py -u 0.3 -d 3           # 从应用电压 0.3V 计算 EFIELD（厚度 3 Angstrom）
    python add-efield.py -f -0.1 -dir 1 -i INCAR  # 指定 EFIELD 值和方向，追加到 INCAR
    python add-efield.py -f -0.05 -d           # 只打印，不写入

参数说明:
    EFIELD: 电场强度 (V/Angstrom)，正值表示正电场，负值表示负电场
    IDIPOL: 电场方向 (1=X, 2=Y, 3=Z)，默认为 3（Z 方向）
    LDIPOL: 偶极层修正开关，默认 .TRUE.

电压与电场的关系:
    EFIELD = -U / thickness
    即：应用电压 U = -EFIELD * thickness
    例如：U=0.3V，厚度=3Å，则 EFIELD = -0.3 / 3 = -0.1 V/Angstrom
"""
import re
import sys
import argparse
from pathlib import Path


def read_template(template_path="ConditionsTemplates/electric-field.incar"):
    """读取 electric-field.incar 模板文件，返回内容。"""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        sys.exit(f"错误：找不到模板文件 {template_path}")
    except Exception as e:
        sys.exit(f"错误：读取 {template_path} 失败 - {e}")


def modify_efield(content, efield_value, idipol=None):
    """
    修改内容中的 EFIELD 和 IDIPOL 值。

    Args:
        content: INCAR 文件内容字符串
        efield_value: 新的 EFIELD 值（浮点数，单位 V/Angstrom），可选
        idipol: 电场方向 (1=X, 2=Y, 3=Z)，可选

    Returns:
        修改后的内容
    """
    new_content = content

    # 修改 EFIELD
    if efield_value is not None:
        pattern_efield = r'(EFIELD\s*=\s*)[-+]?[\d.eE+-]+'
        replacement_efield = f'EFIELD = {efield_value:8g}'
        modified_content = re.sub(pattern_efield, replacement_efield, new_content, flags=re.IGNORECASE)

        if modified_content == new_content:
            # 如果未找到 EFIELD，则在文件末尾追加
            new_content = new_content.rstrip() + f"\n   EFIELD = {efield_value:8g}\n"
        else:
            new_content = modified_content

    # 修改 IDIPOL（如果指定）
    if idipol is not None:
        pattern_idipol = r'(IDIPOL\s*=\s*)\d+'
        replacement_idipol = f'IDIPOL = {idipol:3d}'
        modified_content = re.sub(pattern_idipol, replacement_idipol, new_content, flags=re.IGNORECASE)

        if modified_content != new_content:
            new_content = modified_content

    return new_content


def calculate_efield_from_voltage(voltage, thickness):
    """
    从应用电压和厚度计算 EFIELD。

    关系式：EFIELD = -U / thickness
    例如：U = 0.3V，厚度 = 3Å，则 EFIELD = -0.1 V/Angstrom

    Args:
        voltage: 应用电压 (V)
        thickness: 电场作用层厚度 (Angstrom)

    Returns:
        EFIELD 值
    """
    if thickness == 0:
        sys.exit("错误：厚度不能为 0")

    efield = -voltage / thickness
    return efield


def append_to_incar(incar_path, efield_content, dry_run=False):
    """
    将修改后的内容追加到 INCAR 文件。

    Args:
        incar_path: INCAR 文件路径
        efield_content: 包含 EFIELD 的内容
        dry_run: 为 True 时只打印，不实际写入

    Returns:
        成功返回 True，失败返回 False
    """
    if dry_run:
        print(efield_content)
        return True

    try:
        with open(incar_path, 'a', encoding='utf-8') as f:
            f.write("\n" + efield_content)
        return True
    except Exception as e:
        print(f"错误：写入 {incar_path} 失败 - {e}", file=sys.stderr)
        return False


def get_script_dir():
    """获取脚本所在目录的父目录（项目根目录）。"""
    script_dir = Path(__file__).parent.absolute()
    return script_dir.parent


def main(argv=None):
    p = argparse.ArgumentParser(
        description="为 INCAR 追加电场 (EFIELD) 参数",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 打印 electric-field.incar 模板
  python add-efield.py

  # 指定 EFIELD 为 -0.1 V/Angstrom，追加到 INCAR
  python add-efield.py -f -0.1 -i INCAR

  # 从应用电压 0.3 V，厚度 3 Angstrom 计算 EFIELD
  python add-efield.py -u 0.3 -t 3.0 -i INCAR

  # 指定 EFIELD 和方向（Z 方向），只打印不写入
  python add-efield.py -f -0.1 -dir 3 -d

  # 在 X 方向施加电场 -0.05 V/Angstrom
  python add-efield.py -f -0.05 -dir 1 -i INCAR
        """
    )

    p.add_argument(
        "-f", "--efield", type=float, default=None,
        help="直接指定 EFIELD 值 (V/Angstrom)"
    )
    p.add_argument(
        "-u", "--voltage", type=float, default=None,
        help="应用电压 (V)，需配合 -t/--thickness 使用"
    )
    p.add_argument(
        "-t", "--thickness", type=float, default=None,
        help="电场作用层厚度 (Angstrom)，配合 -u/--voltage 使用"
    )
    p.add_argument(
        "-dir", "--idipol", type=int, default=None,
        choices=[1, 2, 3],
        help="电场方向：1=X, 2=Y, 3=Z，默认为模板中的值"
    )
    p.add_argument(
        "-i", "--incar", default=None,
        help="INCAR 文件路径，指定时追加参数，否则只打印"
    )
    p.add_argument(
        "-t_template", "--template", default=None,
        help="electric-field.incar 模板文件路径"
    )
    p.add_argument(
        "-d", "--dry-run", action="store_true",
        help="只打印，不写入 INCAR"
    )

    args = p.parse_args(argv)

    # 确定模板路径
    if args.template:
        template_path = args.template
    else:
        project_root = get_script_dir()
        template_path = project_root / "ConditionsTemplates" / "electric-field.incar"

    # 读取模板
    content = read_template(str(template_path))

    # 确定 EFIELD 值
    efield = None
    if args.voltage is not None:
        if args.thickness is None:
            sys.exit("错误：指定 -u/--voltage 时必须提供 -t/--thickness")
        efield = calculate_efield_from_voltage(args.voltage, args.thickness)
        print(f"# 从电压 {args.voltage:.3f} V，厚度 {args.thickness:.2f} Å 计算得 EFIELD = {efield:.4g} V/Angstrom",
              file=sys.stderr)
    elif args.efield is not None:
        efield = args.efield

    # 修改内容
    if efield is not None:
        content = modify_efield(content, efield, args.idipol)
    elif args.idipol is not None:
        content = modify_efield(content, None, args.idipol)

    # 输出或追加
    if args.incar:
        if not append_to_incar(args.incar, content, args.dry_run):
            sys.exit(1)
        if not args.dry_run:
            print(f"# 已追加电场参数到 {args.incar}", file=sys.stderr)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
