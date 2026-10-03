# -*- coding: utf-8 -*-
"""iOS 浅色外观的配色与全局样式表。"""
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


class C:
    BG = "#F2F2F7"
    CARD = "#FFFFFF"
    CARD_2 = "#F7F7FA"
    LINE = "#E5E5EA"
    LINE_STRONG = "#D1D1D6"
    FILL = "#E9E9EB"
    TEXT = "#1C1C1E"
    TEXT_2 = "#6E6E73"
    TEXT_3 = "#8E8E93"
    BLUE = "#007AFF"
    BLUE_HOVER = "#1A88FF"
    BLUE_PRESS = "#0062CC"
    BLUE_SOFT = "#E8F1FF"
    BLUE_DISABLED = "#B8D9FF"
    GREEN = "#34C759"
    RED = "#FF3B30"
    CODE_BG = "#1C1C1E"


MONO = '"Cascadia Code", "JetBrains Mono", Consolas, Menlo, monospace'

STYLESHEET = f"""
QWidget {{ color: {C.TEXT}; font-size: 13px; }}
QMainWindow {{ background: transparent; }}
#Root {{ background: {C.BG}; border-radius: 14px; }}
#Root[max="true"] {{ border-radius: 0; }}
QDialog, QMessageBox {{ background: {C.BG}; }}
QScrollArea, #Pane {{ background: transparent; border: none; }}
QSplitter::handle {{ background: transparent; }}

#NavBar {{ background: #F9F9F9; border-bottom: 1px solid {C.LINE};
    border-top-left-radius: 13px; border-top-right-radius: 13px; }}
#NavBar[max="true"] {{ border-radius: 0; }}
#NavTitle {{ font-size: 16px; font-weight: 700; }}
#NavSub {{ color: {C.TEXT_2}; font-size: 11.5px; }}

#BottomBar {{ background: {C.CARD}; border-top: 1px solid {C.LINE};
    border-bottom-left-radius: 13px; border-bottom-right-radius: 13px; }}
#BottomBar[max="true"] {{ border-radius: 0; }}
#OutPath {{ color: {C.TEXT}; font-weight: 600; }}
#StateChip {{ background: {C.FILL}; color: {C.TEXT_2}; border-radius: 8px; padding: 3px 9px; font-size: 11.5px; font-weight: 600; }}
#StateChip[manual="true"] {{ background: #FFF4E5; color: #B26A00; }}
#FindBar {{ background: {C.CARD_2}; border: 1px solid {C.LINE}; border-radius: 10px; }}
#RowKey {{ color: {C.TEXT_2}; }}

#Card {{ background: {C.CARD}; border: 1px solid {C.LINE}; border-radius: 16px; }}
#CardTitle {{ font-size: 15px; font-weight: 700; }}
#StepBadge {{ background: {C.BLUE}; color: #FFFFFF; border-radius: 11px; font-size: 12px; font-weight: 700; }}
#RowTitle {{ font-size: 13.5px; font-weight: 500; }}
#Hint {{ color: {C.TEXT_3}; font-size: 11.5px; }}
#HLine {{ background: {C.LINE}; border: none; }}
#SubPanel {{ background: {C.CARD_2}; border: 1px solid {C.LINE}; border-radius: 12px; }}
#ResultBox {{ background: {C.CARD}; border: 1px solid {C.LINE}; border-radius: 10px; }}
#ResultTitle {{ color: {C.TEXT_2}; font-size: 12px; font-weight: 600; }}
QTextBrowser#ResultBody {{ background: transparent; border: none; color: {C.TEXT}; font-size: 12px; }}
#NoteBox {{ background: {C.BLUE_SOFT}; border: 1px solid #CFE1FF; border-radius: 10px;
    padding: 8px 12px; color: #0B4FA8; font-size: 12px; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C7C7CC; border-radius: 5px; min-height: 40px; }}
QScrollBar::handle:vertical:hover {{ background: #AEAEB2; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C7C7CC; border-radius: 5px; min-width: 40px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QPushButton#Primary {{ background: {C.BLUE}; color: #FFFFFF; border: none; border-radius: 13px;
    padding: 11px 26px; font-size: 15px; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {C.BLUE_HOVER}; }}
QPushButton#Primary:pressed {{ background: {C.BLUE_PRESS}; }}
QPushButton#Primary:disabled {{ background: {C.BLUE_DISABLED}; color: #FFFFFF; }}
QPushButton#Ghost {{ background: transparent; color: {C.BLUE}; border: none; border-radius: 9px;
    padding: 6px 12px; font-weight: 600; }}
QPushButton#Ghost:hover {{ background: {C.BLUE_SOFT}; }}
QPushButton#Danger {{ background: transparent; color: {C.RED}; border: none; border-radius: 9px;
    padding: 6px 12px; font-weight: 600; }}
QPushButton#Danger:hover {{ background: #FFEAE9; }}

#Segmented {{ background: {C.FILL}; border-radius: 9px; }}
QPushButton#Segment {{ background: transparent; border: none; border-radius: 7px; padding: 6px 14px;
    color: {C.TEXT_2}; font-weight: 500; }}
QPushButton#Segment:hover {{ color: {C.TEXT}; }}
QPushButton#Segment:checked {{ background: {C.CARD}; color: {C.TEXT}; font-weight: 600; }}

QPushButton#ProjectCard {{ background: {C.CARD}; border: 1.5px solid {C.LINE}; border-radius: 12px; }}
QPushButton#ProjectCard:hover {{ border-color: {C.BLUE}; }}
QPushButton#ProjectCard:checked {{ background: {C.BLUE_SOFT}; border: 1.5px solid {C.BLUE}; }}
#ProjectNo {{ color: {C.TEXT_3}; border: 1px solid {C.LINE_STRONG}; border-radius: 6px;
    font-family: {MONO}; font-size: 11px; font-weight: 700; }}
#ProjectNo[on="true"] {{ color: #FFFFFF; background: {C.BLUE}; border-color: {C.BLUE}; }}
#ProjectTitle {{ font-size: 13.5px; font-weight: 600; }}
#ProjectTitle[on="true"] {{ color: {C.BLUE}; }}

#DropZone {{ background: {C.CARD_2}; border: 2px dashed {C.LINE_STRONG}; border-radius: 14px; }}
#DropZone:hover, #DropZone[drag="true"] {{ background: {C.BLUE_SOFT}; border-color: {C.BLUE}; }}
#DropIcon {{ font-size: 26px; }}
#DropText {{ color: {C.TEXT_2}; }}
#FileChip {{ background: {C.CARD_2}; border: 1px solid {C.LINE}; border-radius: 11px; }}
#FileName {{ color: {C.BLUE}; font-weight: 600; }}
#Chip {{ background: {C.CARD_2}; border: 1px solid {C.LINE}; border-radius: 9px; padding: 3px 10px;
    color: {C.TEXT_2}; font-size: 12px; }}
#Badge {{ background: {C.BLUE_SOFT}; color: {C.BLUE}; border-radius: 9px; padding: 3px 10px;
    font-size: 12px; font-weight: 600; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {C.CARD}; border: 1px solid {C.LINE_STRONG};
    border-radius: 9px; padding: 5px 10px; selection-background-color: {C.BLUE}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {C.BLUE}; }}
QComboBox:hover {{ border-color: {C.BLUE}; }}
QComboBox QAbstractItemView {{ background: {C.CARD}; border: 1px solid {C.LINE}; selection-background-color: {C.BLUE};
    selection-color: #FFFFFF; outline: none; }}

#ViewerFrame {{ background: #F7F7FA; border: 1px solid {C.LINE}; border-radius: 12px; }}
QPushButton#FileToggle {{ background: {C.CARD_2}; color: {C.TEXT_3}; border: 1px solid {C.LINE}; border-radius: 9px;
    padding: 5px 10px; font-family: {MONO}; font-size: 12px; font-weight: 700; }}
QPushButton#FileToggle:checked {{ background: {C.BLUE_SOFT}; color: {C.BLUE}; border-color: #CFE1FF; }}
QPushButton:disabled#Ghost {{ color: #C7C7CC; }}
#PreviewName {{ font-family: {MONO}; font-size: 14px; font-weight: 700; color: {C.BLUE}; }}
QPlainTextEdit#Preview {{ background: {C.CODE_BG}; color: #E5E5EA; border: none; border-radius: 12px;
    padding: 10px 12px; font-family: {MONO}; font-size: 12.5px;
    selection-background-color: #3A5F99; selection-color: #FFFFFF; }}
QPlainTextEdit#Preview:focus {{ border: 1px solid #3A5F99; }}

#Toast {{ background: rgba(28, 28, 30, 235); color: #FFFFFF; border-radius: 13px; padding: 10px 20px; }}
#Toast[error="true"] {{ background: {C.RED}; }}
"""


def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())  # 固定浅色，避免跟随系统深色模式
    font = QFont()
    font.setFamilies(["SF Pro Text", "PingFang SC", "Segoe UI", "Microsoft YaHei UI"])
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)
