# -*- coding: utf-8 -*-
"""iOS 风格基础控件。"""
from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QButtonGroup, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
)


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("HLine")
    f.setFixedHeight(1)
    return f


def set_hint(lab: QLabel, text: str, color: str = ""):
    """只在内容变化时更新：每次 refresh 都会调用，重复 setStyleSheet 会触发重新布局与重绘。"""
    style = f"color: {color};" if color else ""
    if lab.text() != text:
        lab.setText(text)
    if lab.styleSheet() != style:
        lab.setStyleSheet(style)
    lab.setVisible(bool(text))


def clear_layout(layout):
    while layout.count():
        w = layout.takeAt(0).widget()
        if w:
            w.hide()
            w.deleteLater()


class _NoWheel:
    """滚轮不修改数值 / 选项，交给外层滚动区域滚动页面；只能用键盘输入。"""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(_NoWheel, QSpinBox):
    pass


class NoWheelDoubleSpinBox(_NoWheel, QDoubleSpinBox):
    pass


class NoWheelComboBox(_NoWheel, QComboBox):
    pass


def combo() -> QComboBox:
    c = NoWheelComboBox()
    c.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return c


def dspin(lo, hi, step, value, decimals=3, width=100) -> QDoubleSpinBox:
    s = NoWheelDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    s.setFixedWidth(width)
    s.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    return s


def ispin(lo, hi, value, width=80) -> QSpinBox:
    s = NoWheelSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setFixedWidth(width)
    s.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    return s


class Card(QFrame):
    """白色圆角卡片；step 为空时不显示编号。head 可追加右侧控件，body 放内容。"""

    def __init__(self, step=None, title=""):
        super().__init__()
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 16, 20, 18)
        self.body.setSpacing(10)
        self.head = QHBoxLayout()
        self.head.setSpacing(10)
        if step:
            badge = label(step, "StepBadge")
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.head.addWidget(badge)
        if title:
            self.head.addWidget(label(title, "CardTitle"))
        self.head.addStretch()
        self.body.addLayout(self.head)


class SubPanel(QFrame):
    """卡片内的浅灰次级面板，用于展开的详细参数。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("SubPanel")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(14, 10, 14, 10)
        self.lay.setSpacing(10)

    def add_field(self, text: str, field: QWidget):
        self.lay.addWidget(label(text, "Hint"))
        self.lay.addWidget(field)
        self.lay.addSpacing(8)


class Switch(QAbstractButton):
    """iOS 开关。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(46, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float):
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def sizeHint(self):
        return QSize(46, 28)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        off, on, t = QColor("#E5E5EA"), QColor("#34C759"), self._offset
        track = QColor(int(off.red() + (on.red() - off.red()) * t),
                       int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        d = h - 4
        x = 2 + t * (w - d - 4)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, 3, d, d))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 2, d, d))


class Segmented(QFrame):
    """iOS 分段控件（单选）。"""
    changed = Signal(str)

    def __init__(self, items, default):
        super().__init__()
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self.buttons = {}
        for key, text in items:
            b = QPushButton(text)
            b.setObjectName("Segment")
            b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)  # 不压缩到文字以下
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.toggled.connect(lambda on, k=key: on and self.changed.emit(k))
        self.buttons[default].setChecked(True)

    def value(self) -> str:
        return next(k for k, b in self.buttons.items() if b.isChecked())


class ProjectCard(QPushButton):
    """计算项目磁贴：编号 + 名称。"""

    def __init__(self, no: str, title: str):
        super().__init__()
        self.setObjectName("ProjectCard")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(46)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 10, 0)
        lay.setSpacing(9)
        self._no = label(no, "ProjectNo")
        self._no.setFixedSize(26, 20)
        self._no.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = label(title, "ProjectTitle")
        for w in (self._no, self._title):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(w)
        lay.addStretch()
        self.toggled.connect(self._restyle)

    def _restyle(self, on: bool):
        for w in (self._no, self._title):
            w.setProperty("on", on)
            repolish(w)


class DropZone(QFrame):
    """点击或拖拽文件的虚线上传区。"""
    clicked = Signal()
    dropped = Signal(str)

    def __init__(self, text: str, sub: str = ""):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(118)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(4)
        for w in (label("📄", "DropIcon"), label(text, "DropText"), label(sub, "Hint")):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            v.addWidget(w)

    def _set_drag(self, on: bool):
        self.setProperty("drag", on)
        repolish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag(True)

    def dragLeaveEvent(self, _):
        self._set_drag(False)

    def dropEvent(self, event):
        self._set_drag(False)
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.dropped.emit(urls[0])


class Spinner(QWidget):
    """iOS 活动指示器：12 根辐条，亮度依次递减并顺时针转动。start() 显示并转动，stop() 隐藏。"""

    def __init__(self, size: int = 18, color: str = "#007AFF"):
        super().__init__()
        self.setFixedSize(size, size)
        self._color = QColor(color)
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        self._step = 0
        self.show()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._step = (self._step + 1) % 12
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.width() / 2
        p.translate(r, r)
        color = QColor(self._color)
        for i in range(12):
            color.setAlphaF(1.0 - ((self._step - i) % 12) / 13)
            p.setPen(QPen(color, max(1.6, r / 4.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(0, -r * 0.45), QPointF(0, -r * 0.85))
            p.rotate(30)


class ResultBox(QFrame):
    """输出区：标题行（活动指示器 + 状态）+ 只读正文。

    正文高度随内容增长，超过 max_body 后在框内滚动；长文本在框内换行，不撑宽外层布局。
    """

    def __init__(self, max_body: int = 200):
        super().__init__()
        self.setObjectName("ResultBox")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 9, 12, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.spinner = Spinner(15)
        self.title = label("", "ResultTitle")
        head.addWidget(self.spinner)
        head.addWidget(self.title, 1)
        v.addLayout(head)
        self.body = QTextBrowser()
        self.body.setObjectName("ResultBody")
        self.body.setOpenLinks(False)
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.body.setWordWrapMode(self.body.wordWrapMode().WrapAtWordBoundaryOrAnywhere)
        self.body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.body.document().setDocumentMargin(0)
        self.body.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._max = max_body
        v.addWidget(self.body)

    def set_title(self, text: str, color: str = ""):
        set_hint(self.title, text, color)
        self.title.setVisible(True)

    def set_html(self, html: str):
        self.body.setHtml(html)
        self.body.setVisible(bool(html))
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self, *_):
        h = int(self.body.document().size().height()) + 2
        self.body.setFixedHeight(max(18, min(h, self._max)))


class Toast(QLabel):
    """底部浮动提示，约 2 秒后自动消失。"""

    def __init__(self, parent: QWidget, bottom_gap: int = 90):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._gap = bottom_gap
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text: str, error: bool = False):
        self.setText(text)
        self.setProperty("error", error)
        repolish(self)
        self.adjustSize()
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - self._gap)
        self.raise_()
        self.show()
        self._timer.start(2200)


class PopupFrame(QWidget):
    """无边框窗口的外框：透明边距里画柔和阴影与圆角面板，边距同时作为缩放手柄。"""
    MARGIN = 14
    RADIUS = 14

    def __init__(self, bg: str, line: str):
        super().__init__()
        self.setMouseTracking(True)
        self._bg, self._line = QColor(bg), QColor(line)
        self.maximized = False
        self.lay = QVBoxLayout(self)
        self.set_maximized(False)

    def set_maximized(self, on: bool):
        self.maximized = on
        m = 0 if on else self.MARGIN
        self.lay.setContentsMargins(m, m, m, m)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.maximized:
            p.fillRect(self.rect(), self._bg)
            return
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))  # 全透明像素会被鼠标穿透，保留 1/255 以便边缘可缩放
        panel = QRectF(self.rect()).adjusted(self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.MARGIN, 0, -1):
            alpha = int(30 * (1 - i / self.MARGIN) ** 2) + 1
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(panel.adjusted(-i, -i + 4, i, i + 4), self.RADIUS + i, self.RADIUS + i)
        p.setBrush(self._bg)
        p.setPen(QPen(self._line, 1))
        p.drawRoundedRect(panel, self.RADIUS, self.RADIUS)

    def _edges(self, pos) -> Qt.Edge:
        if self.maximized:
            return Qt.Edge(0)
        m, r = self.MARGIN, self.rect()
        edges = Qt.Edge(0)
        if pos.x() < m:
            edges |= Qt.Edge.LeftEdge
        if pos.x() > r.width() - m:
            edges |= Qt.Edge.RightEdge
        if pos.y() < m:
            edges |= Qt.Edge.TopEdge
        if pos.y() > r.height() - m:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        e = self._edges(event.position().toPoint())
        diag1 = (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
        diag2 = (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.Edge.LeftEdge | Qt.Edge.BottomEdge)
        if e in diag1:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif e in diag2:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif e & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif e & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        e = self._edges(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and e:
            self.window().windowHandle().startSystemResize(e)


class WinButton(QAbstractButton):
    """圆形窗口按钮，图形自绘（不依赖字体字形）。"""

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        close = self.kind == "close"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FF3B30" if close and hover else "#D1D1D6" if hover else "#E9E9EB"))
        p.drawEllipse(QRectF(0, 0, 30, 30))
        p.setPen(QPen(QColor("#FFFFFF" if close and hover else "#3A3A3C"), 1.4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "min":
            p.drawLine(QPointF(10, 15), QPointF(20, 15))
        elif self.kind == "max":
            p.drawRoundedRect(QRectF(10.5, 10.5, 9, 9), 1.5, 1.5)
        elif self.kind == "restore":
            p.drawRoundedRect(QRectF(9.5, 12.5, 8, 8), 1.5, 1.5)
            p.drawPolyline([QPointF(12.5, 10.5), QPointF(20.5, 10.5), QPointF(20.5, 17.5)])
        else:
            p.drawLine(QPointF(11, 11), QPointF(19, 19))
            p.drawLine(QPointF(19, 11), QPointF(11, 19))


class TitleBar(QFrame):
    """可拖动的标题栏；双击最大化 / 还原。右侧为最小化、最大化、关闭按钮。"""

    def __init__(self):
        super().__init__()
        self.setObjectName("NavBar")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(22, 0, 14, 0)
        self.lay.setSpacing(12)
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(8)
        self.max_btn = None
        for kind, tip, slot in (("min", "最小化", self._minimize), ("max", "最大化", self.toggle_max),
                                ("close", "关闭", self._close)):
            b = WinButton(kind)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            self._buttons.addWidget(b)
            if kind == "max":
                self.max_btn = b

    def finish(self):
        """在调用方添加完标题内容后放入窗口按钮。"""
        self.lay.addStretch()
        self.lay.addLayout(self._buttons)

    def set_maximized(self, on: bool):
        self.max_btn.kind = "restore" if on else "max"
        self.max_btn.update()
        self.max_btn.setToolTip("还原" if on else "最大化")
        self.setProperty("max", on)
        repolish(self)

    def _minimize(self):
        self.window().showMinimized()

    def _close(self):
        self.window().close()

    def toggle_max(self):
        w = self.window()
        w.showNormal() if w.isMaximized() else w.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_max()
