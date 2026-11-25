# -*- coding: utf-8 -*-
"""
浮窗小说阅读器 (Floating Reader)
================================
一个透明悬浮的 TXT 小说阅读器，专为"低调阅读"设计。

核心特性：
  - 双层结构：背景半透明、文字 100% 不透明（不会像 PowerToys 那样文字一起变淡）
  - 【像素级连续滑动】自绘文本视口，滚轮按像素一丝一丝滑动，像刷网页一样顺滑，
    不是"一次跳 N 行"；相邻帧内容完全重叠，彻底消除吞字
  - 鼠标移入窗口即显示、移出完全隐藏；滚轮须对准【文字】才滑动
  - 无边框、可自由拖动、四边四角拖拽缩放、永远置顶
  - 章节目录：自动识别"第X章/第X回"标题，可搜索、双击跳转
  - 书签与续读：自动记住每本书的阅读位置，下次打开继续；B 设书签 / Shift+B 跳转
  - 导入 TXT（自动识别 UTF-8 / GBK / GB18030 编码）
  - 内容完全由窗口尺寸自适应：拉伸窗口文字自动重排（自绘 TextWordWrap）
  - 可设置字体、字号、行距、文字颜色、背景颜色、背景透明度、滑动速度
  - 自动滚屏、老板键一键隐藏
  - 配置自动保存到程序目录的 config.json
"""

import sys
import os
import json
import re
import bisect
import traceback
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QSize, QEvent
from PyQt5.QtGui import (
    QFont, QColor, QCursor, QIcon, QPainter, QFontMetrics, QTextOption
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QSlider, QSpinBox, QCheckBox, QColorDialog, QFontDialog, QFileDialog,
    QSystemTrayIcon, QMenu, QAction, QMessageBox, QFrame, QStyle,
    QListWidget, QListWidgetItem, QLineEdit
)

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_PATH = APP_DIR / "config.json"

VERSION = "v7.0 跟手滚动版 (2026-11-24 22:20)"

DEFAULT_CONFIG = {
    "bg_color": "#1A1A1A",
    "bg_alpha": 55,
    "text_color": "#E8E8E8",
    "font_family": "Microsoft YaHei",
    "font_size": 14,
    "bold": False,
    "line_spacing": 6,
    "scroll_px": 40,              # 滚轮每格滑动的像素数（越小越跟手、越细腻）
    "hide_opacity": 0,
    "show_opacity": 100,
    "always_on_top": True,
    "auto_scroll": False,
    "scroll_ms": 30,
    "scroll_speed": 1.0,
    "click_through_hidden": True,
    "last_file": "",
    "progress": {},
    "scroll_pos": {},
    "bookmarks": {},
    "bookmark_scroll": {},
    "library_dir": "",
    "window_x": 60,
    "window_y": 60,
    "window_w": 420,
    "window_h": 200,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    for key in ("progress", "scroll_pos", "bookmarks", "bookmark_scroll"):
        if not isinstance(cfg.get(key), dict):
            cfg[key] = {}
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def read_text_file(path):
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_CHAPTER_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节回篇部卷集折出场幕].{0,40}"
    r"|[Cc]hapter\s+\d+.{0,40}"
    r"|(?:序章|楔子|尾声|番外|前言|后记|引子|自序).{0,40}"
    r")"
)


def parse_chapters(lines):
    chapters = []
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or len(s) > 45 or s[-1:] in "。？！，,.?!;；:：":
            continue
        if _CHAPTER_RE.match(s):
            chapters.append((i, s))
    out = []
    for item in chapters:
        if not out or item[1] != out[-1][1]:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# 自绘文本视口：像素级连续滑动
# ---------------------------------------------------------------------------
class TextCanvas(QWidget):
    """自绘小说文本视口 —— 双向可靠的像素滚动 + 网页式缓动。

    - _y0[k]：第 k 行的起始 Y（像素），从行 0 开始的密集表，单调递增。
      只往后追加、绝不丢弃已有条目 → 向上滚时永远有精确数据，文字不会消失。
    - _computed：已精确测量的行数。未算到的尾部用"已算区平均行高"估算总高度，
      O(1)，所以 11 万行的书也不会卡。
    - scroll_y：当前显示位置（像素）。滚轮 delta 直接、立即改它 —— 真正跟手，
      滚轮转多少文字动多少，滚轮停文字停，没有"自动滑一段"的动画。
    - paintEvent：二分定位可见行，drawText(TextWordWrap) 按窗口宽度精确换行。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lines = []
        self.font = QFont("Microsoft YaHei", 14)
        self.color = QColor("#E8E8E8")
        self.line_spacing = 6
        self.pad_x = 10
        self.pad_top = 8
        self.pad_bottom = 8
        self.scroll_y = 0.0        # 显示位置（像素），滚轮直接改它
        self._y0 = [0.0]           # _y0[k] = 行 k 起始 Y；_y0[0]=0
        self._computed = 0         # 已精确测量的行数
        self._line_h = 20
        self._anchor_line = 0
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.timeout.connect(self._do_relayout)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setMouseTracking(True)

    # ---------- 样式 / 内容 ----------
    def set_style(self, font, color, spacing):
        self.font = font
        self.color = color
        self.line_spacing = int(spacing)
        self._reset_layout()
        self.update()

    def set_text(self, lines):
        self.lines = lines
        self._reset_layout()
        self.update()

    def _reset_layout(self):
        """字体/宽度/内容变化 → 整表失效，从头重来（调用方负责恢复当前行）。"""
        self._y0 = [0.0]
        self._computed = 0
        self._line_h = QFontMetrics(self.font).height() + self.line_spacing
        self.scroll_y = 0.0

    # ---------- 行高表 ----------
    def _measure_rows(self, text):
        if not text:
            return 1
        w = max(20, self.width() - 2 * self.pad_x)
        rect = QRect(0, 0, w, 1000000)
        r = QFontMetrics(self.font).boundingRect(rect, Qt.TextWordWrap, text)
        return max(1, (r.height() + self._line_h - 1) // self._line_h)

    def _compute_to(self, n):
        """精确测量到行 n（生成行 n 的起始 Y）；只追加，不重算已有。"""
        n = max(0, min(int(n), len(self.lines)))
        if n <= self._computed:
            return
        lh = self._line_h
        for i in range(self._computed, n):
            self._y0.append(self._y0[-1] + self._measure_rows(self.lines[i]) * lh)
        self._computed = n

    def _avg_row_h(self):
        if self._computed <= 0:
            return float(self._line_h)
        return self._y0[-1] / self._computed

    def _y_at_line(self, line_no, exact=True):
        if not self.lines:
            return 0.0
        n = max(0, min(int(line_no), len(self.lines)))
        if exact:
            self._compute_to(n)
        if n <= self._computed:
            return self._y0[n]
        return self._y0[-1] + (n - self._computed) * self._avg_row_h()

    def _line_at_y(self, y):
        if not self.lines:
            return 0
        if y <= self._y0[-1]:
            return max(0, min(bisect.bisect_right(self._y0, y) - 1, len(self.lines) - 1))
        extra = (y - self._y0[-1]) / self._avg_row_h()
        return max(0, min(self._computed + int(extra), len(self.lines) - 1))

    def _total_height(self):
        return self._y_at_line(len(self.lines), exact=False)

    def _max_scroll(self):
        return max(0.0, self._total_height() - self.height() + self.pad_top + self.pad_bottom)

    # ---------- 滚动：滚轮直接跟手 ----------
    def scroll_px(self, delta):
        """滚轮 delta 直接、立即变成像素位移 —— 滚轮停文字立刻停，无自动滑行。"""
        new = max(0.0, min(self._max_scroll(), self.scroll_y + float(delta)))
        if new != self.scroll_y:
            self.scroll_y = new
            self.update()

    def scroll_to_pixel(self, y):
        """跳转/续读用：先精确算到目标行，保证定位准、向上滚也有数据。"""
        y = max(0.0, float(y))
        self._compute_to(min(len(self.lines), self._line_at_y(y) + 40))
        self.scroll_y = min(y, self._max_scroll())
        self.update()

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        if not self.lines:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(self.font)
        painter.setPen(self.color)

        w = max(20, self.width() - 2 * self.pad_x)
        h = self.height()
        lh = self._line_h
        sy = min(self.scroll_y, self._max_scroll())
        metrics = QFontMetrics(self.font)
        rect = QRect(0, 0, w, 1000000)

        top_line = max(0, self._line_at_y(sy) - 2)
        self._anchor_line = top_line
        visible = max(1, h // lh + 6)
        # 只精确测量"可见区 + 余量"，不全文重算
        self._compute_to(min(len(self.lines), top_line + visible + 6))
        if top_line > self._computed:
            top_line = max(0, self._computed - 2)

        y = self.pad_top + self._y0[top_line] - sy
        i = top_line
        while i < len(self.lines) and y <= h + lh:
            text = self.lines[i]
            if text:
                r = metrics.boundingRect(rect, Qt.TextWordWrap, text)
                n = max(1, (r.height() + lh - 1) // lh)
                painter.drawText(
                    QRect(self.pad_x, int(y), w, n * lh),
                    Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, text
                )
            else:
                n = 1
            y += n * lh
            i += 1
        painter.end()

    def _do_relayout(self):
        """拖动停止后：按当前行重建整表，保证之后上下滚都精确。"""
        cur_line = self._line_at_y(self.scroll_y)
        self._reset_layout()
        self._compute_to(min(len(self.lines), cur_line + 40))
        self.scroll_y = min(self._y0[cur_line], self._max_scroll())
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.lines:
            return
        # 拖动中用旧表继续绘制（不重算 → 流畅），停 160ms 后统一重建
        self._relayout_timer.start(160)
        self.update()


class FloatingReader(QWidget):
    """悬浮阅读窗：无边框、置顶、背景半透明、文字清晰、像素级连续滑动。"""

    EDGE = 12
    MIN_W = 160
    MIN_H = 96

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.lines = []
        self.chapters = []
        self._drag_pos = None
        self._hidden = False
        self._awake = False
        self._resize_edge = None
        self._resize_origin = None

        flags = Qt.FramelessWindowHint | Qt.Tool
        if cfg["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.setGeometry(cfg["window_x"], cfg["window_y"], cfg["window_w"], cfg["window_h"])
        self.setMouseTracking(True)

        self._build_ui()
        self.setMinimumSize(QSize(self.MIN_W, self.MIN_H))
        self._apply_style()
        self._apply_window_state()
        self._load_last_file()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._auto_scroll_tick)
        self.apply_auto_scroll()

        self.hover_timer = QTimer(self)
        self.hover_timer.timeout.connect(self._poll_hover)
        self.hover_timer.start(120)

        self.progress_timer = QTimer(self)
        self.progress_timer.setSingleShot(True)
        self.progress_timer.timeout.connect(lambda: save_config(self.cfg))

    # ---------- UI ----------
    def _build_ui(self):
        self.panel = QWidget(self)
        self.panel.setObjectName("panel")
        self.text_view = TextCanvas(self.panel)
        self.text_view.setObjectName("text")
        self._position_children()

        self.set_context_menu_policy()
        self.panel.setMouseTracking(True)
        self.panel.installEventFilter(self)
        self.text_view.installEventFilter(self)

    def set_context_menu_policy(self):
        for w in (self, self.panel, self.text_view):
            w.setContextMenuPolicy(Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos):
        w = self.sender()
        if w is not None and w is not self:
            pos = w.mapTo(self, pos)
        self.show_context_menu(pos)

    def _apply_style(self):
        c = QColor(self.cfg["bg_color"])
        a = int(self.cfg["bg_alpha"])
        self.panel.setStyleSheet(
            "#panel { background-color: rgba(%d,%d,%d,%d); border-radius: 10px; }"
            % (c.red(), c.green(), c.blue(), a)
        )
        font = QFont(self.cfg["font_family"], int(self.cfg["font_size"]))
        font.setBold(bool(self.cfg["bold"]))
        self.text_view.set_style(font, QColor(self.cfg["text_color"]), self.cfg["line_spacing"])

    def _apply_window_state(self):
        self.setWindowOpacity(int(self.cfg["show_opacity"]) / 100.0)
        flags = self.windowFlags()
        if self.cfg["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def apply_auto_scroll(self):
        if self.cfg.get("auto_scroll"):
            self.timer.start(max(16, int(self.cfg.get("scroll_ms", 30))))
        else:
            self.timer.stop()

    # ---------- 文件 ----------
    def _load_last_file(self):
        p = self.cfg.get("last_file", "")
        if p and Path(p).exists():
            self.load_file(p)

    def load_file(self, path, restore=True):
        try:
            text = read_text_file(path)
        except Exception as e:
            QMessageBox.warning(self, "读取失败", str(e))
            return
        self.lines = [ln.rstrip("\r") for ln in text.splitlines()]
        self.chapters = parse_chapters(self.lines)
        self.cfg["last_file"] = path
        save_config(self.cfg)
        self.text_view.set_text(self.lines)

        if restore:
            target = None
            old = self.cfg.get("progress", {}).get(path)
            old_line = None
            if old is not None:
                try:
                    old_line = max(0, int(old))
                except (ValueError, TypeError):
                    pass
            sp = self.cfg.get("scroll_pos", {}).get(path)
            # 兼容旧版：旧版 scroll_pos 存的是"行号"（与 progress 同量级），
            # 新版存的是"像素"（约为行号的十几倍）。若像素值小于行号的 3 倍，
            # 判定为旧格式，改用 progress 的行号恢复。
            sp_is_pixel = False
            if sp is not None:
                try:
                    sp_val = float(sp)
                    sp_is_pixel = not (old_line is not None and old_line > 0 and sp_val <= old_line * 3)
                except (ValueError, TypeError):
                    pass
            if sp_is_pixel:
                target = ("px", sp_val)
            elif old_line is not None:
                target = ("line", old_line)
            if target is not None:
                self._restore_position(target)
        try:
            rebuild_tray_menu()
        except Exception:
            pass

    # ---------- 滚动（像素级，滚轮直接跟手）----------
    def scroll_by_px(self, delta):
        if not self.lines:
            return
        self.text_view.scroll_px(delta)
        self._remember_progress()

    def scroll_down(self, steps=1):
        self.scroll_by_px(int(self.cfg.get("scroll_px", 100)) * steps)

    def scroll_up(self, steps=1):
        self.scroll_by_px(-int(self.cfg.get("scroll_px", 100)) * steps)

    def next_page(self):
        self.scroll_down(1)

    def prev_page(self):
        self.scroll_up(1)

    def _auto_scroll_tick(self):
        self.text_view.scroll_px(float(self.cfg.get("scroll_speed", 1.0)))
        self._remember_progress()

    def _restore_position(self, target):
        kind, value = target
        if kind == "px":
            self.text_view.scroll_to_pixel(value)
        else:
            self.text_view.scroll_to_pixel(self.text_view._y_at_line(value, exact=True))

    def _current_line_no(self):
        return self.text_view._line_at_y(self.text_view.scroll_y)

    def _remember_progress(self):
        path = self.cfg.get("last_file", "")
        if not path or not self.lines:
            return
        self.cfg.setdefault("scroll_pos", {})[path] = round(self.text_view.scroll_y, 1)
        self.cfg.setdefault("progress", {})[path] = self._current_line_no()
        self.progress_timer.start(800)

    @staticmethod
    def pad_x():
        return 6

    @staticmethod
    def pad_y():
        return 10

    def _position_children(self):
        self.panel.setGeometry(0, 0, self.width(), self.height())
        self.text_view.setGeometry(self.pad_x(), self.pad_y(),
                                   max(1, self.width() - self.pad_x() * 2),
                                   max(1, self.height() - self.pad_y() * 2))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._position_children()

    # ---------- 章节 ----------
    def jump_to_line(self, line_no):
        if not self.lines:
            return
        self.text_view.scroll_to_pixel(self.text_view._y_at_line(line_no, exact=True))
        self._remember_progress()

    def current_chapter_line(self):
        cur = self._current_line_no()
        found = -1
        for ln, _ in self.chapters:
            if ln <= cur:
                found = ln
            else:
                break
        return found

    def open_chapter_dialog(self):
        if not self.lines:
            QMessageBox.information(self, "章节目录", "请先载入 TXT 文件。")
            return
        if not self.chapters:
            QMessageBox.information(
                self, "章节目录",
                "未识别到章节标题。\n支持：第X章 / 第X回 / 第X节 / Chapter N / 序章 / 番外 等。"
            )
            return
        dlg = ChapterDialog(self.chapters, self.current_chapter_line(), self)
        dlg.exec_()

    def _text_hit_rect(self):
        return QRect(self.text_view.mapToGlobal(QPoint(0, 0)), self.text_view.size())

    # ---------- 书签 ----------
    def bookmark_line(self):
        path = self.cfg.get("last_file", "")
        if not path:
            return None
        v = self.cfg.get("bookmarks", {}).get(path)
        try:
            return int(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    def toggle_bookmark(self):
        path = self.cfg.get("last_file", "")
        if not path or not self.lines:
            QMessageBox.information(self, "书签", "请先载入 TXT 文件。")
            return
        marks = self.cfg.setdefault("bookmarks", {})
        pmarks = self.cfg.setdefault("bookmark_scroll", {})
        cur = self.bookmark_line()
        line = self._current_line_no()
        if cur is not None and abs(cur - line) <= 1:
            marks.pop(path, None)
            pmarks.pop(path, None)
            self._notify("已清除书签")
        else:
            marks[path] = line
            pmarks[path] = round(self.text_view.scroll_y, 1)
            self._notify("书签已设在第 %d 行" % (line + 1))
        save_config(self.cfg)
        try:
            rebuild_tray_menu()
        except Exception:
            pass

    def jump_to_bookmark(self):
        path = self.cfg.get("last_file", "")
        line = self.bookmark_line()
        if line is None:
            QMessageBox.information(self, "书签", "这本书还没有书签。按 B 在当前位置设置。")
            return
        px = self.cfg.get("bookmark_scroll", {}).get(path)
        if px is not None:
            self.text_view.scroll_to_pixel(float(px))
        else:
            self.text_view.scroll_to_pixel(self.text_view._y_at_line(line, exact=True))
        self._remember_progress()
        self._notify("已跳到书签（第 %d 行）" % (line + 1))

    def restart_book(self):
        path = self.cfg.get("last_file", "")
        if not path or not self.lines:
            return
        self.cfg.get("scroll_pos", {}).pop(path, None)
        self.cfg.get("progress", {}).pop(path, None)
        self.text_view.scroll_to_pixel(0)
        save_config(self.cfg)
        self._notify("已从第一行开始")

    @staticmethod
    def _notify(msg):
        try:
            if _TRAY is not None:
                _TRAY.showMessage("浮窗阅读器", msg, QSystemTrayIcon.Information, 1800)
        except Exception:
            pass

    # ---------- 书架 ----------
    def scan_library(self):
        """列书架。加缓存：目录和 mtime 不变就直接返回，避免每次开菜单都递归扫描。"""
        d = self.cfg.get("library_dir", "")
        if not d or not Path(d).is_dir():
            self._lib_cache = (None, None, [])
            return []
        try:
            mtime = os.path.getmtime(d)
        except OSError:
            mtime = None
        cache = getattr(self, "_lib_cache", None)
        if cache and cache[0] == d and cache[1] == mtime:
            return cache[2]
        try:
            files = [str(p) for p in Path(d).rglob("*")
                     if p.is_file() and p.suffix.lower() in (".txt", ".text")]
            files = sorted(files, key=lambda x: os.path.basename(x).lower())
        except Exception:
            files = []
        self._lib_cache = (d, mtime, files)
        return files

    def open_book_at(self, path):
        self.load_file(path)

    def next_book(self):
        files = self.scan_library()
        cur = self.cfg.get("last_file", "")
        i = (files.index(cur) + 1) % len(files) if cur in files else 0
        if files:
            self.open_book_at(files[i])

    def prev_book(self):
        files = self.scan_library()
        cur = self.cfg.get("last_file", "")
        i = (files.index(cur) - 1) % len(files) if cur in files else (len(files) - 1 if files else 0)
        if files:
            self.open_book_at(files[i])

    # ---------- 显隐 ----------
    def _set_hover(self, shown):
        if self._hidden:
            return
        target = self.cfg["show_opacity"] if shown else self.cfg["hide_opacity"]
        self.setWindowOpacity(int(target) / 100.0)

    def _poll_hover(self):
        if self._hidden or self._drag_pos is not None or self._resize_edge is not None:
            return
        inside = self.geometry().contains(QCursor.pos())
        if inside != self._awake:
            self._awake = inside
            self._set_hover(inside)

    def enterEvent(self, e):
        if not self._hidden and self._drag_pos is None and self._resize_edge is None:
            self._awake = True
            self._set_hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        for w in (self, self.panel, self.text_view):
            w.unsetCursor()
        super().leaveEvent(e)

    def toggle_hidden(self):
        self._hidden = not self._hidden
        if self._hidden:
            self.timer.stop()
            self._awake = False
            self.hide()
        else:
            self.show()
            self._awake = True
            self.setWindowOpacity(int(self.cfg["show_opacity"]) / 100.0)
            self.apply_auto_scroll()

    # ---------- 交互 ----------
    def _edge_at(self, pos):
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        m = self.EDGE
        left, right = x <= m, x >= w - m
        top, bottom = y <= m, y >= h - m
        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        if left:
            return "l"
        if right:
            return "r"
        if top:
            return "t"
        if bottom:
            return "b"
        return None

    @staticmethod
    def _edge_cursor(edge):
        return {
            "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
            "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
            "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
            "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
        }.get(edge, Qt.ArrowCursor)

    def _edge_at_global(self, gpos):
        g = self.geometry()
        return self._edge_at(QPoint(gpos.x() - g.left(), gpos.y() - g.top()))

    def eventFilter(self, obj, event):
        if obj not in (self.panel, self.text_view):
            return super().eventFilter(obj, event)
        t = event.type()
        if t == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._start_interaction(event.globalPos())
            return True
        if t == QEvent.MouseMove:
            if event.buttons() & Qt.LeftButton:
                self._continue_interaction(event.globalPos())
                return True
            self._update_edge_cursor(event.globalPos(), obj)
            return False
        if t == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self._end_interaction()
            return True
        if t == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
            if self._edge_at_global(event.globalPos()) is None:
                self.open_file_dialog()
            return True
        if t == QEvent.Wheel:
            self._on_wheel(event)
            return True
        return super().eventFilter(obj, event)

    def _on_wheel(self, e):
        if not self._text_hit_rect().contains(QCursor.pos()):
            return
        dy = e.angleDelta().y()
        if dy == 0:
            return
        # 真正跟手：滚轮 delta 直接、立即变成文字位移，不放大、不取整格、无自动滑行。
        # 滚轮转多少文字动多少，滚轮停文字立刻停。
        # 普通鼠标一格 120 → scroll_px 像素；触摸板小 delta → 成比例的小位移。
        px_per_notch = float(self.cfg.get("scroll_px", 40))
        self.scroll_by_px(-dy / 120.0 * px_per_notch)

    def _start_interaction(self, gpos):
        edge = self._edge_at_global(gpos)
        if edge:
            self._resize_edge = edge
            self._resize_origin = (QRect(self.geometry()), QPoint(gpos))
        else:
            self._drag_pos = QPoint(gpos) - self.frameGeometry().topLeft()

    def _continue_interaction(self, gpos):
        if self._resize_edge:
            g0, p0 = self._resize_origin
            dx = gpos.x() - p0.x()
            dy = gpos.y() - p0.y()
            g = QRect(g0)
            edge = self._resize_edge
            if "l" in edge:
                g.setLeft(min(g0.left() + dx, g0.right() - self.MIN_W))
            if "r" in edge:
                g.setRight(max(g0.right() + dx, g0.left() + self.MIN_W))
            if "t" in edge:
                g.setTop(min(g0.top() + dy, g0.bottom() - self.MIN_H))
            if "b" in edge:
                g.setBottom(max(g0.bottom() + dy, g0.top() + self.MIN_H))
            self.setGeometry(g)
        elif self._drag_pos is not None:
            self.move(QPoint(gpos) - self._drag_pos)

    def _end_interaction(self):
        changed = self._drag_pos is not None or self._resize_edge is not None
        self._drag_pos = None
        self._resize_edge = None
        self._resize_origin = None
        if changed:
            self._save_geometry()

    def _update_edge_cursor(self, gpos, obj=None):
        shape = self._edge_cursor(self._edge_at_global(gpos))
        self.setCursor(shape)
        if obj is not None:
            obj.setCursor(shape)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start_interaction(e.globalPos())
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._continue_interaction(e.globalPos())
            e.accept()
        else:
            self._update_edge_cursor(e.globalPos())

    def mouseReleaseEvent(self, e):
        self._end_interaction()

    def mouseDoubleClickEvent(self, e):
        if self._edge_at_global(e.globalPos()) is None:
            self.open_file_dialog()

    def wheelEvent(self, e):
        self._on_wheel(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Down, Qt.Key_Space):
            self.scroll_down()
        elif e.key() == Qt.Key_PageDown:
            self.scroll_down(4)
        elif e.key() == Qt.Key_Up:
            self.scroll_up()
        elif e.key() == Qt.Key_PageUp:
            self.scroll_up(4)
        elif e.key() == Qt.Key_Comma:
            self.prev_book()
        elif e.key() == Qt.Key_Period:
            self.next_book()
        elif e.key() == Qt.Key_C:
            self.open_chapter_dialog()
        elif e.key() == Qt.Key_B:
            if e.modifiers() & Qt.ShiftModifier:
                self.jump_to_bookmark()
            else:
                self.toggle_bookmark()
        elif e.key() == Qt.Key_Escape:
            self.toggle_hidden()
        else:
            super().keyPressEvent(e)

    def _save_geometry(self):
        g = self.geometry()
        self.cfg.update({
            "window_x": g.x(), "window_y": g.y(),
            "window_w": g.width(), "window_h": g.height(),
        })
        save_config(self.cfg)

    # ---------- 菜单 ----------
    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { color:#ddd; background:#252525; border:1px solid #444; }"
                           "QMenu::item:selected { background:#3a6ea5; }")
        menu.addAction("打开 TXT 文件…", self.open_file_dialog)
        menu.addAction("章节目录… (C)", self.open_chapter_dialog)
        bm = self.bookmark_line()
        if bm is None:
            menu.addAction("设置书签 (B)", self.toggle_bookmark)
        else:
            menu.addAction("跳到书签 (Shift+B) · 第%d行" % (bm + 1), self.jump_to_bookmark)
            menu.addAction("清除书签", self.toggle_bookmark)
        menu.addAction("从头开始读", self.restart_book)
        menu.addAction("向上滑动 ↑", self.prev_page)
        menu.addAction("向下滑动 ↓", self.next_page)
        books = self.scan_library()
        if books:
            menu.addSeparator()
            menu.addAction("上一本 (,)", self.prev_book)
            menu.addAction("下一本 (.)", self.next_book)
            shelf = menu.addMenu("📚 书架 (%d)" % len(books))
            cur = self.cfg.get("last_file", "")
            for b in books:
                act = shelf.addAction(os.path.basename(b))
                act.setCheckable(True)
                act.setChecked(b == cur)
                act.triggered.connect(lambda checked=False, p=b: self.open_book_at(p))
        menu.addSeparator()
        act_scroll = menu.addAction("自动滚屏")
        act_scroll.setCheckable(True)
        act_scroll.setChecked(self.cfg["auto_scroll"])
        act_scroll.triggered.connect(self.toggle_auto_scroll)
        menu.addAction("设置…", self.open_settings)
        menu.addSeparator()
        menu.addAction("隐藏 (Esc)", self.toggle_hidden)
        menu.addAction("退出", QApplication.instance().quit)
        menu.exec_(self.mapToGlobal(pos))

    def toggle_auto_scroll(self, checked):
        self.cfg["auto_scroll"] = checked
        save_config(self.cfg)
        self.apply_auto_scroll()

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 TXT 文件", self.cfg.get("last_file", ""),
            "文本文件 (*.txt *.text);;所有文件 (*)"
        )
        if path:
            self.load_file(path)

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec_() == QDialog.Accepted:
            old_line = self._current_line_no()
            self.cfg.update(dlg.result_cfg)
            save_config(self.cfg)
            self._apply_style()
            self._apply_window_state()
            self.text_view.scroll_to_pixel(self.text_view._y_at_line(old_line, exact=True))
            self.apply_auto_scroll()
            try:
                rebuild_tray_menu()
            except Exception:
                pass


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = dict(cfg)
        self.setWindowTitle("浮窗阅读器 - 设置")
        self.setMinimumWidth(380)
        self.result_cfg = None
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        def row(label, widget):
            h = QHBoxLayout()
            h.addWidget(QLabel(label))
            h.addStretch(1)
            h.addWidget(widget)
            root.addLayout(h)

        self.file_btn = QPushButton("选择 TXT 文件…")
        self.file_btn.clicked.connect(self.pick_file)
        row("小说文件:", self.file_btn)
        self.file_label = QLabel(self.cfg.get("last_file", "（未选择）"))
        self.file_label.setStyleSheet("color:#888;")
        self.file_label.setWordWrap(True)
        root.addWidget(self.file_label)

        self.dir_btn = QPushButton("选择文件夹…")
        self.dir_btn.clicked.connect(self.pick_dir)
        row("书架目录:", self.dir_btn)
        self.dir_label = QLabel(self._describe_dir())
        self.dir_label.setStyleSheet("color:#888;")
        self.dir_label.setWordWrap(True)
        root.addWidget(self.dir_label)

        root.addWidget(self._hr())

        self.bg_color_btn = QPushButton()
        self.bg_color_btn.clicked.connect(lambda: self.pick_color("bg_color", self.bg_color_btn))
        row("背景颜色:", self.bg_color_btn)
        self._paint_btn(self.bg_color_btn, self.cfg["bg_color"])

        self.bg_alpha = QSlider(Qt.Horizontal)
        self.bg_alpha.setRange(0, 255)
        self.bg_alpha.setValue(int(self.cfg["bg_alpha"]))
        row("背景不透明度:", self.bg_alpha)

        self.text_color_btn = QPushButton()
        self.text_color_btn.clicked.connect(lambda: self.pick_color("text_color", self.text_color_btn))
        row("文字颜色:", self.text_color_btn)
        self._paint_btn(self.text_color_btn, self.cfg["text_color"])

        self.font_btn = QPushButton("选择字体…")
        self.font_btn.clicked.connect(self.pick_font)
        row("字体:", self.font_btn)
        self.font_label = QLabel("%s  %d" % (self.cfg["font_family"], self.cfg["font_size"]))
        self.font_label.setStyleSheet("color:#888;")
        root.addWidget(self.font_label)

        root.addWidget(self._hr())

        hint = QLabel("阅读方式：像素级连续滑动（滚轮在文字上平滑滑动，像刷网页，不跳行）")
        hint.setStyleSheet("color:#7a9;")
        root.addWidget(hint)

        self.scroll_px = QSpinBox()
        self.scroll_px.setRange(10, 400)
        self.scroll_px.setSingleStep(10)
        self.scroll_px.setValue(int(self.cfg.get("scroll_px", 100)))
        row("每格滑动像素:", self.scroll_px)

        self.line_spacing = QSpinBox()
        self.line_spacing.setRange(0, 30)
        self.line_spacing.setValue(int(self.cfg["line_spacing"]))
        row("行间距(px):", self.line_spacing)

        root.addWidget(self._hr())

        self.show_op = QSlider(Qt.Horizontal)
        self.show_op.setRange(0, 100)
        self.show_op.setValue(int(self.cfg["show_opacity"]))
        row("移入显示:", self.show_op)

        self.hide_op = QSlider(Qt.Horizontal)
        self.hide_op.setRange(0, 100)
        self.hide_op.setValue(int(self.cfg["hide_opacity"]))
        row("移出淡至:", self.hide_op)

        self.top_chk = QCheckBox("窗口永远置顶")
        self.top_chk.setChecked(bool(self.cfg["always_on_top"]))
        root.addWidget(self.top_chk)

        self.auto_chk = QCheckBox("自动滚屏")
        self.auto_chk.setChecked(bool(self.cfg["auto_scroll"]))
        root.addWidget(self.auto_chk)

        hint = QLabel(
            "提示：滚轮须对准文字才滑动；拖窗口内部移动、拖四边四角缩放；"
            "C 章节目录；B 书签；Esc 老板键隐藏。"
        )
        hint.setStyleSheet("color:#777;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)

    @staticmethod
    def _hr():
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#444;")
        return line

    def _paint_btn(self, btn, color):
        btn.setStyleSheet("background-color:%s; color:#fff; min-width:80px;" % color)
        btn.setText(color)

    def pick_color(self, key, btn):
        c = QColorDialog.getColor(QColor(self.cfg[key]), self, "选择颜色")
        if c.isValid():
            self.cfg[key] = c.name()
            self._paint_btn(btn, c.name())

    def pick_font(self):
        f = QFont(self.cfg["font_family"], int(self.cfg["font_size"]))
        f.setBold(bool(self.cfg["bold"]))
        ok, chosen = QFontDialog.getFont(f, self, "选择字体")
        if ok:
            self.cfg["font_family"] = chosen.family()
            self.cfg["font_size"] = chosen.pointSize() if chosen.pointSize() > 0 else chosen.pixelSize()
            self.cfg["bold"] = chosen.bold()
            self.font_label.setText("%s  %d" % (self.cfg["font_family"], self.cfg["font_size"]))

    def pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 TXT 文件", "", "文本文件 (*.txt *.text);;所有文件 (*)")
        if path:
            self.cfg["last_file"] = path
            self.file_label.setText(path)

    def _describe_dir(self):
        d = self.cfg.get("library_dir", "")
        if not d or not Path(d).is_dir():
            return "（未设置；设置后自动扫描其中所有 txt）"
        try:
            n = sum(1 for p in Path(d).rglob("*")
                    if p.is_file() and p.suffix.lower() in (".txt", ".text"))
        except Exception:
            n = 0
        return "%s  ——  发现 %d 本 txt" % (d, n)

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择书架文件夹", self.cfg.get("library_dir", ""))
        if d:
            self.cfg["library_dir"] = d
            self.dir_label.setText(self._describe_dir())

    def accept(self):
        self.result_cfg = {
            "bg_color": self.cfg["bg_color"],
            "bg_alpha": self.bg_alpha.value(),
            "text_color": self.cfg["text_color"],
            "font_family": self.cfg["font_family"],
            "font_size": self.cfg["font_size"],
            "bold": self.cfg.get("bold", False),
            "scroll_px": self.scroll_px.value(),
            "line_spacing": self.line_spacing.value(),
            "show_opacity": self.show_op.value(),
            "hide_opacity": self.hide_op.value(),
            "always_on_top": self.top_chk.isChecked(),
            "auto_scroll": self.auto_chk.isChecked(),
            "last_file": self.cfg.get("last_file", ""),
            "library_dir": self.cfg.get("library_dir", ""),
        }
        if getattr(self.parent(), "cfg", None) is not None:
            new_file = self.result_cfg["last_file"]
            if new_file and new_file != self.parent().cfg.get("last_file", ""):
                self.parent().load_file(new_file)
        super().accept()


class ChapterDialog(QDialog):
    def __init__(self, chapters, current_line, parent=None):
        super().__init__(parent)
        book = os.path.basename(parent.cfg.get("last_file", "")) if parent else ""
        self.setWindowTitle("章节目录 - %s" % book)
        self.setMinimumSize(360, 420)
        self.resize(460, 560)
        self.chapters = chapters
        self.parent_reader = parent
        self._current_line = current_line

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("输入章节名筛选…")
        self.search.textChanged.connect(self._filter)
        root.addWidget(self.search)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        for line_no, title in chapters:
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, line_no)
            item.setToolTip("第 %d 行" % (line_no + 1))
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(self._jump)
        root.addWidget(self.list)

        self._highlight_current()

        hint = QLabel("双击章节即可跳转；当前所在章节已高亮。共 %d 章。" % len(chapters))
        hint.setStyleSheet("color:#777;")
        root.addWidget(hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        root.addLayout(btns)

        self.search.setFocus()

    def _filter(self, text):
        kw = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(kw) and kw not in item.text().lower())

    def _highlight_current(self):
        if self._current_line < 0:
            return
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.UserRole) == self._current_line:
                self.list.setCurrentItem(item)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                item.setBackground(QColor("#3a6ea5"))
                break

    def _jump(self, item):
        if self.parent_reader is not None:
            self.parent_reader.jump_to_line(item.data(Qt.UserRole))
        self.accept()


# ---------------------------------------------------------------------------
# 全局强引用：防止托盘/菜单被 GC 回收导致闪退
# ---------------------------------------------------------------------------
_APP = None
_WIN = None
_TRAY = None
_TRAY_MENU = None
_TRAY_ICON = None
_SHELF_MENU = None
LOG_PATH = APP_DIR / "crash.log"


def _write_log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n===== %s =====\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            f.write(msg)
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_tb):
    _write_log("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def rebuild_tray_menu():
    global _TRAY_MENU, _SHELF_MENU
    if _TRAY is None or _WIN is None:
        return
    menu = QMenu()
    menu.setStyleSheet(
        "QMenu { color:#ddd; background:#252525; border:1px solid #444; }"
        "QMenu::item:selected { background:#3a6ea5; }"
    )
    menu.addAction("导入 TXT 文件…", _WIN.open_file_dialog)
    menu.addAction("章节目录… (C)", _WIN.open_chapter_dialog)
    bm = _WIN.bookmark_line()
    if bm is None:
        menu.addAction("设置书签 (B)", _WIN.toggle_bookmark)
    else:
        menu.addAction("跳到书签 (第%d行)" % (bm + 1), _WIN.jump_to_bookmark)
    menu.addSeparator()

    books = _WIN.scan_library()
    lib_dir = _WIN.cfg.get("library_dir", "")
    if books:
        shelf = menu.addMenu("📚 书架 (%d 本)" % len(books))
        _SHELF_MENU = shelf
        cur = _WIN.cfg.get("last_file", "")
        for b in books:
            act = shelf.addAction(os.path.basename(b))
            act.setCheckable(True)
            act.setChecked(b == cur)
            act.triggered.connect(lambda checked=False, p=b: _WIN.open_book_at(p))
        shelf.addSeparator()
        shelf.addAction("下一本 (.)").triggered.connect(_WIN.next_book)
        shelf.addAction("上一本 (,)").triggered.connect(_WIN.prev_book)
    else:
        menu.addAction("📚 书架（未设置目录）").triggered.connect(_WIN.open_settings)
        if lib_dir:
            tip = menu.addAction("  目录: %s（无 txt，点设置更换）" % os.path.basename(lib_dir))
            tip.setEnabled(False)

    menu.addSeparator()
    menu.addAction("显示 / 隐藏", _WIN.toggle_hidden)
    menu.addAction("设置…", _WIN.open_settings)
    menu.addSeparator()
    menu.addAction("退出", _APP.quit if _APP else QApplication.instance().quit)
    _TRAY.setContextMenu(menu)
    _TRAY_MENU = menu


def _on_tray_activated(reason):
    if reason == QSystemTrayIcon.Trigger:
        _WIN.toggle_hidden()


def main():
    global _APP, _WIN, _TRAY, _TRAY_MENU, _TRAY_ICON

    sys.excepthook = _excepthook
    _write_log("程序启动 %s" % VERSION)
    _write_log("  配置文件: %s" % CONFIG_PATH)

    try:
        _APP = QApplication(sys.argv)
        _APP.setQuitOnLastWindowClosed(False)
        cfg = load_config()
        _write_log("  实际配置: scroll_px=%s, 窗口=%sx%s" % (
            cfg.get("scroll_px"), cfg.get("window_w"), cfg.get("window_h")))
        _APP.aboutToQuit.connect(lambda: save_config(cfg))
        _WIN = FloatingReader(cfg)
        _WIN.show()

        _TRAY = QSystemTrayIcon(_WIN)
        _TRAY_ICON = _APP.style().standardIcon(QStyle.SP_ComputerIcon)
        _TRAY.setIcon(_TRAY_ICON)
        _TRAY.setToolTip("浮窗阅读器 " + VERSION)
        rebuild_tray_menu()
        _TRAY.activated.connect(_on_tray_activated)
        _TRAY.show()
        # 启动时弹一次气泡，明确告知版本，便于确认运行的是不是最新版
        _TRAY.showMessage("浮窗阅读器 " + VERSION,
                          "滚轮实时跟手：文字随滚轮移动，滚轮停文字停",
                          QSystemTrayIcon.Information, 4000)

        _write_log("初始化完成，进入事件循环")
        code = _APP.exec_()
        _write_log("事件循环结束，退出码 %s" % code)
        sys.exit(code)
    except Exception:
        _write_log("main异常:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
