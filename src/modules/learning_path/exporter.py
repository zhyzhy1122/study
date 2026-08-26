# -*- coding: utf-8 -*-
"""
src/modules/learning_path/exporter.py
作用：学习路线导出器 —— 把 Markdown 格式的路线导出成各种格式

支持的格式：
  - Markdown (.md)：直接保存，最完整（代码、链接、格式全保留）
  - Word (.docx)：用 python-docx 转成 Word 文档，便于编辑/打印

为什么单独一个文件：
  - 导出是纯 IO 操作，和 Agent 规划逻辑无关
  - 独立出来，未来加 PDF / HTML / 图片 都只改这个文件
  - 不碰 Agent / 总控 / 中间件，零冲突

依赖：
  - MD 导出：零依赖（直接写文本文件）
  - DOCX 导出：python-docx（需 pip install python-docx）
"""

# ========== 导入部分 ==========

from pathlib import Path
# Path：跨平台路径处理，自动处理 Windows / Linux 的斜杠

import re
# re：正则表达式，用来解析 Markdown 的标题/列表/链接等结构


# ========== MD 导出（最简单） ==========

def export_markdown(route_md: str, output_path: str) -> str:
    """
    导出为 Markdown 文件（.md）

    参数:
        route_md:   学习路线的 Markdown 字符串（即 Agent 返回的结果）
        output_path: 输出文件路径，如 "output/ai_路线.md"

    返回:
        str：实际写入的文件绝对路径

    原理：
        就是"把字符串写到文件里"，因为 Agent 产出的本身就是 Markdown。
        这是最保真、最简单、零依赖的导出方式。
    """
    path = Path(output_path)
    # 把字符串路径转成 Path 对象，方便后面建目录、取绝对路径

    path.parent.mkdir(parents=True, exist_ok=True)
    # 确保父目录存在
    # parents=True：如果上级目录也不存在，一起建
    # exist_ok=True：已经存在也不报错（幂等）

    path.write_text(route_md, encoding="utf-8")
    # 用 utf-8 写入文件，保留中文、emoji、链接

    return str(path.resolve())
    # 返回绝对路径，方便调用者知道文件到底写在哪了


# ========== DOCX 导出（用 python-docx） ==========

def export_docx(route_md: str, output_path: str) -> str:
    """
    导出为 Word 文档（.docx）

    参数:
        route_md:   学习路线的 Markdown 字符串
        output_path: 输出文件路径，如 "output/ai_路线.docx"

    返回:
        str：实际写入的文件绝对路径

    依赖:
        python-docx —— 纯 Python 的 Word 文档生成库
        安装：pip install python-docx

    原理（轻量级 Markdown → DOCX 解析器）：
        逐行读 Markdown，根据行首字符判断是什么元素：
        - # / ## / ### ... → 标题（Heading 1/2/3...）
        - - ...         → 无序列表（List Bullet）
        - 1. ...        → 有序列表（List Number）
        - > ...         → 引用（Quote）
        - ---           → 空行分隔
        - 其它          → 普通段落
        链接、粗体、行内代码用正则替换成 Word 对应的样式。

    为什么自己写解析而不用 markdown 库：
        - 项目里 Markdown 结构是我们自己定的（Agent 按固定格式输出），足够规整
        - 少一个依赖（不用装 markdown / mistune）
        - 逻辑简单透明，你能逐行看懂
    """
    # ---------- 1. 检查依赖 ----------
    try:
        from docx import Document
        # Document：python-docx 的核心类，代表一个 Word 文档
        from docx.shared import Pt
        # Pt：字号单位（磅）
    except ImportError:
        # 如果没装 python-docx，抛出明确错误，告诉用户怎么装
        raise ImportError(
            "导出 DOCX 需要 python-docx 库。\n"
            "请先安装：pip install python-docx"
        )

    # ---------- 2. 初始化文档 ----------
    doc = Document()
    # 创建一个空白 Word 文档

    # 设置默认字体（保证中文显示正常）
    style = doc.styles["Normal"]
    # Normal 是默认段落样式
    style.font.name = "微软雅黑"
    # 中文字体名（Word 会按系统字体回退）
    style.font.size = Pt(11)
    # 正文字号 11 磅（Word 默认是 11）

    # ---------- 3. 逐行解析 Markdown ----------
    lines = route_md.split("\n")
    # 按换行切成一行一行的列表

    in_code_block = False
    # 标记：当前是否在代码块里（```开头/结束）

    for line in lines:
        # 遍历每一行

        stripped = line.rstrip()
        # 去掉行尾空白（保留行首空格，用来判断列表缩进）

        # ---- 处理代码块边界 ----
        if stripped.startswith("```"):
            # ``` 开头 → 代码块开始或结束
            in_code_block = not in_code_block
            # 翻转状态：在块外 → 进入块；在块内 → 退出块
            continue
            # 这一行本身不显示（只是标记）

        if in_code_block:
            # 处于代码块里 → 用等宽字体段落显示
            p = doc.add_paragraph()
            run = p.add_run(stripped)
            # add_run：往段落里加一段文本（可以单独设样式）
            run.font.name = "Consolas"
            # 等宽字体
            continue

        # ---- 空行 ----
        if not stripped:
            # 空行 → 加一个空段落，保持间距
            doc.add_paragraph("")
            continue

        # ---- 分隔线 ----
        if stripped == "---":
            # Markdown 的水平分隔线 → Word 里用一段横线（或者空段落代替）
            doc.add_paragraph("—" * 30)
            # 用 30 个破折号模拟分隔线
            # （python-docx 加真正的水平线要改 XML，比较复杂，破折号够用）
            continue

        # ---- 标题 ----
        # 用正则匹配 "# 标题" 的结构，数 # 的个数决定级别
        m = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if m:
            level = len(m.group(1))
            # group(1) 是 # 号串，长度就是标题级别
            # 1 个 # = Heading 1，2 个 # = Heading 2，以此类推
            title_text = m.group(2)
            # group(2) 是标题文字内容

            # 处理标题里的行内格式（粗体、链接）
            title_text = _strip_inline_markdown(title_text)
            # 标题里一般不保留链接/粗体，先简化成纯文本
            doc.add_heading(title_text, level=level)
            # 添加标题（Word 自带 Heading 样式，自动带编号/目录支持）
            continue

        # ---- 引用 ----
        if stripped.startswith("> "):
            quote_text = stripped[2:]
            # 去掉 "> " 前缀
            p = doc.add_paragraph(quote_text)
            p.style = "Quote"
            # 用 Word 自带的 Quote（引用）样式
            continue

        # ---- 无序列表 ----
        # 匹配 "- " 或 "* " 或 "+ " 开头的列表项
        if re.match(r'^[-*+]\s+', stripped):
            item_text = re.sub(r'^[-*+]\s+', '', stripped)
            # 去掉列表标记，拿纯文本
            item_text = _apply_inline_styles(item_text, doc)
            # 处理行内格式（粗体/链接/代码）
            # 注意：_apply_inline_styles 返回的是段落（含样式），
            # 但列表要单独处理，所以这里先简化为纯文本 + 列表样式
            p = doc.add_paragraph(style="List Bullet")
            # List Bullet 是 Word 自带的无序列表样式
            _add_rich_text_to_paragraph(p, item_text)
            # 把带行内格式的文本加到这个列表段落里
            continue

        # ---- 有序列表 ----
        # 匹配 "1. " "2. " 等数字开头的列表项
        if re.match(r'^\d+\.\s+', stripped):
            item_text = re.sub(r'^\d+\.\s+', '', stripped)
            p = doc.add_paragraph(style="List Number")
            # List Number 是 Word 自带的有序列表样式
            _add_rich_text_to_paragraph(p, item_text)
            continue

        # ---- 普通段落 ----
        # 以上都没匹配到 → 普通段落
        p = doc.add_paragraph()
        _add_rich_text_to_paragraph(p, stripped)
        # 把带行内格式的文本加进去

    # ---------- 4. 保存文件 ----------
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 确保目录存在

    doc.save(str(path))
    # 保存为 .docx 文件

    return str(path.resolve())
    # 返回绝对路径


# ========== 行内格式辅助函数（DOCX 内部用） ==========

def _strip_inline_markdown(text: str) -> str:
    """
    去掉 Markdown 行内格式标记，返回纯文本
    用在标题里（标题不需要粗体/链接）

    处理的格式：
      **粗体** → 粗体
      `代码`  → 代码
      [文字](链接) → 文字
    """
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # **xxx** → xxx
    text = re.sub(r'`(.+?)`', r'\1', text)
    # `xxx` → xxx
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # [文字](url) → 文字
    return text


def _add_rich_text_to_paragraph(paragraph, text: str) -> None:
    """
    把带 Markdown 行内格式的文本，加到一个 Word 段落里
    支持：**粗体**、`行内代码`、[链接文字](url)

    参数:
        paragraph: docx 的 Paragraph 对象
        text: 带 Markdown 行内格式的字符串

    原理:
        用正则分段：普通文本、粗体、代码、链接 → 分别用不同 run 加进段落
        （run 是 Word 里"一段连续同格式文本"的最小单位）
    """
    # 用一个正则同时匹配三种格式
    pattern = re.compile(
        r'(\*\*.+?\*\*)'     # group 1: 粗体 **xxx**
        r'|(`.+?`)'          # group 2: 行内代码 `xxx`
        r'|(\[[^\]]+\]\([^)]+\))'  # group 3: 链接 [text](url)
    )

    last_end = 0
    # 上一段普通文本的结束位置

    for m in pattern.finditer(text):
        # 遍历所有匹配到的行内格式
        if m.start() > last_end:
            # 两个格式之间有普通文本 → 先加普通 run
            paragraph.add_run(text[last_end:m.start()])

        if m.group(1):
            # 粗体 **xxx**
            bold_text = m.group(1)[2:-2]
            # 去掉首尾的 **
            run = paragraph.add_run(bold_text)
            run.bold = True
            # 设置粗体

        elif m.group(2):
            # 行内代码 `xxx`
            code_text = m.group(2)[1:-1]
            # 去掉首尾的 `
            run = paragraph.add_run(code_text)
            run.font.name = "Consolas"
            # 等宽字体

        elif m.group(3):
            # 链接 [text](url)
            link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', m.group(3))
            if link_match:
                link_text = link_match.group(1)
                link_url = link_match.group(2)
                # python-docx 加超链接比较麻烦（要操作底层 XML）
                # 这里简化：显示为 "文字 (url)" 的形式，读者可复制
                run = paragraph.add_run(f"{link_text} ({link_url})")
                run.font.color.rgb = None
                # 不用蓝色高亮，保持简洁

        last_end = m.end()
        # 更新"上一段结束位置"

    # 最后一段普通文本
    if last_end < len(text):
        paragraph.add_run(text[last_end:])


def _apply_inline_styles(text: str, doc) -> str:
    """
    【兼容函数】保留旧接口，实际用 _add_rich_text_to_paragraph
    这个函数暂时保留，防止外部调用（其实内部现在都走 _add_rich_text_to_paragraph）
    """
    return text


# ========== 统一导出入口（按后缀自动选格式） ==========

def export_route(route_md: str, output_path: str) -> str:
    """
    统一导出函数：根据文件后缀自动选择导出格式

    参数:
        route_md:   学习路线（Markdown 字符串）
        output_path: 输出路径（后缀决定格式）
            - .md   → Markdown
            - .docx → Word 文档

    返回:
        str：导出文件的绝对路径

    用法:
        export_route(route, "output/ai.md")
        export_route(route, "output/ai.docx")

    为什么要这个统一入口：
        上层（FastAPI / 命令行）不用关心格式差异，
        只传一个路径，后缀是什么就导出什么格式
        未来加 PDF / HTML，只在这个函数里加一个分支
    """
    path = Path(output_path)
    suffix = path.suffix.lower()
    # 取后缀（转小写，避免 .MD / .Docx 大小写问题）

    if suffix == ".md":
        return export_markdown(route_md, output_path)
    elif suffix == ".docx":
        return export_docx(route_md, output_path)
    else:
        # 不认识的格式 → 报错
        raise ValueError(
            f"不支持的导出格式: {suffix}\n"
            f"支持的格式：.md, .docx"
        )


# ========== 文件结束 ==========
