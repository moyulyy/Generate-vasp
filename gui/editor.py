# -*- coding: utf-8 -*-
"""VASP 输入文件编辑器：语法高亮、查找（全部匹配高亮）、当前行高亮。"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

MAX_MARKS = 3000


def _fmt(color: str, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    return f


class VaspHighlighter(QSyntaxHighlighter):
    TAG = re.compile(r"^\s*([A-Za-z_]\w*)\s*=")
    FLAG = re.compile(r"(?<=\s)([TF])(?=\s|$)")

    def __init__(self, document, kind: str):
        super().__init__(document)
        self.kind = kind
        self.tag = _fmt("#64D2FF", bold=True)
        self.comment = _fmt("#7C7C80")
        self.header = _fmt("#FF9F0A", bold=True)
        self.label = _fmt("#BF5AF2", bold=True)
        self.true = _fmt("#30D158", bold=True)
        self.false = _fmt("#FF6961", bold=True)

    def _tail_comment(self, text: str, chars: str = "#!"):
        cut = min((i for i in (text.find(c) for c in chars) if i >= 0), default=-1)
        if cut >= 0:
            self.setFormat(cut, len(text) - cut, self.comment)

    def highlightBlock(self, text: str):
        s = text.strip()
        if not s:
            return
        n = self.currentBlock().blockNumber()
        getattr(self, "_" + self.kind.lower().replace(".", "_"))(text, s, n)

    def _incar(self, text, s, n):
        if s[0] in "#!":
            self.setFormat(0, len(text), self.comment)
            return
        m = self.TAG.match(text)
        if m:
            self.setFormat(m.start(1), len(m.group(1)), self.tag)
        else:
            self.setFormat(0, len(text), self.header)
        self._tail_comment(text)

    def _kpoints(self, text, s, n):
        if n == 0:
            self.setFormat(0, len(text), self.comment)
            return
        if s[0].isalpha():
            self.setFormat(0, len(text), self.header)
        cut = text.find("!")
        if cut >= 0:
            self.setFormat(cut, len(text) - cut, self.label)

    def _poscar(self, text, s, n):
        if n == 0:
            self.setFormat(0, len(text), self.comment)
        elif n == 5 and s[0].isalpha():
            self.setFormat(0, len(text), self.tag)
        elif s[0].isalpha():
            self.setFormat(0, len(text), self.header)
        elif n > 6:
            for m in self.FLAG.finditer(text):
                self.setFormat(m.start(1), 1, self.true if m.group(1) == "T" else self.false)
            self._tail_comment(text, "#!")

    def _lobsterin(self, text, s, n):
        if s[0] in "!#":
            self.setFormat(0, len(text), self.comment)
            return
        start = len(text) - len(text.lstrip())
        word = s.split()[0]
        self.setFormat(start, len(word), self.tag)
        for kw in ("atom", "and", "orbitalwise"):
            for m in re.finditer(rf"\b{kw}\b", text):
                self.setFormat(m.start(), len(kw), self.label)

    def _optcell(self, text, s, n):
        for i, ch in enumerate(text):
            if ch in "01":
                self.setFormat(i, 1, self.true if ch == "1" else self.false)

    def _potcar(self, text, s, n):
        """POTCAR 页只含组成：标签行 + 注释。"""
        if s[0] == "#":
            self.setFormat(0, len(text), self.comment)
            return
        start = len(text) - len(text.lstrip())
        self.setFormat(start, len(s.split()[0]), self.tag)
        self._tail_comment(text, "#")


class FileEditor(QWidget):
    """单个文件的编辑页：工具条 + 查找栏 + 文本区。"""
    edited = Signal(str)
    saveRequested = Signal(str)
    saveAsRequested = Signal(str)
    revertRequested = Signal(str)
    copyRequested = Signal(str)

    def __init__(self, name: str, placeholder: str = ""):
        super().__init__()
        self.name = name
        self._guard = False
        self._marks: list[QTextCursor] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.state = QLabel()
        self.state.setObjectName("StateChip")
        self.info = QLabel()
        self.info.setObjectName("Hint")
        self.info.setWordWrap(True)
        bar.addWidget(self.state)
        bar.addStretch()
        self.revert_btn = self._button("恢复自动生成", lambda: self.revertRequested.emit(self.name))
        for w in (self.revert_btn, self._button("查找", self.open_find),
                  self._button("复制", lambda: self.copyRequested.emit(self.name)),
                  self._button("另存为…", lambda: self.saveAsRequested.emit(self.name)),
                  self._button("保存", lambda: self.saveRequested.emit(self.name))):
            bar.addWidget(w)
        v.addLayout(bar)
        v.addWidget(self.info)

        self.find_bar = QFrame()
        self.find_bar.setObjectName("FindBar")
        fh = QHBoxLayout(self.find_bar)
        fh.setContentsMargins(10, 6, 6, 6)
        fh.setSpacing(6)
        self.query = QLineEdit()
        self.query.setPlaceholderText("查找…   Enter 下一个 · Shift+Enter 上一个 · Esc 关闭")
        self.query.textChanged.connect(self._search)
        self.query.returnPressed.connect(self.find_next)
        self.case = QPushButton("Aa")
        self.case.setObjectName("FileToggle")
        self.case.setCheckable(True)
        self.case.setToolTip("区分大小写")
        self.case.toggled.connect(self._search)
        self.count = QLabel()
        self.count.setObjectName("Hint")
        self.count.setMinimumWidth(64)
        fh.addWidget(self.query, 1)
        fh.addWidget(self.case)
        fh.addWidget(self.count)
        fh.addWidget(self._button("↑", self.find_prev))
        fh.addWidget(self._button("↓", self.find_next))
        fh.addWidget(self._button("✕", self.close_find))
        self.find_bar.setVisible(False)
        v.addWidget(self.find_bar)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self.query, self.close_find, context=Qt.ShortcutContext.WidgetShortcut)
        QShortcut(QKeySequence("Shift+Return"), self.query, self.find_prev, context=Qt.ShortcutContext.WidgetShortcut)

        self.edit = QPlainTextEdit()
        self.edit.setObjectName("Preview")
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setTabChangesFocus(False)
        self._hl = VaspHighlighter(self.edit.document(), name)
        self.edit.textChanged.connect(self._changed)
        self.edit.cursorPositionChanged.connect(self._paint)
        v.addWidget(self.edit, 1)
        QShortcut(QKeySequence.StandardKey.FindNext, self, self.find_next,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.StandardKey.FindPrevious, self, self.find_prev,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

        self._research = QTimer(self)
        self._research.setSingleShot(True)
        self._research.setInterval(200)
        self._research.timeout.connect(self._search)

    @staticmethod
    def _button(text, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("Ghost")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    # ---------------------------------------------------------- 内容
    def text(self) -> str:
        return self.edit.toPlainText()

    def set_text(self, text: str, undoable: bool = False):
        """程序更新内容（不触发 edited），尽量保持滚动位置与光标。

        undoable：作为一步可撤销的编辑写入（合并进手动编辑的内容时用，Ctrl+Z 可撤回），否则重置撤销历史。
        """
        if text == self.edit.toPlainText():
            return
        bar = self.edit.verticalScrollBar()
        scroll, pos = bar.value(), self.edit.textCursor().position()
        self._guard = True
        if undoable:
            cur = QTextCursor(self.edit.document())
            cur.beginEditBlock()
            cur.select(QTextCursor.SelectionType.Document)
            cur.insertText(text)
            cur.endEditBlock()
        else:
            self.edit.setPlainText(text)
        self._guard = False
        cur = self.edit.textCursor()
        cur.setPosition(min(pos, len(text)))
        self.edit.setTextCursor(cur)
        bar.setValue(scroll)
        self._search()

    def set_status(self, manual: bool, saved: bool, info: str, error: bool = False):
        self.state.setText(("手动编辑" if manual else "自动生成") + (" · 已保存" if saved else " · 未保存"))
        if self.state.property("manual") != manual:
            self.state.setProperty("manual", manual)
            self.state.style().unpolish(self.state)
            self.state.style().polish(self.state)
        self.revert_btn.setVisible(manual)
        self.info.setText(info)
        self.info.setVisible(bool(info))
        style = "color: #FF3B30;" if error else ""
        if self.info.styleSheet() != style:
            self.info.setStyleSheet(style)

    def _changed(self):
        if not self._guard:
            self.edited.emit(self.name)
        if self.find_bar.isVisible():
            self._research.start()
        else:
            self._paint()

    # ---------------------------------------------------------- 查找
    def open_find(self):
        sel = self.edit.textCursor().selectedText()
        self.find_bar.setVisible(True)
        if sel and " " not in sel:
            self.query.setText(sel)
        self.query.setFocus()
        self.query.selectAll()
        self._search()

    def close_find(self):
        self.find_bar.setVisible(False)
        self._marks = []
        self._paint()
        self.edit.setFocus()

    def _flags(self, backward: bool = False):
        flags = QTextDocument.FindFlag(0)
        if self.case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        return flags

    def _search(self, *_):
        self._marks = []
        q = self.query.text()
        if self.find_bar.isVisible() and q:
            doc, cur = self.edit.document(), QTextCursor(self.edit.document())
            while len(self._marks) < MAX_MARKS:
                cur = doc.find(q, cur, self._flags())
                if cur.isNull():
                    break
                self._marks.append(QTextCursor(cur))
        self._paint()

    def _jump(self, backward: bool):
        q = self.query.text()
        if not q:
            return
        doc = self.edit.document()
        cur = doc.find(q, self.edit.textCursor(), self._flags(backward))
        if cur.isNull():  # 回绕
            start = QTextCursor(doc)
            if backward:
                start.movePosition(QTextCursor.MoveOperation.End)
            cur = doc.find(q, start, self._flags(backward))
        if not cur.isNull():
            self.edit.setTextCursor(cur)
            self.edit.centerCursor()

    def find_next(self):
        if not self.find_bar.isVisible():
            self.open_find()
        self._jump(False)

    def find_prev(self):
        if not self.find_bar.isVisible():
            self.open_find()
        self._jump(True)

    def _paint(self):
        """当前行 + 全部匹配 + 当前匹配三层高亮。"""
        sels = []
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(QColor("#2C2C2E"))
        line.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
        line.cursor = self.edit.textCursor()
        line.cursor.clearSelection()
        sels.append(line)
        cur = self.edit.textCursor()
        current = 0
        for i, mark in enumerate(self._marks):
            sel = QTextEdit.ExtraSelection()
            sel.cursor = mark
            hit = mark.selectionStart() == cur.selectionStart() and mark.selectionEnd() == cur.selectionEnd()
            if hit:
                current = i + 1
                sel.format.setBackground(QColor("#FF9F0A"))
                sel.format.setForeground(QColor("#1C1C1E"))
            else:
                sel.format.setBackground(QColor("#5C4A12"))
            sels.append(sel)
        self.edit.setExtraSelections(sels)
        if self.find_bar.isVisible():
            n = len(self._marks)
            more = "+" if n >= MAX_MARKS else ""
            self.count.setText(f"{current}/{n}{more}" if n else ("无匹配" if self.query.text() else ""))
            self.count.setStyleSheet("color: #FF3B30;" if self.query.text() and not n else "")
