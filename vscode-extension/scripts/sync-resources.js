#!/usr/bin/env node
/*
 * 把仓库中的 Python 逻辑、模板与前端静态资源同步到扩展目录，
 * 使 `vsce package` 生成的 VSIX 自带完整后端。
 *
 * 开发时即使没有运行本脚本，python/service.py 也会回退到仓库根目录的 gui/ 与 script/。
 */
"use strict";

const fs = require("fs");
const path = require("path");

const EXT = path.resolve(__dirname, "..");
const REPO = path.resolve(EXT, "..");
const RES = path.join(EXT, "python", "resources");

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyFile(src, dest) {
  ensureDir(path.dirname(dest));
  fs.copyFileSync(src, dest);
}

function copyTree(src, dest, accept = () => true) {
  if (!fs.existsSync(src)) return;
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    for (const name of fs.readdirSync(src)) {
      if (name === ".git" || name === "node_modules" || name === "__pycache__" || name === "tests") continue;
      copyTree(path.join(src, name), path.join(dest, name), accept);
    }
    return;
  }
  if (accept(src)) copyFile(src, dest);
}

function copyPyTree(src, dest) {
  copyTree(src, dest, (file) => file.endsWith(".py") || file.endsWith(".json"));
}

if (!fs.existsSync(path.join(REPO, "gui", "jobgen.py"))) {
  console.error("找不到仓库根目录的 gui/jobgen.py；请在 Generate-Input 仓库内运行本脚本。");
  process.exit(1);
}

// 先清空再复制，避免源仓库删除文件后 resources 里残留旧副本
fs.rmSync(RES, { recursive: true, force: true });
ensureDir(RES);

// GUI 核心逻辑（不依赖 PySide6）
for (const name of ["structure.py", "jobgen.py", "lobster.py", "camera_views.py"]) {
  copyFile(path.join(REPO, "gui", name), path.join(RES, "gui", name));
}

// 命令行脚本：保持原有目录结构，jobgen.py 通过相对路径加载
copyPyTree(path.join(REPO, "script"), path.join(RES, "script"));

// INCAR 与条件模板
copyTree(path.join(REPO, "IncarTemplates"), path.join(RES, "IncarTemplates"));
copyTree(path.join(REPO, "ConditionsTemplates"), path.join(RES, "ConditionsTemplates"));

// 配置示例与许可证
copyFile(path.join(REPO, "llm_config.example.json"), path.join(RES, "llm_config.example.json"));
copyFile(path.join(REPO, "LICENSE"), path.join(RES, "LICENSE"));

// 前端资源
copyFile(path.join(REPO, "gui", "3Dmol-min.js"), path.join(EXT, "media", "3Dmol-min.js"));
copyFile(path.join(REPO, "gui", "assets", "app.png"), path.join(EXT, "media", "app.png"));

fs.writeFileSync(
  path.join(RES, ".sync-stamp"),
  new Date().toISOString() + "\n",
  "utf8"
);

console.log("已同步资源到 " + path.relative(EXT, RES));
