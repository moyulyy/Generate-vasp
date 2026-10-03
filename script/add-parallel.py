# -*- coding: utf-8 -*-
"""
add-parallel.py —— 为 INCAR 追加并行化 (KPAR / NPAR) 参数

CLI:
    python add-parallel.py                     # 打印 parallel.incar 模板内容
    python add-parallel.py -kpar 4             # 指定 KPAR 值
    python add-parallel.py -npar 8             # 指定 NPAR 值
    python add-parallel.py -kpar 4 -npar 8 -i INCAR  # 同时指定 KPAR 和 NPAR，追加到 INCAR
    python add-parallel.py -n 32 -k 8          # 从核心数 32 和 k点数 8 自动计算参数
    python add-parallel.py -kpar 2 -npar 4 -d  # 只打印，不写入

参数说明:
    KPAR: k点并行化参数，应该不超过 k点数量，通常为 k点数量的因子
    NPAR: 带结构并行化参数，应该能整除核心总数，通常为 2 的倍数

自动优化:
    -n/--ncores: 计算核心总数
    -k/--nkpts:  k点总数
    脚本将自动计算最优的 KPAR 和 NPAR 组合，使得 KPAR * NPAR ≈ 核心数
"""
import re
import sys
import argparse
import math
from pathlib import Path


def read_template(template_path="ConditionsTemplates/parallel.incar"):
    """读取 parallel.incar 模板文件，返回内容。"""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        sys.exit(f"错误：找不到模板文件 {template_path}")
    except Exception as e:
        sys.exit(f"错误：读取 {template_path} 失败 - {e}")


def modify_parallel_params(content, kpar=None, npar=None):
    """
    修改内容中的 KPAR 和 NPAR 值。

    Args:
        content: INCAR 文件内容字符串
        kpar: 新的 KPAR 值（整数），可选
        npar: 新的 NPAR 值（整数），可选

    Returns:
        修改后的内容
    """
    new_content = content

    # 修改 KPAR
    if kpar is not None:
        pattern_kpar = r'(KPAR\s*=\s*)\d+'
        replacement_kpar = f'KPAR = {kpar:3d}'
        modified_content = re.sub(pattern_kpar, replacement_kpar, new_content, flags=re.IGNORECASE)
        if modified_content == new_content:
            # 如果未找到 KPAR，则追加
            new_content = new_content.rstrip() + f"\n   KPAR = {kpar:3d}\n"
        else:
            new_content = modified_content

    # 修改 NPAR
    if npar is not None:
        pattern_npar = r'(NPAR\s*=\s*)\d+'
        replacement_npar = f'NPAR = {npar:3d}'
        modified_content = re.sub(pattern_npar, replacement_npar, new_content, flags=re.IGNORECASE)
        if modified_content == new_content:
            # 如果未找到 NPAR，则追加
            new_content = new_content.rstrip() + f"\n   NPAR = {npar:3d}\n"
        else:
            new_content = modified_content

    return new_content


def get_divisors(n):
    """获取 n 的所有因子，按升序返回。"""
    divisors = []
    for i in range(1, int(math.sqrt(n)) + 1):
        if n % i == 0:
            divisors.append(i)
            if i != n // i:
                divisors.append(n // i)
    return sorted(divisors)


def calculate_optimal_parallel_params(ncores, nkpts=None, prefer_balanced=True):
    """
    从核心数和 k点数自动计算最优的 KPAR 和 NPAR。

    策略:
    1. 获取 ncores 的所有因子对 (a, b)，使得 a * b = ncores
    2. 如果指定了 nkpts，优先选择 KPAR <= nkpts 的组合
    3. 如果 prefer_balanced=True，优先选择 KPAR 和 NPAR 接近的组合

    Args:
        ncores: 计算核心总数
        nkpts: k点总数（可选），用于限制 KPAR 上界
        prefer_balanced: 是否优先选择 KPAR 和 NPAR 接近的组合

    Returns:
        (kpar, npar) 元组
    """
    divisors = get_divisors(ncores)

    # 筛选满足条件的因子对
    candidates = []
    for kpar in divisors:
        npar = ncores // kpar
        # 如果指定了 nkpts，要求 KPAR <= nkpts
        if nkpts is not None and kpar > nkpts:
            continue
        candidates.append((kpar, npar))

    if not candidates:
        sys.exit(f"错误：无法从核心数 {ncores} 和 k点数 {nkpts} 计算并行参数")

    # 选择最优组合
    if prefer_balanced:
        # 选择 KPAR 和 NPAR 最接近的组合
        best = min(candidates, key=lambda x: abs(x[0] - x[1]))
    else:
        # 选择 KPAR 最小的组合（即 NPAR 最大的）
        best = min(candidates, key=lambda x: x[0])

    return best


def append_to_incar(incar_path, parallel_content, dry_run=False):
    """
    将修改后的内容追加到 INCAR 文件。

    Args:
        incar_path: INCAR 文件路径
        parallel_content: 包含 KPAR/NPAR 的内容
        dry_run: 为 True 时只打印，不实际写入

    Returns:
        成功返回 True，失败返回 False
    """
    if dry_run:
        print(parallel_content)
        return True

    try:
        with open(incar_path, 'a', encoding='utf-8') as f:
            f.write("\n" + parallel_content)
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
        description="为 INCAR 追加并行化 (KPAR / NPAR) 参数",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 打印 parallel.incar 模板
  python add-parallel.py

  # 指定 KPAR=4，NPAR=8，追加到 INCAR
  python add-parallel.py -kpar 4 -npar 8 -i INCAR

  # 从核心数 32 自动计算最优并行参数
  python add-parallel.py -n 32 -i INCAR

  # 核心数 32，k点数 8，自动计算最优参数
  python add-parallel.py -n 32 -k 8 -i INCAR

  # 只修改 KPAR，保持 NPAR 不变
  python add-parallel.py -kpar 2 -i INCAR

  # 查看自动计算的结果（不写入）
  python add-parallel.py -n 64 -k 16 -d
        """
    )

    p.add_argument(
        "-kpar", "--kpar", type=int, default=None,
        help="直接指定 KPAR 值"
    )
    p.add_argument(
        "-npar", "--npar", type=int, default=None,
        help="直接指定 NPAR 值"
    )
    p.add_argument(
        "-n", "--ncores", type=int, default=None,
        help="计算核心总数，用于自动计算 KPAR 和 NPAR"
    )
    p.add_argument(
        "-k", "--nkpts", type=int, default=None,
        help="k点总数，用于限制 KPAR 上界（配合 -n 使用）"
    )
    p.add_argument(
        "-b", "--balanced", action="store_true", default=True,
        help="自动计算时优先选择 KPAR 和 NPAR 接近的组合（默认）"
    )
    p.add_argument(
        "-i", "--incar", default=None,
        help="INCAR 文件路径，指定时追加参数，否则只打印"
    )
    p.add_argument(
        "-t", "--template", default=None,
        help="parallel.incar 模板文件路径"
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
        template_path = project_root / "ConditionsTemplates" / "parallel.incar"

    # 读取模板
    content = read_template(str(template_path))

    # 确定 KPAR 和 NPAR
    kpar, npar = None, None

    if args.ncores is not None:
        # 自动计算模式
        kpar, npar = calculate_optimal_parallel_params(args.ncores, args.nkpts, args.balanced)
        if args.nkpts:
            print(f"# 从核心数 {args.ncores}，k点数 {args.nkpts} 计算得 KPAR={kpar}, NPAR={npar}",
                  file=sys.stderr)
        else:
            print(f"# 从核心数 {args.ncores} 计算得 KPAR={kpar}, NPAR={npar}",
                  file=sys.stderr)
    else:
        # 手动指定模式
        if args.kpar is not None:
            kpar = args.kpar
        if args.npar is not None:
            npar = args.npar

    # 修改内容
    if kpar is not None or npar is not None:
        content = modify_parallel_params(content, kpar, npar)

    # 输出或追加
    if args.incar:
        if not append_to_incar(args.incar, content, args.dry_run):
            sys.exit(1)
        if not args.dry_run:
            print(f"# 已追加并行化参数到 {args.incar}", file=sys.stderr)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
