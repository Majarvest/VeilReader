# 潜读 · VeilReader

> 仓库名建议：`VeilReader`　|　中文名：潜读

**潜读（VeilReader）** 是一款为「低调阅读」而生的透明悬浮 TXT 小说阅读器。背景半透明、文字始终清晰；鼠标移入显现、移出隐身；无边框置顶，滚轮跟手滑动，像刷网页一样自然。内置书架、章节目录、书签续读、四边拉伸与老板键，9MB 长篇也能秒开如飞。

**VeilReader** is a translucent, always-on-top TXT reader built for discreet reading. Its background melts into the page while the text stays crisp; the window appears when your cursor enters and vanishes when it leaves. Scroll with the wheel and the text follows your hand, just like a web page. It packs a bookshelf, chapter index, bookmarks with auto-resume, resizable edges, and a boss key — and even a 9 MB novel opens in a flash.

## 功能特性

- **网页式跟手滚动**：滚轮转多少，文字实时移动多少，滚轮停文字立刻停；向上滚能看到之前的文字，不吞字、不丢行
- **双层透明**：仅背景半透明，文字 100% 不透明，不会像整体透明那样看不清
- **显隐控制**：鼠标移入窗口显现，移出后完全透明；滚轮仅在文字区域生效
- **无边框悬浮**：可自由拖动，四边四角可拉伸，永远置顶，老板键（Esc）一键隐藏
- **书架目录**：指定文件夹后自动扫描其中所有 TXT（含子文件夹），托盘快速切换
- **章节目录**：自动识别"第X章 / 第X回 / Chapter N / 序章 / 番外"等标题，可搜索、双击跳转、当前章高亮
- **书签与续读**：每本书自动记录阅读位置，下次打开接着读；B 设书签 / Shift+B 跳转
- **自适应排版**：拉伸窗口时文字按新宽度自动重排，内容量完全由窗口大小决定
- **可定制**：字体、字号、行距、文字颜色、背景颜色与透明度、滚动步长、自动滚屏

## 运行

需要 Python 3.8+ 与 PyQt5：

```bash
pip install -r requirements.txt
python floating_reader.py
```

启动后程序没有主窗口，屏幕右下角出现半透明悬浮窗，系统托盘区有其图标。所有操作通过托盘菜单、窗口右键菜单和快捷键完成，详见 [使用说明.txt](使用说明.txt)。

## 打包为独立 exe（Windows）

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --clean --name "浮窗阅读器" floating_reader.py
```

产物在 `dist/浮窗阅读器.exe`。配置文件 `config.json` 会在首次运行时生成在 exe 同目录，用于保存进度、书签和各项设置。

## 项目结构

| 文件 | 说明 |
| --- | --- |
| `floating_reader.py` | 全部源码（单文件实现） |
| `使用说明.txt` | 详细操作说明与快捷键 |
| `requirements.txt` | Python 依赖 |
| `浮窗阅读器.spec` | PyInstaller 打包配置（可选，可重新生成） |

## 技术实现要点

- 自绘文本视口：用 `QPainter.drawText(..., Qt.TextWordWrap)` 按窗口宽度换行，只绘制视口内可见的十余行，因此 9MB / 11 万行的长篇也能秒开、滚动流畅
- 行高表从行 0 开始、只向后追加绝不丢弃，向上滚动时始终有精确的行坐标；未计算部分用平均行高估算总高度，避免全文重算
- 滚轮事件直接把 delta 线性换算为像素位移并立即重绘，无中间目标和缓动动画，实现真正的"跟手"
- 使用 Qt 系统托盘，并保持模块级强引用，避免被垃圾回收导致闪退

## 当前版本

v7.0 跟手滚动版（2026-11-24）
