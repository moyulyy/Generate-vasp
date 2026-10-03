# -*- coding: utf-8 -*-
"""
add-cp-vasp.py —— 为 INCAR 追加恒电势 (CP-VASP) 参数，并修改 TARGETMU

CLI:
    python add-cp-vasp.py                   # 打印 cp-vasp.incar 模板内容
    python add-cp-vasp.py -mu -4.5          # 指定 TARGETMU 值，打印
    python add-cp-vasp.py -mu -4.5 -i INCAR  # 指定 TARGETMU，追加到 INCAR
    python add-cp-vasp.py -e 0.5            # 使用电极电势计算 TARGETMU（相对SHE）
    python add-cp-vasp.py -d                # 只打印，不写入
    python add-cp-vasp.py -t path/to/incar  # 指定模板文件路径

TARGETMU 的确定方法:
    方法1: 直接指定 -mu <value>，使用给定值
    方法2: 通过电极电势计算 -e <potential_vs_SHE>
           TARGETMU = -4.43 - potential_vs_SHE（相对水中 SHE）
"""
import re
import sys
import argparse
import os
from pathlib import Path


def read_template(template_path="ConditionsTemplates/cp-vasp.incar"):
    """读取 cp-vasp.incar 模板文件，返回内容。"""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        sys.exit(f"错误：找不到模板文件 {template_path}")
    except Exception as e:
        sys.exit(f"错误：读取 {template_path} 失败 - {e}")


def modify_targetmu(content, targetmu_value):
    """
    修改内容中的 TARGETMU 值。

    Args:
        content: INCAR 文件内容字符串
        targetmu_value: 新的 TARGETMU 值（浮点数）

    Returns:
        修改后的内容
    """
    pattern = r'(TARGETMU\s*=\s*)[-+]?[\d.eE+-]+'
    replacement = f'TARGETMU = {targetmu_value:8g}'

    new_content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)
    if new_content == content:
        # 如果未找到 TARGETMU，则在文件末尾追加
        new_content = content.rstrip() + f"\n   TARGETMU = {targetmu_value:8g}\n"

    return new_content


def calculate_targetmu_from_potential(potential_vs_SHE, reference=None):
    """
    从电极电势计算 TARGETMU。

    VASP 中 TARGETMU 对应化学势 (μ)，与电极电势的关系为：
    μ = μ_SHE + (-1) * E_potential

    其中 μ_SHE ≈ -4.43 eV（水环境中 SHE 的化学势参考值）

    Args:
        potential_vs_SHE: 相对 SHE 的电极电势 (V)
        reference: SHE 的化学势参考值 (eV)，默认 -4.43

    Returns:
        TARGETMU 值
    """
    if reference is None:
        reference = -4.43

    targetmu = reference - potential_vs_SHE
    return targetmu


def append_to_incar(incar_path, targetmu_content, dry_run=False):
    """
    将修改后的内容追加到 INCAR 文件。

    Args:
        incar_path: INCAR 文件路径
        targetmu_content: 包含 TARGETMU 的内容
        dry_run: 为 True 时只打印，不实际写入

    Returns:
        成功返回 True，失败返回 False
    """
    if dry_run:
        print(targetmu_content)
        return True

    try:
        with open(incar_path, 'a', encoding='utf-8') as f:
            f.write("\n" + targetmu_content)
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
        description="为 INCAR 追加恒电势 (CP-VASP) 参数，并修改 TARGETMU",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 打印 cp-vasp.incar 模板
  python add-cp-vasp.py

  # 指定 TARGETMU 为 -4.5 eV，追加到 INCAR
  python add-cp-vasp.py -mu -4.5 -i INCAR

  # 从电极电势 0.5 V (vs SHE) 计算 TARGETMU
  python add-cp-vasp.py -e 0.5 -i INCAR

  # 只打印修改后的内容，不写入
  python add-cp-vasp.py -mu -4.5 -d
        """
    )

    p.add_argument(
        "-mu", "--targetmu", type=float, default=None,
        help="直接指定 TARGETMU 值 (eV)，默认为模板中的值"
    )
    p.add_argument(
        "-e", "--electrode", type=float, default=None,
        help="从电极电势计算 TARGETMU (V vs SHE)，优先级高于 -mu"
    )
    p.add_argument(
        "-ref", "--reference", type=float, default=-4.43,
        help="SHE 化学势参考值 (eV)，默认 -4.43 (水环境)"
    )
    p.add_argument(
        "-i", "--incar", default=None,
        help="INCAR 文件路径，指定时追加参数，否则只打印"
    )
    p.add_argument(
        "-t", "--template", default=None,
        help="cp-vasp.incar 模板文件路径，默认为 ConditionsTemplates/cp-vasp.incar"
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
        template_path = project_root / "ConditionsTemplates" / "cp-vasp.incar"

    # 读取模板
    content = read_template(str(template_path))

    # 确定 TARGETMU 值
    targetmu = None
    if args.electrode is not None:
        targetmu = calculate_targetmu_from_potential(args.electrode, args.reference)
        print(f"# 从电极电势 {args.electrode:.2f} V (vs SHE) 计算得 TARGETMU = {targetmu:.4g} eV",
              file=sys.stderr)
    elif args.targetmu is not None:
        targetmu = args.targetmu

    # 修改内容
    if targetmu is not None:
        content = modify_targetmu(content, targetmu)

    # 输出或追加
    if args.incar:
        if not append_to_incar(args.incar, content, args.dry_run):
            sys.exit(1)
        if not args.dry_run:
            print(f"# 已追加 CP-VASP 参数到 {args.incar}", file=sys.stderr)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
