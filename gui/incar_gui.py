# -*- coding: utf-8 -*-
"""VASP 作业文件生成器（PySide6，iOS 风格弹窗）。

用法: python incar_gui.py [CIF / POSCAR]
读取 CIF / POSCAR → 3D 查看并设置固定 / 弛豫原子 → 生成并可编辑 INCAR、KPOINTS、POSCAR、POTCAR，
输出到结构文件所在目录。
"""
from __future__ import annotations

import html
import json
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSettings, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

import jobgen as jg
import lobster as lb
import structure as st
from camera_views import standard_view
from editor import FileEditor
from theme import C, apply_theme
from widgets import (
    Card, DropZone, PopupFrame, ProjectCard, ResultBox, Segmented, SubPanel, Switch, TitleBar, Toast,
    clear_layout, combo, dspin, hline, ispin, label, repolish, set_hint,
)

HERE = Path(__file__).resolve().parent
ICON = HERE / "assets" / "app.ico"
FILES = ["INCAR", "KPOINTS", "POSCAR", "POTCAR"]
EXTRA = ["lobsterin", "OPTCELL"]           # 按开关决定是否生成
ALL_FILES = FILES + EXTRA
MERGED = ["INCAR", "KPOINTS", *EXTRA]      # 手动编辑后，左侧设置的改动仍按三方合并写入
SYSTEMS = [("auto", "自动判别"), ("mole", "分子"), ("slab", "表面"), ("bulk", "体相")]
SPIN_MODES = [("off", "关闭"), ("default", "启发式"), ("llm", "LLM 分析"), ("custom", "按元素指定")]
U_MODES = [("off", "关闭"), ("default", "默认 U 值"), ("custom", "自定义 L / U / J")]
BOND_MODES = [("off", "不显示"), ("half", "半键"), ("ghost", "胞外原子")]
DETECTED = {"bulk": "体相 bulk", "slab": "表面 slab", "mole": "分子 mole", "unknown": "未确定 · 按 slab 处理"}
PLACEHOLDER = "加载结构文件后自动生成"


def setting_row(title: str, control: QWidget):
    """左：标题 + 灰色说明；右：控件。返回 (行, 说明标签)。"""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 4, 0, 4)
    h.setSpacing(16)
    left = QVBoxLayout()
    left.setSpacing(2)
    left.addWidget(label(title, "RowTitle"))
    hint = label("", "Hint", wrap=True)
    hint.setVisible(False)
    left.addWidget(hint)
    h.addLayout(left, 1)
    h.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return w, hint


def ghost(text: str, slot, name: str = "Ghost") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def toggle(text: str, checked: bool, slot) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("FileToggle")
    b.setCheckable(True)
    b.setChecked(checked)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.toggled.connect(slot)
    return b


class Bridge(QObject):
    stateChanged = Signal(str)
    viewRequested = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    @Slot()
    def ready(self):
        self.window.viewer_ready = True
        self.window.show_structure()

    @Slot(str)
    def selectAtoms(self, raw: str):
        data = json.loads(raw)
        if data.get("generation") == self.window.generation:
            self.window.edit_mask(data["indices"], data["action"])

    @Slot(str)
    def requestMode(self, mode: str):
        self.window.mode_seg.buttons[mode].setChecked(True)

    @Slot(str)
    def reportError(self, message: str):
        print(f"3Dmol: {message}", file=sys.stderr)


class LlmSignals(QObject):
    """后台线程 → 界面线程；第一个参数为运行编号，取消或重新开始后旧编号的结果被丢弃。"""
    progress = Signal(int, str)
    done = Signal(int, object)
    failed = Signal(int, str)


class LocalPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, _type, _main):
        return url.scheme() in ("file", "qrc", "about", "data")

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            print(f"JS {Path(source).name}:{line}: {message}", file=sys.stderr)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VASP 作业文件生成器")
        self.setWindowIcon(QIcon(str(ICON)))
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        if jg.FROZEN:  # 便携版：设置写在 exe 旁边，不写注册表
            self.settings = QSettings(str(jg.ROOT / "settings.ini"), QSettings.Format.IniFormat)
        else:
            self.settings = QSettings("Generate-Input", "incar-gui")
        self.atoms = None
        self.mask = np.zeros((0, 3), dtype=bool)
        self.spos = None          # add-spin 的 Poscar 对象，用于体系判别与磁矩
        self.detected = None
        self.source: Path | None = None
        self.generation = 0
        self.viewer_ready = False
        self.undo_stack: list[np.ndarray] = []
        self.redo_stack: list[np.ndarray] = []
        self._spin_plan = None
        self._kp_cache: dict = {}
        self.auto = {f: "" for f in ALL_FILES}        # 按当前参数自动生成的文本
        self.manual = {f: False for f in ALL_FILES}   # 编辑器内容是否为手动编辑
        self.base = {f: "" for f in ALL_FILES}        # 手动编辑所基于的自动生成文本（三方合并的公共祖先）
        self.errors = {f: [] for f in ALL_FILES}
        self.notes = {f: "" for f in ALL_FILES}       # 编辑器顶部的说明
        self.pot_infos: list[dict] = []
        self._llm: dict | None = None                 # 最近一次 LLM 结果 {gen, moments…} 或 {gen, error}
        self._llm_running = False
        self._llm_run = 0                             # 运行编号
        self._llm_gen = -1                            # 正在分析的结构 generation
        self._llm_t0 = 0.0
        self._llm_msg = ""
        self._llm_note = ""                           # 没有可用结果时的状态（已取消、结构已改变…）
        self._llm_sig = LlmSignals()
        self._llm_sig.progress.connect(self._llm_progress)
        self._llm_sig.done.connect(self._llm_done)
        self._llm_sig.failed.connect(self._llm_failed)
        self._clean_before = False
        self.edit_errors = {"POSCAR": [], "POTCAR": []}  # 解析手动编辑内容的错误
        self.disk: dict[str, str] = {}            # 本次会话最后写入磁盘的内容
        self.pot_manual: list[str] | None = None  # 手动编辑的 POTCAR 标签
        self.potcar_full = ""
        self.spin_inputs: dict = {}
        self.u_inputs: dict = {}
        self.pot_inputs: dict[str, QComboBox] = {}

        self.frame = PopupFrame(C.BG, C.LINE)
        self.root = QWidget()
        self.root.setObjectName("Root")
        self.root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.frame.lay.addWidget(self.root)
        v = QVBoxLayout(self.root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._title_bar())
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._settings_pane())
        split.addWidget(self._workspace())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([680, 800])
        v.addWidget(split, 1)
        v.addWidget(self._bottom_bar())
        self.setCentralWidget(self.frame)
        self.toast = Toast(self.root)

        for key, slot in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo),
                          (QKeySequence("Ctrl+Y"), self.redo), (QKeySequence.StandardKey.Open, self._pick_structure),
                          (QKeySequence.StandardKey.Save, self._save_current), (QKeySequence.StandardKey.Find, self._find_current),
                          (QKeySequence("Ctrl+Return"), self.generate)):
            QShortcut(key, self, slot)

        self._poscar_timer = self._debounce(self._apply_poscar_edit, 450)
        self._potcar_timer = self._debounce(self._apply_potcar_edit, 450)
        self._rebuild_editors()
        self.refresh()
        self._place()

    @staticmethod
    def _debounce(slot, ms: int) -> QTimer:
        t = QTimer()
        t.setSingleShot(True)
        t.setInterval(ms)
        t.timeout.connect(slot)
        return t

    def _place(self):
        avail = QApplication.primaryScreen().availableGeometry()
        w, h = min(1500, int(avail.width() * 0.94)), min(940, int(avail.height() * 0.94))
        self.resize(w, h)
        self.setMinimumSize(min(1100, w), min(680, h))
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            on = self.isMaximized()
            self.frame.set_maximized(on)
            self.title.set_maximized(on)
            for w in (self.root, self.bottom):
                w.setProperty("max", on)
                repolish(w)
        super().changeEvent(event)

    # ================================================================ 骨架
    def _title_bar(self):
        self.title = TitleBar()
        self.title.setFixedHeight(60)
        icon = QLabel()
        icon.setPixmap(QIcon(str(ICON)).pixmap(QSize(34, 34), self.devicePixelRatioF()))
        icon.setFixedSize(34, 34)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("VASP 作业文件生成器", "NavTitle"))
        titles.addWidget(label("CIF / POSCAR → 固定原子 → INCAR · KPOINTS · POSCAR · POTCAR", "NavSub"))
        self.title.lay.addWidget(icon)
        self.title.lay.addLayout(titles)
        self.title.finish()
        return self.title

    def _settings_pane(self):
        inner = QWidget()
        inner.setObjectName("Pane")
        body = QVBoxLayout(inner)
        body.setContentsMargins(22, 18, 10, 22)
        body.setSpacing(16)
        for card in (self._structure_card(), self._project_card(), self._electronic_card(),
                     self._kpoint_potcar_card(), self._condition_card()):
            body.addWidget(card)
        body.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setMinimumWidth(620)
        return scroll

    def _workspace(self):
        pane = QWidget()
        pane.setObjectName("Pane")
        v = QVBoxLayout(pane)
        v.setContentsMargins(10, 18, 22, 22)
        card = Card()
        self.tab_seg = Segmented([("structure", "结构")] + [(f, f) for f in ALL_FILES], "structure")
        self.tab_seg.changed.connect(self._switch_tab)
        card.head.insertWidget(0, self.tab_seg)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._structure_page())
        self.editors: dict[str, FileEditor] = {}
        for f in ALL_FILES:
            ed = FileEditor(f, PLACEHOLDER)
            ed.edited.connect(self._on_edited)
            ed.saveRequested.connect(self.save_file)
            ed.saveAsRequested.connect(self.save_file_as)
            ed.revertRequested.connect(self.revert_file)
            ed.copyRequested.connect(self._copy)
            self.editors[f] = ed
            self.stack.addWidget(ed)
        card.body.addWidget(self.stack, 1)
        v.addWidget(card, 1)
        pane.setMinimumWidth(560)
        return pane

    def _structure_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.mode_seg = Segmented([("rotate", "旋转"), ("point", "点选"), ("box", "框选")], "rotate")
        self.mode_seg.changed.connect(lambda m: self._send({"mode": m}))
        self.mode_seg.setToolTip("在 3D 视图中按 1 / 2 / 3 切换，Esc 回到旋转；Ctrl+Z / Ctrl+Y 撤销 / 重做固定操作")
        top.addWidget(self.mode_seg)
        top.addStretch()
        self.view_buttons = [ghost(text, lambda _=False, n=name: self.set_view(n))
                             for text, name in (("正视", "front"), ("侧视", "right"), ("俯视", "top"))]
        for b in self.view_buttons:
            top.addWidget(b)
        v.addLayout(top)

        pbc = QHBoxLayout()
        pbc.setSpacing(8)
        pbc.addWidget(label("周期性", "RowKey"))
        self.boundary_btn = toggle("边界等价原子", self.settings.value("boundary", True, bool), self._display_changed)
        self.boundary_btn.setToolTip("在晶胞面 / 棱 / 角上显示分数坐标 0 与 1 处的等价原子")
        pbc.addWidget(self.boundary_btn)
        pbc.addSpacing(10)
        pbc.addWidget(label("跨边界成键", "RowKey"))
        self.bond_seg = Segmented(BOND_MODES, str(self.settings.value("bonds", "half")))
        self.bond_seg.changed.connect(self._display_changed)
        self.bond_seg.setToolTip("半键：画到两原子中点；胞外原子：显示与胞内原子成键的周期像（浅色）")
        pbc.addWidget(self.bond_seg)
        pbc.addStretch()
        v.addLayout(pbc)

        frame = QFrame()
        frame.setObjectName("ViewerFrame")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        self.web = QWebEngineView()
        self.web.setPage(LocalPage(self.web))
        self.bridge = Bridge(self)
        channel = QWebChannel(self.web.page())
        channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(channel)
        self.web.setUrl(QUrl.fromLocalFile(str(HERE / "viewer.html")))
        self.web.setAcceptDrops(False)
        fl.addWidget(self.web)
        v.addWidget(frame, 1)

        tools = QFrame()
        tools.setObjectName("SubPanel")
        tg = QVBoxLayout(tools)
        tg.setContentsMargins(14, 10, 14, 10)
        tg.setSpacing(8)
        self.fix_stats = label("", "RowTitle")
        row1 = QHBoxLayout()
        row1.addWidget(self.fix_stats)
        row1.addStretch()
        self.edit_buttons = [ghost("全部固定", lambda: self.edit_all("fix")),
                             ghost("全部弛豫", lambda: self.edit_all("release")),
                             ghost("↶ 撤销", self.undo), ghost("↷ 重做", self.redo)]
        for b in self.edit_buttons:
            row1.addWidget(b)
        tg.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.layer_n = ispin(1, 99, 2, 56)
        self.layer_info = label("", "Hint")
        self.index_edit = QLineEdit()
        self.index_edit.setPlaceholderText("编号，如 1-8, 12")
        self.index_edit.returnPressed.connect(lambda: self.edit_typed("fix"))
        row2.addWidget(label("底部", "RowKey"))
        row2.addWidget(self.layer_n)
        row2.addWidget(label("层", "RowKey"))
        self.layer_btn = ghost("固定", self.fix_bottom_layers)
        row2.addWidget(self.layer_btn)
        row2.addWidget(self.layer_info)
        row2.addSpacing(18)
        row2.addWidget(self.index_edit, 1)
        self.index_buttons = [ghost("固定", lambda: self.edit_typed("fix")),
                              ghost("弛豫", lambda: self.edit_typed("release"))]
        for b in self.index_buttons:
            row2.addWidget(b)
        tg.addLayout(row2)
        v.addWidget(tools)
        return page

    def _bottom_bar(self):
        self.bottom = QFrame()
        self.bottom.setObjectName("BottomBar")
        h = QHBoxLayout(self.bottom)
        h.setContentsMargins(22, 12, 22, 12)
        h.setSpacing(10)
        h.addWidget(label("输出到", "RowKey"))
        self.out_label = label("结构文件所在目录", "OutPath")
        self.out_label.setMaximumWidth(420)
        h.addWidget(self.out_label)
        self.open_btn = ghost("打开文件夹", self._open_outdir)
        h.addWidget(self.open_btn)
        h.addSpacing(10)
        self.file_checks = {f: toggle(f, True, self.refresh) for f in ALL_FILES}
        for b in self.file_checks.values():
            h.addWidget(b)
        h.addSpacing(10)
        self.status = label("", "Hint", wrap=True)
        h.addWidget(self.status, 1)
        self.gen_btn = QPushButton("⚡ 生成作业文件")
        self.gen_btn.setObjectName("Primary")
        self.gen_btn.setMinimumWidth(190)
        self.gen_btn.setToolTip("Ctrl+Enter")
        self.gen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gen_btn.clicked.connect(self.generate)
        h.addWidget(self.gen_btn)
        return self.bottom

    # ================================================================ 左侧卡片
    def _structure_card(self):
        card = Card("1", "结构文件")
        self.drop = DropZone("点击选择，或把 CIF / POSCAR / CONTCAR 拖到这里", "CIF 会自动转换为 POSCAR · 输出写到同一目录")
        self.drop.clicked.connect(self._pick_structure)
        self.drop.dropped.connect(self.load_structure)
        card.body.addWidget(self.drop)

        self.file_box = QWidget()
        fv = QVBoxLayout(self.file_box)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(10)
        chip = QFrame()
        chip.setObjectName("FileChip")
        ch = QHBoxLayout(chip)
        ch.setContentsMargins(14, 8, 8, 8)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.file_name = label("", "FileName")
        self.file_dir = label("", "Hint")
        names.addWidget(self.file_name)
        names.addWidget(self.file_dir)
        ch.addLayout(names, 1)
        ch.addWidget(ghost("更换", self._pick_structure))
        ch.addWidget(ghost("移除", self.clear_structure, "Danger"))
        fv.addWidget(chip)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(8)
        fv.addLayout(self.chips)
        self.load_note = label("", "Hint", wrap=True)
        fv.addWidget(self.load_note)
        self.file_box.setVisible(False)
        card.body.addWidget(self.file_box)
        return card

    def _project_card(self):
        card = Card("2", "计算项目")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.calc_group = QButtonGroup(self)
        for i, (no, name, _) in enumerate(jg.CALCS):
            b = ProjectCard(no, name)
            self.calc_group.addButton(b, i)
            grid.addWidget(b, i // 4, i % 4)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        self.calc_group.button(0).setChecked(True)
        self.calc_group.idToggled.connect(lambda _id, on: on and self.refresh())
        card.body.addLayout(grid)
        self.tpl_note = label("", "NoteBox", wrap=True)
        card.body.addWidget(self.tpl_note)
        return card

    def _electronic_card(self):
        card = Card("3", "体系与电子结构")
        self.sys_seg = Segmented(SYSTEMS, "auto")
        self.sys_seg.changed.connect(self.refresh)
        w, self.sys_hint = setting_row("体系类型", self.sys_seg)
        card.body.addWidget(w)
        card.body.addWidget(hline())

        self.spin_seg = Segmented(SPIN_MODES, "off")
        self.spin_seg.changed.connect(self.refresh)
        w, self.spin_hint = setting_row("自旋参数", self.spin_seg)
        card.body.addWidget(w)
        self.spin_panel = QFrame()
        self.spin_panel.setObjectName("SubPanel")
        self.spin_grid = QGridLayout(self.spin_panel)
        self.spin_grid.setContentsMargins(14, 10, 14, 10)
        self.spin_grid.setHorizontalSpacing(10)
        card.body.addWidget(self.spin_panel)
        card.body.addWidget(self._llm_panel())
        card.body.addWidget(hline())

        self.u_seg = Segmented(U_MODES, "off")
        self.u_seg.changed.connect(self.refresh)
        w, self.u_hint = setting_row("DFT+U 参数", self.u_seg)
        card.body.addWidget(w)
        self.u_panel = QFrame()
        self.u_panel.setObjectName("SubPanel")
        self.u_grid = QGridLayout(self.u_panel)
        self.u_grid.setContentsMargins(14, 10, 14, 10)
        self.u_grid.setHorizontalSpacing(14)
        card.body.addWidget(self.u_panel)
        card.body.addWidget(hline())

        w, self.sw_mix, self.mix_edits, self.mix_hint = self._value_row(
            "电荷混合 parameters-MIX", "parameters-MIX.incar", float)
        card.body.addWidget(w)
        card.body.addWidget(hline())
        w, self.sw_ngxf, self.ngxf_edits, self.ngxf_hint = self._value_row(
            "细 FFT 网格 parameters-NGXF", "parameters-NGXF.incar", int)
        card.body.addWidget(w)
        return card

    def _value_row(self, title: str, template: str, kind):
        """开关 + 可编辑数值面板（数值来自模板）。返回 (容器, 开关, {标签: 输入框}, 说明)。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        row, sw, hint = self._switch_row(title)
        v.addWidget(row)
        panel = QFrame()
        panel.setObjectName("SubPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(10)
        edits = {}
        for i, (tag, value) in enumerate(jg.template_values(template)):
            e = QLineEdit(value)
            e.setFixedWidth(92)
            e.setProperty("kind", kind.__name__)
            e.textChanged.connect(self.refresh)
            r, c = divmod(i, 2)
            grid.addWidget(label(tag, "Hint"), r, 2 * c)
            grid.addWidget(e, r, 2 * c + 1)
            edits[tag] = e
        grid.setColumnStretch(4, 1)
        self._attach(sw, panel)
        v.addWidget(panel)
        return box, sw, edits, hint

    def _llm_panel(self):
        """补充说明 + 开始分析，下方为 LLM 输出框。接口、模型与密钥只来自配置，不在界面显示。"""
        for key in ("llm_model", "llm_url"):  # 旧版本记住的连接设置不再使用
            self.settings.remove(key)
        self.llm_panel = QFrame()
        self.llm_panel.setObjectName("SubPanel")
        g = QGridLayout(self.llm_panel)
        g.setContentsMargins(14, 10, 14, 12)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(10)
        self.llm_hint_edit = QLineEdit()
        self.llm_hint_edit.setPlaceholderText("补充说明，如：Ni 为 +2 价，反铁磁")
        self.llm_btn = ghost("开始分析", self.start_llm)
        self.llm_box = ResultBox()
        self._llm_tick = QTimer(self)
        self._llm_tick.setInterval(1000)
        self._llm_tick.timeout.connect(self._llm_render)
        g.addWidget(label("补充说明", "RowKey"), 0, 0)
        g.addWidget(self.llm_hint_edit, 0, 1)
        g.addWidget(self.llm_btn, 0, 2)
        g.addWidget(self.llm_box, 1, 0, 1, 3)
        g.setColumnStretch(1, 1)
        self.llm_panel.setVisible(False)
        return self.llm_panel

    def _kpoint_potcar_card(self):
        card = Card("4", "K 点与赝势")
        self.kdens = dspin(0.005, 0.5, 0.005, 0.04, 3, 90)
        self.kdens.valueChanged.connect(self.refresh)
        self.kmesh_row, self.k_hint = setting_row("K 点密度（1/Å，越小越密）", self.kdens)
        card.body.addWidget(self.kmesh_row)
        self.per_seg = ispin(5, 200, 20, 90)
        self.per_seg.valueChanged.connect(self.refresh)
        self.kline_row, self.kline_hint = setting_row("能带路径每段插点数", self.per_seg)
        card.body.addWidget(self.kline_row)
        card.body.addWidget(hline())

        lib = QWidget()
        lh = QHBoxLayout(lib)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(6)
        self.potdir_edit = QLineEdit(str(self.settings.value("potcar_dir", jg.default_potcar_dir())))
        self.potdir_edit.setMinimumWidth(280)
        self.potdir_edit.editingFinished.connect(self._potdir_changed)
        lh.addWidget(self.potdir_edit)
        lh.addWidget(ghost("浏览…", self._pick_potdir))
        w, self.pot_hint = setting_row("赝势库", lib)
        card.body.addWidget(w)
        self.pot_panel = QFrame()
        self.pot_panel.setObjectName("SubPanel")
        self.pot_grid = QGridLayout(self.pot_panel)
        self.pot_grid.setContentsMargins(14, 10, 14, 10)
        self.pot_grid.setHorizontalSpacing(10)
        card.body.addWidget(self.pot_panel)
        self.sw_clean = Switch()
        self.sw_clean.toggled.connect(self.refresh)
        w, self.clean_hint = setting_row("去除 SHA256 / COPYR 行", self.sw_clean)
        card.body.addWidget(w)
        return card

    def _condition_card(self):
        card = Card("5", "计算条件")
        w, self.sw_sol, self.sol_hint = self._switch_row("隐式溶剂化 VASPsol")
        card.body.addWidget(w)
        card.body.addWidget(hline())

        w, self.sw_ef, _ = self._switch_row("电场 E-field")
        self.ef_value = dspin(-1.0, 1.0, 0.01, -0.1)
        self.ef_dir = combo()
        for i, ax in enumerate("abc", 1):
            self.ef_dir.addItem(f"{i}  ({ax} 方向)", i)
        self.ef_dir.setCurrentIndex(2)
        self.ef_value.valueChanged.connect(self.refresh)
        self.ef_dir.currentIndexChanged.connect(self.refresh)
        panel = SubPanel()
        panel.add_field("EFIELD (V/Å)", self.ef_value)
        panel.add_field("IDIPOL", self.ef_dir)
        panel.lay.addStretch()
        self._attach(self.sw_ef, panel)
        card.body.addWidget(w)
        card.body.addWidget(panel)
        card.body.addWidget(hline())

        w, self.sw_cp, _ = self._switch_row("恒电势 CP-VASP")
        self.cp_mu = dspin(-10.0, 0.0, 0.1, -4.6)
        self.cp_u = dspin(-5.0, 5.0, 0.1, jg.CP_REF - self.cp_mu.value())
        self.cp_u.valueChanged.connect(self._cp_from_u)
        self.cp_mu.valueChanged.connect(self._cp_from_mu)
        self.sw_cp.toggled.connect(lambda on: self.sw_sol.setEnabled(not on))
        panel = SubPanel()
        panel.add_field("电势 U (V vs SHE)", self.cp_u)
        panel.add_field("TARGETMU (eV)", self.cp_mu)
        panel.lay.addWidget(label(f"TARGETMU = {jg.CP_REF} − U", "Hint"))
        panel.lay.addStretch()
        self._attach(self.sw_cp, panel)
        card.body.addWidget(w)
        card.body.addWidget(panel)
        card.body.addWidget(hline())

        w, self.sw_par, _ = self._switch_row("并行参数 KPAR / NPAR")
        self.kpar = ispin(1, 512, 2)
        self.npar = ispin(1, 512, 2)
        self.kpar.valueChanged.connect(self.refresh)
        self.npar.valueChanged.connect(self.refresh)
        panel = SubPanel()
        panel.add_field("KPAR", self.kpar)
        panel.add_field("NPAR", self.npar)
        panel.lay.addStretch()
        self._attach(self.sw_par, panel)
        card.body.addWidget(w)
        card.body.addWidget(panel)
        card.body.addWidget(hline())

        w, self.sw_lob, self.lob_hint = self._switch_row("创建 lobsterin（LOBSTER 成键分析）")
        self.sw_lob.toggled.connect(self._lobster_toggled)
        panel = SubPanel()
        self.lob_pairs = QLineEdit()
        self.lob_pairs.setPlaceholderText("原子对（编号从 1 开始），如 37-38, 12-15 · 在 3D 视图悬停可查看编号")
        self.lob_pairs.textChanged.connect(self.refresh)
        panel.add_field("cohpbetween", self.lob_pairs)
        panel.lay.setStretchFactor(self.lob_pairs, 1)
        self._attach(self.sw_lob, panel)
        card.body.addWidget(w)
        card.body.addWidget(panel)
        card.body.addWidget(hline())

        w, self.sw_opt, self.opt_hint = self._switch_row("创建 OPTCELL（限定晶格方向弛豫）")
        panel = QFrame()
        panel.setObjectName("SubPanel")
        og = QGridLayout(panel)
        og.setContentsMargins(14, 10, 14, 10)
        og.setHorizontalSpacing(8)
        for c, axis in enumerate("xyz", 1):
            og.addWidget(label(axis, "Hint"), 0, c, Qt.AlignmentFlag.AlignCenter)
        self.opt_cells = []
        for r, (vec, row) in enumerate(zip("abc", jg.optcell_default()), 1):
            og.addWidget(label(f"{vec} 矢量", "RowKey"), r, 0)
            cells = []
            for c, v in enumerate(row, 1):
                b = toggle(str(v), bool(v), self._optcell_changed)
                b.setFixedWidth(44)
                og.addWidget(b, r, c)
                cells.append(b)
            self.opt_cells.append(cells)
        og.addWidget(label("1 = 该分量可变，0 = 固定\n需要编译了 OPTCELL 补丁的 VASP，且 ISIF=3", "Hint", wrap=True),
                     1, 4, 3, 1)
        og.setColumnStretch(4, 1)
        self.opt_panel = panel
        self._attach(self.sw_opt, panel)
        card.body.addWidget(w)
        card.body.addWidget(panel)
        return card

    def _lobster_toggled(self, on: bool):
        """LOBSTER 无法读取含 SHA256 / COPYR 行的 POTCAR，开启时强制去除。"""
        if on:
            self._clean_before = self.sw_clean.isChecked()
            self.sw_clean.setChecked(True)
        else:
            self.sw_clean.setChecked(self._clean_before)
        self.sw_clean.setEnabled(not on)

    def _optcell_changed(self, *_):
        for row in self.opt_cells:
            for b in row:
                b.setText("1" if b.isChecked() else "0")
        self.refresh()

    def _switch_row(self, title: str):
        sw = Switch()
        sw.toggled.connect(self.refresh)
        w, hint = setting_row(title, sw)
        return w, sw, hint

    @staticmethod
    def _attach(switch: Switch, panel: QWidget):
        panel.setVisible(False)
        switch.toggled.connect(panel.setVisible)

    # ================================================================ 结构
    def _pick_structure(self):
        start = str(self.source.parent) if self.source else str(self.settings.value("last_dir", Path.cwd()))
        path, _ = QFileDialog.getOpenFileName(self, "选择结构文件", start, st.FILE_FILTER)
        if path:
            self.load_structure(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.load_structure(urls[0])

    def load_structure(self, path):
        try:
            atoms, mask, notes = st.load(path)
            self._set_structure(atoms, mask)
        except Exception as exc:
            self.toast.show_message(f"无法读取 {Path(path).name}：{exc}", error=True)
            return
        self.source = Path(path).resolve()
        self.settings.setValue("last_dir", str(self.source.parent))
        self.lob_pairs.blockSignals(True)  # 原子编号只对当前结构有意义
        self.lob_pairs.clear()
        self.lob_pairs.blockSignals(False)
        self.manual = {f: False for f in ALL_FILES}
        self.edit_errors = {"POSCAR": [], "POTCAR": []}
        self.pot_manual = None
        self.disk.clear()
        self.file_name.setText(self.source.name)
        self.file_dir.setText(str(self.source.parent))
        set_hint(self.load_note, " · ".join(notes))
        self.drop.setVisible(False)
        self.file_box.setVisible(True)
        if self.detected[0] == "slab":
            self.ef_dir.setCurrentIndex(self.vac_axis())
        self._rebuild_editors()
        self.show_structure()
        self.tab_seg.buttons["structure"].setChecked(True)
        self.refresh()

    def _set_structure(self, atoms, mask):
        """替换当前结构（读取文件或编辑 POSCAR 后），重置依赖结构的缓存。"""
        spos = jg.spin.parse_poscar_text(st.poscar_text(atoms, mask), "POSCAR")
        detected = jg.spin.detect_system_type(spos)
        self.atoms, self.mask, self.spos, self.detected = atoms, mask, spos, detected
        self.generation += 1
        self.cancel_llm("结构已改变，已取消分析，请重新分析", refresh=False)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._spin_plan = None
        self._kp_cache.clear()
        gaps = detected[1]
        clear_layout(self.chips)
        self.chips.addWidget(label("".join(f"{el}{n}" for el, n in st.species(atoms)), "Badge"))
        self.chips.addWidget(label(f"{len(atoms)} 个原子", "Chip"))
        self.chips.addWidget(label(DETECTED[detected[0]], "Chip"))
        self.chips.addWidget(label(f"真空 a/b/c  {gaps['a']:.1f} / {gaps['b']:.1f} / {gaps['c']:.1f} Å", "Chip"))
        self.chips.addStretch()

    def clear_structure(self):
        self.atoms = self.spos = self.detected = self.source = self._spin_plan = None
        self.mask = np.zeros((0, 3), dtype=bool)
        self.generation += 1
        self.cancel_llm("已移除结构", refresh=False)
        self.edit_errors = {"POSCAR": [], "POTCAR": []}
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.manual = {f: False for f in ALL_FILES}
        self.pot_manual = None
        self.disk.clear()
        self.file_box.setVisible(False)
        self.drop.setVisible(True)
        self._rebuild_editors()
        if self.viewer_ready:
            self.bridge.stateChanged.emit(json.dumps({"clear": True}))
        self.refresh()

    def vac_axis(self) -> int:
        return int(np.argmax([self.detected[1][a] for a in "abc"])) if self.detected else 2

    def layer_axis(self) -> int:
        """分层方向：表面取真空方向，其余取 c。"""
        return self.vac_axis() if self.system_type() == "slab" else 2

    def _rebuild_editors(self):
        for grid in (self.spin_grid, self.u_grid, self.pot_grid):
            clear_layout(grid)
        self.spin_inputs.clear()
        self.u_inputs.clear()
        self.pot_inputs.clear()
        if self.atoms is None:
            for grid in (self.spin_grid, self.u_grid, self.pot_grid):
                grid.addWidget(label("加载结构文件后可按元素设置", "Hint"), 0, 0)
            return
        elements = list(dict.fromkeys(el for el, _ in st.species(self.atoms)))

        for i, el in enumerate(elements):
            box = dspin(-10.0, 10.0, 0.5, jg.spin.default_element_moment(el), 2, 76)
            box.valueChanged.connect(self.refresh)
            r, c = divmod(i, 4)
            self.spin_grid.addWidget(label(el, "RowTitle"), r, 2 * c)
            self.spin_grid.addWidget(box, r, 2 * c + 1)
            self.spin_inputs[el] = box
        self.spin_grid.setColumnStretch(8, 1)

        for c, text in enumerate(["元素", "轨道 LDAUL", "U (eV)", "J (eV)"]):
            self.u_grid.addWidget(label(text, "Hint"), 0, c)
        for r, el in enumerate(elements, 1):
            if el in jg.dftu.DEFAULT_U:
                _, l_val, u_val, j_val = jg.dftu.DEFAULT_U[el]
            elif el in jg.dftu.U:
                l_val, u_val, j_val = -1, jg.dftu.U[el][0][1], jg.dftu.U[el][0][2]
            else:
                l_val, u_val, j_val = -1, 0.0, 0.0
            orb = combo()
            for text, val in (("不加 U", -1), ("p  (1)", 1), ("d  (2)", 2), ("f  (3)", 3)):
                orb.addItem(text, val)
            orb.setCurrentIndex(orb.findData(l_val))
            u_box = dspin(-10.0, 20.0, 0.1, u_val, 2, 76)
            j_box = dspin(0.0, 10.0, 0.1, j_val, 2, 76)
            orb.currentIndexChanged.connect(self.refresh)
            u_box.valueChanged.connect(self.refresh)
            j_box.valueChanged.connect(self.refresh)
            for c, w in enumerate((label(el, "RowTitle"), orb, u_box, j_box)):
                self.u_grid.addWidget(w, r, c)
            self.u_inputs[el] = (orb, u_box, j_box)
        self.u_grid.setColumnStretch(4, 1)
        self._rebuild_potcar()

    def _rebuild_potcar(self):
        clear_layout(self.pot_grid)
        self.pot_inputs.clear()
        if self.atoms is None:
            self.pot_grid.addWidget(label("加载结构文件后可按元素设置", "Hint"), 0, 0)
            return
        for i, el in enumerate(dict.fromkeys(el for el, _ in st.species(self.atoms))):
            box = combo()
            box.addItems(jg.potcar_variants(self.potdir_edit.text(), el))
            box.currentIndexChanged.connect(self._potcar_combo_changed)
            r, c = divmod(i, 3)
            self.pot_grid.addWidget(label(el, "RowTitle"), r, 2 * c)
            self.pot_grid.addWidget(box, r, 2 * c + 1)
            self.pot_inputs[el] = box
        self.pot_grid.setColumnStretch(6, 1)

    def _potcar_combo_changed(self):
        self.pot_manual = None
        self.manual["POTCAR"] = False
        self.refresh()

    # ================================================================ 3D 视图与固定
    def _display_changed(self, *_):
        self.settings.setValue("boundary", self.boundary_btn.isChecked())
        self.settings.setValue("bonds", self.bond_seg.value())
        self.send_view(full=True, keep_view=True)

    def _send(self, state: dict):
        if self.viewer_ready and self.atoms is not None:
            self.bridge.stateChanged.emit(json.dumps(state))

    def send_view(self, full: bool = False, keep_view: bool = False, view=None):
        """发送固定状态；full 时连同结构整体重建（view 为初始朝向四元数，与结构同一帧生效）。"""
        if not self.viewer_ready or self.atoms is None:
            return
        state = {"generation": self.generation, "masks": self.mask.tolist(), "mode": self.mode_seg.value()}
        if full:
            state["structure"] = st.viewer_payload(self.atoms, self.boundary_btn.isChecked(), self.bond_seg.value())
            state["keepView"] = keep_view
            if view is not None:
                state["view"] = view
        self._send(state)

    def show_structure(self, keep_view: bool = False):
        """整体发送结构；真空沿 c 的表面默认正视，便于看清分层。"""
        view = None
        if not keep_view and self.detected and self.detected[0] == "slab" and self.vac_axis() == 2:
            view = standard_view(self.atoms.cell.array, "front")["quaternion"]
        self.send_view(full=True, keep_view=keep_view, view=view)

    def set_view(self, name: str):
        if self.atoms is None or not self.viewer_ready:
            return
        view = standard_view(self.atoms.cell.array, name)
        view["generation"] = self.generation
        self.bridge.viewRequested.emit(json.dumps(view))

    def edit_mask(self, indices, action: str):
        if self.atoms is None or not indices:
            return
        new = self.mask.copy()
        if action == "toggle":
            new[indices] = (~new[indices].all(axis=1))[:, None]
        else:
            new[indices] = action == "fix"
        self._push_mask(new)

    def _push_mask(self, new):
        if np.array_equal(new, self.mask):
            return
        self.undo_stack = (self.undo_stack + [self.mask])[-100:]
        self.redo_stack.clear()
        self._set_mask(new)

    def _set_mask(self, mask):
        self.mask = mask
        self.manual["POSCAR"] = False  # 固定状态改变后按当前结构重新生成 POSCAR
        self.send_view()
        self.refresh()

    def edit_all(self, action: str):
        if self.atoms is not None:
            self.edit_mask(list(range(len(self.atoms))), action)

    def edit_typed(self, action: str):
        if self.atoms is None:
            return
        try:
            self.edit_mask(st.parse_indices(self.index_edit.text(), len(self.atoms)), action)
        except ValueError as exc:
            self.toast.show_message(str(exc), error=True)

    def fix_bottom_layers(self):
        if self.atoms is None:
            return
        groups = st.layers(self.atoms, self.layer_axis())
        n = min(self.layer_n.value(), len(groups))
        bottom = np.concatenate(groups[:n]).tolist()
        new = np.zeros_like(self.mask)
        new[bottom] = True
        self._push_mask(new)
        self.toast.show_message(f"已固定底部 {n} 层（{len(bottom)} 个原子），其余原子弛豫")

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.mask)
            self._set_mask(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.mask)
            self._set_mask(self.redo_stack.pop())

    def _update_fix_tools(self):
        has = self.atoms is not None
        for w in self.edit_buttons + self.index_buttons + self.view_buttons + [self.layer_btn, self.layer_n, self.index_edit]:
            w.setEnabled(has)
        self.edit_buttons[2].setEnabled(has and bool(self.undo_stack))
        self.edit_buttons[3].setEnabled(has and bool(self.redo_stack))
        if not has:
            self.fix_stats.setText("未加载结构")
            self.layer_info.setText("")
            return
        full = int(self.mask.all(axis=1).sum())
        part = int((self.mask.any(axis=1) & ~self.mask.all(axis=1)).sum())
        free = len(self.atoms) - full - part
        text = f"共 {len(self.atoms)} 个原子 · 固定 {full} · 弛豫 {free}"
        self.fix_stats.setText(text + (f" · 部分固定 {part}" if part else ""))
        axis = self.layer_axis()
        self.layer_info.setText(f"沿 {'abc'[axis]} 方向共 {len(st.layers(self.atoms, axis))} 层")

    # ================================================================ 参数
    def _cp_from_u(self, u: float):
        self.cp_mu.blockSignals(True)
        self.cp_mu.setValue(jg.cpvasp.calculate_targetmu_from_potential(u, jg.CP_REF))
        self.cp_mu.blockSignals(False)
        self.refresh()

    def _cp_from_mu(self, mu: float):
        self.cp_u.blockSignals(True)
        self.cp_u.setValue(jg.CP_REF - mu)
        self.cp_u.blockSignals(False)
        self.refresh()

    def _potdir_changed(self):
        self.settings.setValue("potcar_dir", self.potdir_edit.text().strip())
        self._rebuild_potcar()
        self.refresh()

    def _pick_potdir(self):
        path = QFileDialog.getExistingDirectory(self, "选择赝势库目录", self.potdir_edit.text())
        if path:
            self.potdir_edit.setText(path)
            self._potdir_changed()

    def system_type(self) -> str:
        choice = self.sys_seg.value()
        if choice != "auto":
            return choice
        if self.detected is None:
            return "bulk"
        return "slab" if self.detected[0] == "unknown" else self.detected[0]

    def calc_tpl(self) -> tuple[str, str]:
        """返回 (计算项目模板键, INCAR 模板文件名)。"""
        key = jg.CALCS[self.calc_group.checkedId()][2]
        return (key or "relax"), (key or f"relax-{self.system_type()}")

    def _spin_moments(self, mode: str, errors: list):
        """返回 (逐原子磁矩, ISPIN)；需要等待 LLM 时返回 (None, None) 并记录原因。"""
        s = self.spos
        if mode == "default":
            moments, reason = self._default_moments()
            set_hint(self.spin_hint, "内置启发式（未调用 LLM）：" + reason.replace("[回退] ", "", 1))
            return moments, None
        if mode == "custom":
            set_hint(self.spin_hint, "按 POSCAR 原子顺序展开为 MAGMOM，同一元素取相同初值")
            return [self.spin_inputs[el].value() for el in s.symbols], None
        r = self._llm if self._llm and self._llm["gen"] == self.generation else None
        if self._llm_running:
            errors.append("自旋：LLM 分析进行中")
            set_hint(self.spin_hint, "LLM 分析进行中，完成后自动写入 MAGMOM")
            return None, None
        if r is None:
            errors.append("自旋：请先点击“开始分析”运行 LLM")
            set_hint(self.spin_hint, "LLM 结合结构分析、元素与材料知识库给出磁矩方案，经校验后写入 MAGMOM")
            return None, None
        if "error" in r:
            errors.append("自旋：LLM 调用失败")
            set_hint(self.spin_hint, "LLM 调用失败，详见下方；可重试，或改用启发式 / 按元素指定", C.RED)
            return None, None
        if r["warnings"]:
            set_hint(self.spin_hint, "已按 LLM 方案写入 MAGMOM，有告警，详见下方", "#B26A00")
        else:
            set_hint(self.spin_hint, "已按 LLM 方案写入 MAGMOM，依据详见下方")
        return r["moments"], r["ispin"]

    # ---------------------------------------------------------------- LLM
    def start_llm(self):
        """开始分析；分析进行中时同一按钮为“取消”。"""
        if self._llm_running:
            self.cancel_llm("已取消")
            return
        if self.spos is None:
            return
        self._llm_run += 1
        run, spos = self._llm_run, self.spos
        poscar = st.poscar_text(self.atoms, self.mask)
        hint = self.llm_hint_edit.text().strip()
        sig = self._llm_sig

        def work():
            try:
                res = jg.llm_magmom(spos, poscar, hint, progress=lambda m: sig.progress.emit(run, m))
                sig.done.emit(run, res)
            except Exception as exc:  # 网络、鉴权、方案校验失败都在界面上显示
                sig.failed.emit(run, str(exc))

        self._llm_running = True
        self._llm_gen = self.generation
        self._llm_t0 = time.monotonic()
        self._llm_msg = "正在连接"
        self.llm_btn.setText("取消")
        self._llm_tick.start()
        threading.Thread(target=work, daemon=True).start()
        self.refresh()

    def _llm_render(self):
        """按当前状态重绘 LLM 输出框。"""
        box, esc = self.llm_box, html.escape
        box.spinner.start() if self._llm_running else box.spinner.stop()
        if self._llm_running:
            box.set_title(f"分析中 · {self._llm_msg} · 已用 {time.monotonic() - self._llm_t0:.0f} s", C.BLUE)
            box.set_html(f"<span style='color:{C.TEXT_3}'>正在分析结构、查询元素与材料知识库，完成后自动写入 MAGMOM</span>")
            return
        r = self._llm if self._llm and self._llm["gen"] == self.generation else None
        if r is None:
            box.set_title(self._llm_note or "尚未分析")
            box.set_html(f"<span style='color:{C.TEXT_3}'>点击“开始分析”：LLM 结合结构分析、元素与材料知识库给出磁矩方案，"
                         "经 add-spin 校验后写入 MAGMOM；补充说明会一并发送给 LLM</span>")
            return
        if "error" in r:
            box.set_title("分析失败", C.RED)
            box.set_html(f"<span style='color:{C.RED}'>{esc(r['error'])}</span><br>"
                         f"<span style='color:{C.TEXT_3}'>可重试，或改用启发式 / 按元素指定</span>")
            return
        ispin = "ISPIN = 2" if r["ispin"] == 2 else "ISPIN = 1（非磁性）"
        box.set_title(f"完成 · {r['steps']} 轮 · {r['secs']:.0f} s · {ispin}", "#B26A00" if r["warnings"] else C.GREEN)
        block = jg.spin.format_magmom(self.spos, r["moments"], ispin=r["ispin"])
        magmom = next((l.strip() for l in block.splitlines() if l.strip().upper().startswith("MAGMOM")), "")
        parts = []
        if magmom:
            parts.append(f"<span style='font-family:Consolas,monospace'>{esc(magmom)}</span>")
        parts.append(f"<b>依据</b>　{esc(r['rationale'] or '未给出说明')}")
        if r["warnings"]:
            parts.append("<span style='color:#B26A00'><b>告警</b>　" + "<br>".join(map(esc, r["warnings"])) + "</span>")
        box.set_html("".join(f"<p style='margin:0 0 6px 0'>{x}</p>" for x in parts))

    def _llm_progress(self, run: int, msg: str):
        if run == self._llm_run and self._llm_running:
            self._llm_msg = msg
            self._llm_render()

    def _llm_stop(self):
        self._llm_running = False
        self._llm_tick.stop()
        self.llm_btn.setText("重新分析" if self._llm else "开始分析")

    def cancel_llm(self, note: str, refresh: bool = True):
        """丢弃正在进行的分析（后台请求无法中断，结果到达后被忽略）。"""
        if not self._llm_running:
            return
        self._llm_run += 1
        self._llm_stop()
        self._llm_note = note
        if refresh:
            self.refresh()
        else:
            self._llm_render()

    def _llm_finish(self, run: int, result: dict) -> bool:
        if run != self._llm_run or not self._llm_running:  # 已取消或已重新开始
            return False
        stale = self._llm_gen != self.generation
        if stale:
            self._llm_note = "结构已改变，请重新分析"
        else:
            self._llm = {"gen": self._llm_gen, "secs": time.monotonic() - self._llm_t0, **result}
        self._llm_stop()
        self.refresh()
        return not stale

    def _llm_done(self, run: int, res: dict):
        if self._llm_finish(run, res):
            self.toast.show_message("LLM 自旋分析完成，已写入 INCAR")

    def _llm_failed(self, run: int, msg: str):
        self._llm_finish(run, {"error": msg})

    def _default_moments(self):
        if self._spin_plan is None:
            try:
                self._spin_plan = jg.spin.heuristic_plan(self.spos)
            except Exception as exc:
                self._spin_plan = exc
        if isinstance(self._spin_plan, Exception):
            raise self._spin_plan
        return self._spin_plan

    # ================================================================ 自动生成
    def build_incar(self, tpl: str, errors: list) -> str:
        text = jg.read_tpl(jg.INCAR_TPL / f"{tpl}.incar")
        note = jg.NOTES.get(tpl, "")
        set_hint(self.tpl_note, f"模板 {tpl}.incar  ·  {jg.tpl_summary(text)}" + (f"\n{note}" if note else ""))
        s = self.spos

        set_hint(self.spin_hint, "")
        mode = self.spin_seg.value()
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
                    set_hint(self.spin_hint, f"{exc}；可改用 LLM 分析或“按元素指定”", C.RED)

        set_hint(self.u_hint, "")
        mode = self.u_seg.value()
        if mode != "off":
            if s is None:
                errors.append("DFT+U 需要先加载结构")
            else:
                custom = None
                if mode == "custom":
                    custom = {el: (o.currentData(), u.value(), j.value()) for el, (o, u, j) in self.u_inputs.items()}
                else:
                    applied = [f"{el}（{jg.dftu.DEFAULT_U[el][0]}, U={jg.dftu.DEFAULT_U[el][2]:g}）"
                               for el in dict.fromkeys(s.species) if el in jg.dftu.DEFAULT_U]
                    if applied:
                        set_hint(self.u_hint, "加 U：" + "、".join(applied))
                block = jg.dftu.build_dftu(list(zip(s.species, s.counts)), custom=custom)
                if block:
                    text = jg.merge(text, block)
                else:
                    set_hint(self.u_hint, "体系中没有需要加 U 的元素，未写入 DFT+U 参数")

        for sw, edits, hint, tpl_name, head in ((self.sw_mix, self.mix_edits, self.mix_hint, "parameters-MIX.incar", "## Mixing"),
                                                (self.sw_ngxf, self.ngxf_edits, self.ngxf_hint, "parameters-NGXF.incar", "## FFT grid")):
            set_hint(hint, "")
            if not sw.isChecked():
                continue
            values, bad = {}, []
            for tag, e in edits.items():
                raw = e.text().strip()
                try:
                    num = float(raw) if e.property("kind") == "float" else int(raw)
                    if num < 0 or (e.property("kind") == "int" and num == 0):
                        raise ValueError
                except ValueError:
                    bad.append(tag)
                values[tag] = raw
            if bad:
                errors.append(f"{'、'.join(bad)} 数值无效")
                set_hint(hint, f"{'、'.join(bad)} 需要{'正整数' if e.property('kind') == 'int' else '非负数'}", C.RED)
                continue
            text = jg.merge(text, head + "\n" + jg.apply_values(tpl_name, values))
            if sw is self.sw_mix and self.spin_seg.value() == "off":
                set_hint(hint, "AMIX_MAG / BMIX_MAG 只在自旋极化（ISPIN=2）时起作用")
            elif sw is self.sw_ngxf:
                set_hint(hint, "细网格 FFT 点数，通常取 NGX/NGY/NGZ 的 2 倍；可参照同体系 OUTCAR 中的 NGXF 一行")

        cp_on = self.sw_cp.isChecked()
        set_hint(self.sol_hint, "CP-VASP 模板已包含溶剂化参数" if cp_on else "")
        return jg.conditions(
            text, solvation=self.sw_sol.isChecked(),
            efield_value=self.ef_value.value() if self.sw_ef.isChecked() else None,
            idipol=self.ef_dir.currentData(),
            cp_mu=self.cp_mu.value() if cp_on else None,
            kpar_npar=(self.kpar.value(), self.npar.value()) if self.sw_par.isChecked() else None)

    def build_kpoints(self, key: str, errors: list) -> str:
        band = key == "band-band"
        self.kmesh_row.setVisible(not band)
        self.kline_row.setVisible(band)
        hint = self.kline_hint if band else self.k_hint
        set_hint(self.k_hint, "")
        set_hint(self.kline_hint, "")
        if self.atoms is None:
            set_hint(hint, "加载结构后计算")
            return ""
        args = (key, self.system_type(), self.vac_axis(), self.kdens.value(), self.per_seg.value())
        cache_key = (self.generation,) + (args if band else ("mesh",) + args[1:4])
        if cache_key not in self._kp_cache:
            try:
                self._kp_cache[cache_key] = jg.kpoints(self.atoms, *args)
            except Exception as exc:
                self._kp_cache[cache_key] = exc
        result = self._kp_cache[cache_key]
        if isinstance(result, Exception):
            errors.append(f"KPOINTS：{result}")
            set_hint(hint, str(result), C.RED)
            return ""
        text, summary, warnings = result
        set_hint(hint, "；".join([summary] + warnings), C.RED if warnings else "")
        return text

    def pot_labels(self) -> list[str]:
        if self.pot_manual is not None:
            return self.pot_manual
        return [self.pot_inputs[el].currentText() for el, _ in st.species(self.atoms)]

    def build_potcar(self, incar: str, errors: list) -> str:
        """返回 POTCAR 页的组成文本；全文保存在 self.potcar_full。"""
        set_hint(self.pot_hint, "")
        self.potcar_full = ""
        self.pot_infos = []
        if self.atoms is None:
            return ""
        species, labels = st.species(self.atoms), self.pot_labels()
        try:
            self.potcar_full, infos = jg.potcar(species, labels, self.potdir_edit.text().strip(), self.sw_clean.isChecked())
            self.pot_infos = infos
        except Exception as exc:
            errors.append(f"POTCAR：{exc}")
            set_hint(self.pot_hint, str(exc), C.RED)
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
        set_hint(self.pot_hint, msg, "#B26A00" if warn else "")
        return jg.potcar_composition(species, labels, infos, self.potdir_edit.text().strip(),
                                     f"{msg} · 完整 POTCAR {len(self.potcar_full.splitlines())} 行")

    def optcell_allowed(self) -> bool:
        return self.calc_tpl()[0] == "relax" and self.system_type() == "bulk"

    def active_files(self) -> list[str]:
        extra = {"lobsterin": self.sw_lob.isChecked(), "OPTCELL": self.sw_opt.isChecked() and self.optcell_allowed()}
        return FILES + [f for f in EXTRA if extra[f]]

    def build_lobster(self, incar: str, errors: list) -> str:
        set_hint(self.lob_hint, "")
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
            pairs = lb.parse_pairs(self.lob_pairs.text(), len(self.atoms))
        except ValueError as exc:
            errors.append(f"lobsterin：{exc}")
            set_hint(self.lob_hint, str(exc), C.RED)
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
        set_hint(self.lob_hint, "\n".join(msg + (["⚠ " + "；".join(warn)] if warn else [])), "#B26A00" if warn else "")
        self.notes["lobsterin"] = f"基函数 {n_basis} 个 · POTCAR 已自动去除 SHA256 / COPYR 行"
        return lb.lobsterin(jg.read_tpl(jg.COND_TPL / "lobster.incar"), basis_lines, pairs)

    def build_optcell(self, incar: str, errors: list) -> str:
        allowed = self.optcell_allowed()
        self.sw_opt.setEnabled(allowed)
        if not allowed:
            if self.sw_opt.isChecked():
                self.sw_opt.blockSignals(True)
                self.sw_opt.setChecked(False)
                self.sw_opt.blockSignals(False)
                self.opt_panel.setVisible(False)
            set_hint(self.opt_hint, f"仅在体相弛豫时可用（当前：{jg.CALCS[self.calc_group.checkedId()][1]} · {DETECTED.get(self.system_type(), self.system_type())}）")
            return ""
        grid = [[int(b.isChecked()) for b in row] for row in self.opt_cells]
        isif = jg.tag_value(incar, "ISIF")
        warn = [] if isif == "3" else [f"OPTCELL 需要 ISIF = 3（当前 {isif}）"]
        if not any(map(any, grid)):
            warn.append("全部为 0 时晶格不变，相当于 ISIF = 2")
        set_hint(self.opt_hint, "；".join(warn) if warn else "三行依次对应 a、b、c 矢量的 x、y、z 分量",
                 "#B26A00" if warn else "")
        if not self.sw_opt.isChecked():
            return ""
        self.notes["OPTCELL"] = "三行依次对应 a、b、c 矢量的 x y z 分量：1 可变 / 0 固定"
        return jg.optcell_text(grid)

    def _sync_editor(self, f: str):
        """自动生成内容 → 编辑器。未手动编辑时直接替换；手动编辑过则把这次设置改动三方合并进去，
        手动修改保留，同一参数以最后一次改动为准（合并结果可 Ctrl+Z 撤销）。"""
        ed, auto = self.editors[f], self.auto[f]
        if not self.manual[f]:
            ed.set_text(auto)
        elif not auto:          # 当前设置无法自动生成（如能带路径报错），保留手动内容
            return
        elif auto != self.base[f]:
            merged = jg.rebase(ed.text(), self.base[f], auto) if self.base[f] else ed.text()
            ed.set_text(merged, undoable=True)
            self.manual[f] = merged != auto
        self.base[f] = auto

    def refresh(self, *_):
        key, tpl = self.calc_tpl()
        errors = {f: [] for f in ALL_FILES}
        self.auto["INCAR"] = self.build_incar(tpl, errors["INCAR"])
        self.auto["KPOINTS"] = self.build_kpoints(key, errors["KPOINTS"])
        self.auto["POSCAR"] = st.poscar_text(self.atoms, self.mask) if self.atoms is not None else ""
        self._sync_editor("INCAR")
        incar_now = self.editors["INCAR"].text()
        self.auto["POTCAR"] = self.build_potcar(incar_now, errors["POTCAR"])
        self.auto["OPTCELL"] = self.build_optcell(incar_now, errors["OPTCELL"])
        self.auto["lobsterin"] = self.build_lobster(incar_now, errors["lobsterin"]) if self.sw_lob.isChecked() else ""
        set_hint(self.clean_hint, "LOBSTER 无法读取含这些行的 POTCAR，已自动开启" if self.sw_lob.isChecked() else "")
        active = self.active_files()
        for f in EXTRA:
            self.tab_seg.buttons[f].setVisible(f in active)
            self.file_checks[f].setVisible(f in active)
            if f not in active:
                errors[f] = []
        if self.tab_seg.value() not in active + ["structure"]:
            self.tab_seg.buttons["structure"].setChecked(True)
        for f in MERGED:
            if f != "INCAR" and (f in FILES or f in active):  # 未启用的附加文件保留手动内容，重新启用时再合并
                self._sync_editor(f)
            # 设置的改动会合并进手动编辑的文件，其问题照常提示；只有当前设置无法自动生成时以手动内容为准
            if self.manual[f] and not self.auto[f]:
                errors[f] = []
        if self.manual["lobsterin"] and re.search(r"^\s*cohpbetween\b", self.editors["lobsterin"].text(), re.M | re.I):
            errors["lobsterin"] = [e for e in errors["lobsterin"] if "cohpbetween" not in e]
        if self.manual["POSCAR"]:
            errors["POSCAR"] = list(self.edit_errors["POSCAR"])
        if self.manual["POTCAR"]:
            errors["POTCAR"] = list(self.edit_errors["POTCAR"]) or errors["POTCAR"]
        self.errors = errors
        for f in ("POSCAR", "POTCAR"):
            if not self.manual[f]:
                self.editors[f].set_text(self.auto[f])
        self._update_file_states()

        rule = f"弛豫使用 relax-{self.system_type()}.incar · 决定 K 点约束"
        if self.sys_seg.value() == "auto":
            rule = (f"检测为 {DETECTED[self.detected[0]]} · " if self.detected else "未加载结构，暂按体相 · ") + rule
        set_hint(self.sys_hint, rule)
        self.spin_panel.setVisible(self.spin_seg.value() == "custom")
        self.llm_panel.setVisible(self.spin_seg.value() == "llm")
        self.llm_btn.setEnabled(self._llm_running or self.spos is not None)
        self._llm_render()
        self.u_panel.setVisible(self.u_seg.value() == "custom")
        self._update_fix_tools()
        self._update_status()

    # ================================================================ 文件编辑
    def content(self, f: str) -> str:
        """写入磁盘的内容：POTCAR 为拼接全文，其余为编辑器文本。"""
        return self.potcar_full if f == "POTCAR" else self.editors[f].text()

    def _update_file_states(self):
        for f, ed in self.editors.items():
            text = ed.text()
            info = ""
            if self.errors[f]:
                info = "；".join(self.errors[f])
            elif self.manual[f] and text != self.auto[f]:
                info = {"POSCAR": "已同步到 3D 视图；在视图中修改固定状态会按当前结构重新生成",
                        "POTCAR": "已按标签重新拼接"}.get(f, "已手动编辑 · 左侧设置的改动会合并进来，同一参数以最后一次改动为准")
            elif f == "POTCAR" and self.potcar_full:
                info = "仅显示组成，不显示赝势全文"
            elif f == "POSCAR" and text:
                info = "Selective dynamics：T 弛豫 / F 固定"
            elif f in EXTRA and text:
                info = self.notes[f]
            path = self.out_path(f)
            saved = bool(text) and path is not None and self.disk.get(str(path)) == self.content(f)
            ed.set_status(self.manual[f], saved, info, error=bool(self.errors[f]))

    def _update_status(self):
        out = self.output_dir()
        self.out_label.setText(str(out) if out else "结构文件所在目录")
        self.out_label.setToolTip(str(out) if out else "")
        self.open_btn.setEnabled(out is not None)
        selected = self.selected_files()
        problems = [f"{e}" for f in selected for e in self.errors[f]]
        if self.atoms is None:
            set_hint(self.status, "请先上传结构文件")
        elif not selected:
            set_hint(self.status, "请至少选择一个要写入的文件")
        elif problems:
            set_hint(self.status, "；".join(problems), C.RED)
        else:
            edited = [f for f in selected if self.manual[f]]
            msg = f"将写入 {' '.join(selected)}"
            if edited:
                msg += f"（{' '.join(edited)} 为手动编辑）"
            if self.source and self.source.name in selected and self.source.parent == out:
                msg += f" · 将覆盖输入文件 {self.source.name}"
            set_hint(self.status, msg)
        self.gen_btn.setEnabled(self.atoms is not None and bool(selected) and not problems)

    def selected_files(self) -> list[str]:
        return [f for f in self.active_files() if self.file_checks[f].isChecked()]

    def _on_edited(self, f: str):
        ed = self.editors[f]
        self.manual[f] = ed.text() != self.auto[f]
        if f == "POSCAR":
            self._poscar_timer.start()
        elif f == "POTCAR":
            self._potcar_timer.start()
        elif f == "INCAR":
            self.refresh()   # ENCUT 检查依赖 INCAR
            return
        self._update_file_states()
        self._update_status()

    def _apply_poscar_edit(self):
        self.edit_errors["POSCAR"] = []
        if not self.manual["POSCAR"]:
            self.refresh()
            return
        try:
            atoms, mask = st.parse_poscar(self.editors["POSCAR"].text())
        except ValueError as exc:
            self.edit_errors["POSCAR"] = [str(exc)]
            self.refresh()
            return
        if self.atoms is not None and st.same(atoms, self.atoms):
            if not np.array_equal(mask, self.mask):
                self.undo_stack = (self.undo_stack + [self.mask])[-100:]
                self.redo_stack.clear()
                self.mask = mask
                self.send_view()
        else:
            old = [el for el, _ in st.species(self.atoms)] if self.atoms is not None else None
            same_count = self.atoms is not None and len(atoms) == len(self.atoms)
            self._set_structure(atoms, mask)
            if old != [el for el, _ in st.species(atoms)]:
                self.pot_manual = None
                self._rebuild_editors()
            self.show_structure(keep_view=same_count)
        self.refresh()

    def _apply_potcar_edit(self):
        self.edit_errors["POTCAR"] = []
        if not self.manual["POTCAR"]:
            self.pot_manual = None
            self.refresh()
            return
        if self.atoms is None:
            return
        labels = jg.parse_potcar_labels(self.editors["POTCAR"].text())
        species = st.species(self.atoms)
        try:
            jg.potcar(species, labels, self.potdir_edit.text().strip())
        except Exception as exc:
            self.pot_manual = None
            self.edit_errors["POTCAR"] = [str(exc)]
            self.refresh()
            return
        self.pot_manual = labels
        for (el, _), lab in zip(species, labels):  # 同步到左侧下拉框
            combo = self.pot_inputs.get(el)
            if combo is not None and combo.currentText() != lab:
                combo.blockSignals(True)
                if combo.findText(lab) < 0:
                    combo.addItem(lab)
                combo.setCurrentText(lab)
                combo.blockSignals(False)
        self.refresh()

    def revert_file(self, f: str):
        self.manual[f] = False
        if f == "POTCAR":
            self.pot_manual = None
        if f in self.edit_errors:
            self.edit_errors[f] = []
        self.refresh()
        self.toast.show_message(f"{f} 已恢复为自动生成内容")

    def _current_file(self) -> str | None:
        tab = self.tab_seg.value()
        return tab if tab in ALL_FILES else None

    def _save_current(self):
        f = self._current_file()
        if f:
            self.save_file(f)
        else:
            self.generate()

    def _find_current(self):
        f = self._current_file()
        if f:
            self.editors[f].open_find()

    def _switch_tab(self, key: str):
        self.stack.setCurrentIndex(0 if key == "structure" else ALL_FILES.index(key) + 1)
        if key in ALL_FILES:
            self.editors[key].edit.setFocus()

    # ================================================================ 输出
    def output_dir(self) -> Path | None:
        return self.source.parent if self.source else None

    def out_path(self, f: str) -> Path | None:
        out = self.output_dir()
        return out / f if out else None

    def _open_outdir(self):
        if self.output_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir())))

    def _copy(self, f: str):
        QApplication.clipboard().setText(self.editors[f].text())
        self.toast.show_message(f"已复制 {f}" + ("（组成）" if f == "POTCAR" else ""))

    def _confirm_overwrite(self, paths: list[Path]) -> bool:
        exist = [p for p in paths if p.exists() and self.disk.get(str(p)) is None]
        if not exist:
            return True
        names = ", ".join(p.name for p in exist)
        extra = ""
        if self.source in exist:
            extra = f"\n\n注意：{self.source.name} 是当前输入的结构文件。"
        answer = QMessageBox.question(self, "覆盖确认", f"{exist[0].parent} 中已存在 {names}，是否覆盖？{extra}")
        return answer == QMessageBox.StandardButton.Yes

    def _write(self, items: list[tuple[Path, str]]) -> bool:
        try:
            for path, text in items:
                path.write_text(text, encoding="utf-8", newline="\n")
                self.disk[str(path)] = text
        except OSError as exc:
            self.toast.show_message(f"写入失败：{exc}", error=True)
            return False
        self._update_file_states()
        return True

    def save_file(self, f: str):
        path = self.out_path(f)
        if path is None:
            self.toast.show_message("请先上传结构文件", error=True)
            return
        if self.errors[f]:
            self.toast.show_message(f"{f} 有问题，未保存：{self.errors[f][0]}", error=True)
            return
        if self._confirm_overwrite([path]) and self._write([(path, self.content(f))]):
            self.toast.show_message(f"已保存 {path}")

    def save_file_as(self, f: str):
        if not self.content(f):
            self.toast.show_message(f"{f} 为空", error=True)
            return
        start = str(self.out_path(f) or Path(str(self.settings.value("last_dir", Path.cwd()))) / f)
        path, _ = QFileDialog.getSaveFileName(self, f"另存 {f}", start, "所有文件 (*)")
        if path and self._write([(Path(path), self.content(f))]):
            self.toast.show_message(f"已保存 {path}")

    def generate(self):
        out = self.output_dir()
        if out is None or not self.gen_btn.isEnabled():
            return
        selected = self.selected_files()
        if not self._confirm_overwrite([out / f for f in selected]):
            return
        if self._write([(out / f, self.content(f)) for f in selected]):
            set_hint(self.status, f"已写入 {out} · {' '.join(selected)}", C.GREEN)
            self.toast.show_message(f"已生成 {len(selected)} 个文件")


def main():
    import os
    if sys.stdout is None or sys.stderr is None:  # 无控制台的便携版：print 到 stderr 会因 None 出错
        sys.stdout = sys.stdout or open(os.devnull, "w", encoding="utf-8")
        sys.stderr = sys.stderr or open(os.devnull, "w", encoding="utf-8")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GenerateInput.IncarGUI")
        except Exception:
            pass
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("VASP 作业文件生成器")
    app.setWindowIcon(QIcon(str(ICON)))
    apply_theme(app)
    window = Window()
    window.show()
    if len(sys.argv) > 1:
        window.load_structure(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
