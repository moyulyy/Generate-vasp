# -*- coding: utf-8 -*-
"""VS Code 扩展的 Python 常驻服务。

通过 stdio 逐行 JSON 通信：
  请求  {"id": 1, "method": "loadStructure", "params": {...}}
  响应  {"id": 1, "ok": true, "result": {...}}
  事件  {"event": "llmProgress", "msg": "第 1 轮请求"}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def locate_root() -> Path:
    """优先使用随扩展打包的 resources/，开发时回退到仓库根目录。"""
    candidates = [
        HERE / "resources",
        HERE.parent.parent,
    ]
    for candidate in candidates:
        if (candidate / "gui" / "jobgen.py").is_file():
            return candidate
    raise RuntimeError(
        "找不到 gui/jobgen.py。请运行 `npm run sync` 同步资源，"
        f"或确认扩展目录完整（已检查：{', '.join(map(str, candidates))}）"
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Generate-VASP VS Code backend")
    parser.add_argument("--llm-config", default="", help="llm_config.json 路径")
    parser.add_argument("--potcar-dir", default="", help="赝势库目录")
    parser.add_argument("--boundary", action="store_true", default=True)
    parser.add_argument("--no-boundary", dest="boundary", action="store_false")
    parser.add_argument("--bonds", default="half", choices=("off", "half", "ghost"))
    return parser.parse_args()


ARGS = parse_args()
if ARGS.llm_config:
    os.environ["LLM_CONFIG"] = str(Path(ARGS.llm_config).expanduser())

ROOT = locate_root()
sys.path.insert(0, str(ROOT / "gui"))
sys.path.insert(0, str(HERE))

_write_lock = threading.Lock()
_controller = None


def json_default(value):
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def send(payload: dict) -> None:
    line = json.dumps(payload, ensure_ascii=False, default=json_default)
    with _write_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def get_controller():
    global _controller
    if _controller is None:
        import controller as controller_module  # 延迟导入，便于先返回依赖检查
        _controller = controller_module.Controller(
            emit=send,
            potcar_dir=ARGS.potcar_dir,
            boundary=ARGS.boundary,
            bonds=ARGS.bonds,
        )
    return _controller


def env_info() -> dict:
    info = {
        "root": str(ROOT),
        "python": sys.version.split()[0],
        "pythonExecutable": sys.executable,
        "gui": str(ROOT / "gui"),
        "script": str(ROOT / "script"),
        "resources": str(HERE / "resources"),
        "ok": True,
    }
    try:
        import numpy
        import ase
        info["numpy"] = numpy.__version__
        info["ase"] = ase.__version__
    except Exception as exc:
        info.update({"ok": False, "error": f"缺少 Python 依赖：{exc}",
                     "install": f'"{sys.executable}" -m pip install numpy ase spglib seekpath'})
        return info
    for name in ("spglib", "seekpath"):
        try:
            module = __import__(name)
            info[name] = getattr(module, "__version__", "installed")
        except Exception:
            info[name] = None
    try:
        ctrl = get_controller()
        info["potcarDir"] = ctrl.options["potcarDir"]
        info["potcarExists"] = Path(ctrl.options["potcarDir"]).is_dir()
        info["llmConfigured"] = ctrl._llm_state()["configured"]
        info["templates"] = {
            "incar": (ROOT / "IncarTemplates").is_dir(),
            "conditions": (ROOT / "ConditionsTemplates").is_dir(),
        }
    except Exception as exc:
        info.update({"ok": False, "error": str(exc)})
    return info


def handle(method: str, params: dict):
    if method == "check":
        return env_info()
    if method == "shutdown":
        return {"message": "bye"}

    ctrl = get_controller()
    known = params.get("knownStructureRevision")

    with ctrl.lock:
        if method == "getState":
            return ctrl.state(known)
        if method == "loadStructure":
            path = params.get("path")
            if not path:
                raise ValueError("缺少结构文件路径")
            state = ctrl.load_structure(str(path))
            notes = state["summary"].get("notes") or []
            message = f"已加载 {Path(path).name}"
            if notes:
                message += " · " + " · ".join(notes)
            return {"state": state, "message": message}
        if method == "clearStructure":
            return {"state": ctrl.clear_structure(), "message": "已移除结构"}
        if method == "applyOptions":
            ctrl.apply_options(params.get("options"))
            return ctrl.state(known)
        if method == "setPotcarLabel":
            ctrl.set_potcar_label(params.get("element", ""), params.get("label", ""))
            return ctrl.state(known)
        if method == "editMask":
            ctrl.edit_mask(params.get("indices") or [], params.get("action") or "toggle")
            return ctrl.state(known)
        if method == "editAll":
            ctrl.edit_all(params.get("action") or "fix")
            return ctrl.state(known)
        if method == "editTyped":
            message = ctrl.edit_typed(params.get("text") or "", params.get("action") or "fix")
            return {"state": ctrl.state(known), "message": message}
        if method == "fixBottomLayers":
            message = ctrl.fix_bottom_layers(params.get("count") or 1)
            return {"state": ctrl.state(known), "message": message}
        if method == "undo":
            ctrl.undo()
            return ctrl.state(known)
        if method == "redo":
            ctrl.redo()
            return ctrl.state(known)
        if method == "setView":
            return {"view": ctrl.set_view(params.get("name") or "front")}
        if method == "editFile":
            file = params.get("file")
            if file not in {"INCAR", "KPOINTS", "POSCAR", "POTCAR", "lobsterin", "OPTCELL"}:
                raise ValueError(f"未知文件：{file}")
            ctrl.edit_file(file, params.get("text") or "")
            return ctrl.state(known)
        if method == "revertFile":
            file = params.get("file")
            ctrl.revert_file(file)
            return {"state": ctrl.state(known), "message": f"{file} 已恢复为自动生成内容"}
        if method == "saveFile":
            result = ctrl.save_file(params.get("file"), bool(params.get("force")))
            result["state"] = ctrl.state(known)
            return result
        if method == "saveFileAs":
            result = ctrl.save_file_as(params.get("file"), params.get("path") or "")
            result["state"] = ctrl.state(known)
            return result
        if method == "generate":
            result = ctrl.generate(params.get("files"), bool(params.get("force")))
            result["state"] = ctrl.state(known)
            return result
        if method == "startLLM":
            ctrl.start_llm(params.get("hint") or "")
            return ctrl.state(known)
        if method == "cancelLLM":
            ctrl.cancel_llm(params.get("note") or "已取消")
            return ctrl.state(known)
        raise ValueError(f"未知方法：{method}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        req_id = None
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("请求必须是 JSON 对象")
            req_id = request.get("id")
            method = request.get("method") or ""
            params = request.get("params") or {}
            result = handle(method, params)
            send({"id": req_id, "ok": True, "result": result})
            if method == "shutdown":
                break
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            debug = os.environ.get("GENERATE_VASP_DEBUG")
            print(f"[generate-vasp] {message}", file=sys.stderr)
            if debug:
                traceback.print_exc(file=sys.stderr)
            send({"id": req_id, "ok": False, "error": message,
                  "traceback": traceback.format_exc() if debug else None})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
