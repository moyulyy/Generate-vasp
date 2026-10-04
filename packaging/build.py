# -*- coding: utf-8 -*-
"""打包便携版：PyInstaller onedir + 可编辑的模板与脚本，输出 dist/Generate-VASP-<版本>-win64.zip。

用法（在已安装依赖与 PyInstaller 的环境中）:
    python packaging/build.py [版本号]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "gui"
NAME = "Generate-VASP"
VERSION = sys.argv[1] if len(sys.argv) > 1 else "1.0.1"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / NAME

# script/ 下的脚本在运行时按文件路径动态加载，PyInstaller 分析不到它们的依赖，需要显式列出
HIDDEN = [
    "argparse", "contextlib", "dataclasses", "difflib", "html", "itertools", "math", "subprocess", "tempfile",
    "typing", "urllib.error", "urllib.request", "collections",
    "ase.dft.kpoints", "ase.io.vasp", "ase.io.cif", "ase.neighborlist", "ase.constraints",
    "ase.lattice", "ase.spacegroup", "spglib", "seekpath",
]
COLLECT = ["ase.io", "ase.lattice", "ase.dft", "seekpath", "spglib"]
# ase 的部分功能在运行时读取包内数据文件（如 ase/spacegroup/spacegroup.dat），
# --collect-submodules 不会带上它们，必须显式收集，否则打包后会报 Errno 2。
DATA = ["ase.spacegroup"]
EXCLUDE = ["tkinter", "matplotlib", "IPython", "pytest", "ase.gui", "ase.test", "PySide6.Qt3DCore",
           "PySide6.QtQuick3D", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization"]
# 放在 exe 旁边、用户可直接修改的文件
EDITABLE = ["IncarTemplates", "ConditionsTemplates", "script", "llm_config.example.json", "LICENSE"]


def run_pyinstaller():
    sep = ";" if sys.platform == "win32" else ":"
    args = [sys.executable, "-m", "PyInstaller", str(GUI / "incar_gui.py"),
            "--name", NAME, "--noconfirm", "--clean", "--windowed", "--onedir",
            "--icon", str(GUI / "assets" / "app.ico"),
            "--workpath", str(BUILD), "--distpath", str(DIST), "--specpath", str(BUILD),
            "--paths", str(GUI)]
    for f in ("viewer.html", "viewer.js", "3Dmol-min.js"):
        args += ["--add-data", f"{GUI / f}{sep}."]
    args += ["--add-data", f"{GUI / 'assets'}{sep}assets"]
    for m in HIDDEN:
        args += ["--hidden-import", m]
    for m in COLLECT:
        args += ["--collect-submodules", m]
    for m in DATA:
        args += ["--collect-data", m]
    for m in EXCLUDE:
        args += ["--exclude-module", m]
    subprocess.run(args, check=True)


def prune():
    """去掉用不到的 Qt 模块：按 PE 导入表从 exe / .pyd / 保留的插件出发求依赖闭包，删除闭包外的 Qt6*.dll。"""
    import pefile
    internal = APP / "_internal"
    qt = internal / "PySide6"
    for d in ("qml", "plugins/qmltooling", "plugins/position"):
        shutil.rmtree(qt / d, ignore_errors=True)
    for f in qt.glob("resources/*.debug.pak"):
        f.unlink()
    keep_locale = {"en-US.pak", "zh-CN.pak"}
    for f in (qt / "translations" / "qtwebengine_locales").glob("*.pak"):
        if f.name not in keep_locale:
            f.unlink()
    for f in (qt / "translations").glob("*.qm"):
        if not f.stem.endswith(("_zh_CN", "_en")):
            f.unlink()

    dlls = {p.name.lower(): p for p in internal.rglob("*.dll")}
    roots = [APP / f"{NAME}.exe", qt / "QtWebEngineProcess.exe", *internal.rglob("*.pyd"), *qt.glob("plugins/**/*.dll")]
    seen, todo = set(), list(roots)
    while todo:
        path = todo.pop()
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories([pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                       pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        except pefile.PEFormatError:
            continue
        entries = getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])
        for entry in entries:
            name = entry.dll.decode(errors="ignore").lower()
            if name in dlls and name not in seen:
                seen.add(name)
                todo.append(dlls[name])
        pe.close()
    removed = [p for p in qt.glob("Qt6*.dll") if p.name.lower() not in seen]
    for p in removed:
        p.unlink()
    print(f"已移除 {len(removed)} 个未使用的 Qt 模块")


def copy_editable():
    ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "tests", "llm_config.json", ".gitignore")
    for name in EDITABLE:
        src, dst = ROOT / name, APP / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    (APP / "POTCAR").mkdir(exist_ok=True)
    (APP / "POTCAR" / "把赝势库放在这里.txt").write_text(
        "赝势文件受 VASP 许可约束，不随程序分发。\n"
        "目录结构：POTCAR/标签/POTCAR，例如 POTCAR/Ni_pv/POTCAR。\n"
        "也可以在程序「K 点与赝势」中把赝势库指向其他目录。\n", encoding="utf-8")
    shutil.copy2(Path(__file__).with_name("使用说明.txt"), APP / "使用说明.txt")
    # 本机调试时复用开发环境已配好的 llm_config.json，让便携版与源码运行行为一致。
    # 该文件不会进入发行压缩包（见 make_zip），不会随发布泄露密钥。
    local_cfg = ROOT / "llm_config.json"
    if local_cfg.is_file():
        shutil.copy2(local_cfg, APP / "llm_config.json")


def make_zip() -> Path:
    out = DIST / f"{NAME}-{VERSION}-win64.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(APP.rglob("*")):
            if f.name == "llm_config.json":  # 密钥文件只留在本机目录，不进压缩包
                continue
            z.write(f, Path(NAME) / f.relative_to(APP))
        leaked = [n for n in z.namelist() if n.endswith("llm_config.json")]
    if leaked:
        out.unlink(missing_ok=True)
        raise SystemExit(f"压缩包中不应包含密钥文件：{leaked}")
    return out


if __name__ == "__main__":
    run_pyinstaller()
    prune()
    copy_editable()
    zip_path = make_zip()
    print(f"\n便携版目录：{APP}\n压缩包：{zip_path}（{zip_path.stat().st_size / 2**20:.1f} MB）")
