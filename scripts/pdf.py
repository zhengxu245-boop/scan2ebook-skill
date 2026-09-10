#!/usr/bin/env python3
"""步骤 7：把成品 Markdown 交付为"按章节分好"的 PDF。

实现：纯 Python（reportlab），不依赖浏览器/LaTeX —— 本机 Chrome headless 会挂起，
故不用 HTML 打印路线。中文用系统字体（宋体/冬青黑），支持：

产物（默认 output/pdf/ 下）：
  output/pdf/<title>.pdf                  单文件：封面 + 目录（带页码）+ 全文 + 「部/章」PDF 书签
  output/pdf/<title>/split/NN_<标题>.pdf   每章一个独立 PDF（可直接分章入库）

用法：
  python scripts/pdf.py                                  # 读 book.toml
  python scripts/pdf.py --config book-在祈祷中相遇.toml
  python scripts/pdf.py --md output/xxx.md --title 书名
  python scripts/pdf.py --level 1                        # 只按「部」切分（默认 1+2）
"""
import argparse
import os
import re
import shutil
import sys

import common

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak, PageTemplate,
                                Paragraph, Spacer)
from reportlab.platypus.tableofcontents import TableOfContents

BODY_CANDIDATES = ["/System/Library/Fonts/Supplemental/Songti.ttc",
                   "/System/Library/Fonts/Hiragino Sans GB.ttc",
                   "/Library/Fonts/Arial Unicode.ttf"]
HEAD_CANDIDATES = ["/System/Library/Fonts/Hiragino Sans GB.ttc",
                   "/System/Library/Fonts/STHeiti Light.ttc",
                   "/System/Library/Fonts/Supplemental/Songti.ttc",
                   "/Library/Fonts/Arial Unicode.ttf"]

HEAD_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
PAGE_W, PAGE_H = A4


def register_fonts():
    def first(cands, name):
        for p in cands:
            if os.path.exists(p):
                try:
                    pdfmetrics.registerFont(TTFont(name, p, subfontIndex=0))
                    return p
                except Exception:
                    continue
        raise SystemExit("未找到可用的中文字体（尝试过: %s）" % ", ".join(cands))
    body = first(BODY_CANDIDATES, "CJK")
    head = first(HEAD_CANDIDATES, "CJKHead")
    pdfmetrics.registerFontFamily("CJK", normal="CJK", bold="CJKHead",
                                  italic="CJK", boldItalic="CJKHead")
    return body, head


def styles():
    return {
        "title": ParagraphStyle("title", fontName="CJKHead", fontSize=22, leading=32,
                                alignment=TA_CENTER, spaceAfter=18),
        "h1": ParagraphStyle("h1", fontName="CJKHead", fontSize=18, leading=26,
                             alignment=TA_CENTER, spaceBefore=6, spaceAfter=16),
        "h2": ParagraphStyle("h2", fontName="CJKHead", fontSize=15, leading=22,
                             spaceBefore=4, spaceAfter=12),
        "h3": ParagraphStyle("h3", fontName="CJKHead", fontSize=12.5, leading=19,
                             spaceBefore=4, spaceAfter=8),
        "body": ParagraphStyle("body", fontName="CJK", fontSize=10.5, leading=18,
                               alignment=TA_JUSTIFY, firstLineIndent=21, spaceAfter=6),
        "quote": ParagraphStyle("quote", fontName="CJK", fontSize=10.5, leading=17,
                                leftIndent=18, rightIndent=10, textColor="#444444",
                                spaceAfter=8),
        "toc1": ParagraphStyle("toc1", fontName="CJKHead", fontSize=11.5, leading=20),
        "toc2": ParagraphStyle("toc2", fontName="CJK", fontSize=10.5, leading=18,
                               leftIndent=16),
        "tocentry": ParagraphStyle("tocentry", fontName="CJKHead", fontSize=17,
                                   leading=26, alignment=TA_CENTER, spaceAfter=14),
    }


def esc(s):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    s = s.replace("`", "")
    return s


def md_blocks(md_text):
    """Markdown -> [('h', level, title) | ('p', text) | ('q', text) | ('hr',)]"""
    blocks, buf, quote = [], [], []

    def flush():
        if buf:
            blocks.append(("p", " ".join(x.strip() for x in buf).strip()))
            buf.clear()

    def flush_quote():
        if quote:
            blocks.append(("q", " ".join(x.strip() for x in quote).strip()))
            quote.clear()

    for raw in md_text.split("\n"):
        line = raw.rstrip()
        m = HEAD_RE.match(line)
        if m:
            flush(); flush_quote()
            blocks.append(("h", len(m.group(1)), m.group(2).strip()))
            continue
        if not line.strip():
            flush(); flush_quote()
            continue
        if line.lstrip().startswith(">"):
            flush()
            quote.append(line.lstrip()[1:].strip())
            continue
        if re.match(r"^\s*([-*_]\s*){3,}$", line):
            flush(); flush_quote()
            blocks.append(("hr",))
            continue
        flush_quote()
        buf.append(line)
    flush(); flush_quote()
    return blocks


def split_sections(blocks, max_level):
    """按 1..max_level 级标题切分；返回 [{'level','title','blocks'}]，前置内容单列一节。"""
    sections, cur = [], None
    for b in blocks:
        if b[0] == "h" and b[1] <= max_level:
            cur = {"level": b[1], "title": b[2], "blocks": [b]}
            sections.append(cur)
        else:
            if cur is None:
                cur = {"level": 0, "title": "正文", "blocks": []}
                sections.append(cur)
            cur["blocks"].append(b)
    return [s for s in sections if s["blocks"]]


class BookDoc(BaseDocTemplate):
    """记录章节所在页 -> 写 PDF 书签 + 通知 TOC 填页码。"""

    def __init__(self, path, title, **kw):
        super().__init__(path, pagesize=A4, title=title,
                         leftMargin=18 * mm, rightMargin=16 * mm,
                         topMargin=18 * mm, bottomMargin=20 * mm, **kw)
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="F")
        self.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=self._deco)])
        self.first_page = 1
        self._outline_level = -1

    def _deco(self, canv, doc):
        if doc.page <= self.first_page:
            return
        canv.saveState()
        canv.setFont("CJK", 9)
        canv.setFillColor("#666666")
        canv.drawCentredString(PAGE_W / 2, 12 * mm, str(doc.page))
        canv.restoreState()

    def afterFlowable(self, flowable):
        key = getattr(flowable, "_toc_key", None)
        if not key:
            return
        desired = getattr(flowable, "_toc_level", 1)
        # PDF 书签不允许跳级（如无「部」直接出现「章」）：逐级收敛
        lvl = min(desired, self._outline_level + 1)
        self._outline_level = lvl
        title = getattr(flowable, "_toc_title", "")
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(title, key, level=lvl, closed=False)
        self.notify("TOCEntry", (lvl, title, self.page, key))


def heading_para(level, title, st, toc_level=None, seq=0):
    style = st["h1"] if level <= 1 else (st["h2"] if level == 2 else st["h3"])
    p = Paragraph(esc(title), style)
    if toc_level is not None:
        p._toc_key = f"sec{seq}"
        p._toc_level = toc_level
        p._toc_title = title
    return p


def section_flowables(sec, st, seq):
    out = []
    for b in sec["blocks"]:
        if b[0] == "h":
            lvl = b[1]
            out.append(heading_para(lvl, b[2], st,
                                    toc_level=(0 if lvl <= 1 else 1), seq=seq))
        elif b[0] == "p":
            out.append(Paragraph(esc(b[1]), st["body"]))
        elif b[0] == "q":
            out.append(Paragraph(esc(b[1]), st["quote"]))
        else:
            out.append(Spacer(1, 6))
    return out


def safe_name(s, limit=40):
    s = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", s).strip()
    return s[:limit] or "section"


def build_combined(md_path, title, sections, st, out_pdf):
    story = [Paragraph(esc(title), st["title"]), Spacer(1, 6), PageBreak()]
    story.append(Paragraph("目录", st["tocentry"]))
    toc = TableOfContents()
    toc.levelStyles = [st["toc1"], st["toc2"]]
    story += [toc, PageBreak()]
    for i, sec in enumerate(sections, 1):
        if i > 1:
            story.append(PageBreak())
        story += section_flowables(sec, st, i)
    doc = BookDoc(out_pdf, title)
    doc.first_page = 1
    doc.multiBuild(story)
    return out_pdf


def build_split(title, sections, st, split_dir, first_page_no=True):
    os.makedirs(split_dir, exist_ok=True)
    made = []
    for i, sec in enumerate(sections, 1):
        path = os.path.join(split_dir, f"{i:02d}_{safe_name(sec['title'])}.pdf")
        story = section_flowables(sec, st, i)
        doc = BookDoc(path, f"{title} · {sec['title']}")
        doc.first_page = 1  # 分章文件不显示页码，避免与合订本页码混淆
        doc.build(story)
        made.append(path)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="book.toml")
    ap.add_argument("--md", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default="output/pdf")
    ap.add_argument("--level", type=int, default=2, help="切分深度：1=只切部，2=部+章")
    ap.add_argument("--no-split", action="store_true", help="只出合订本")
    args = ap.parse_args()

    cfg, root = common.load_config(args.config)
    if args.md:
        md = common.resolve(os.getcwd(), args.md)
        title = args.title or os.path.splitext(os.path.basename(md))[0]
    else:
        b = cfg["book"]
        md = common.resolve(root, "output/" + b.get("output", b["title"] + ".md"))
        title = args.title or b["title"]
    if not os.path.exists(md):
        raise SystemExit(f"未找到 {md}")

    text = open(md, encoding="utf-8").read()
    cleaned, r1 = common.strip_artifacts(text)
    cleaned, r2 = common.strip_md_residues(cleaned)
    if r1 + r2:
        raise SystemExit(f"{md} 含 {len(r1 + r2)} 行杂质，请先 python scripts/clean.py --fix 再出 PDF")

    register_fonts()
    st = styles()
    blocks = md_blocks(cleaned)
    sections = split_sections(blocks, args.level)
    if not sections:
        raise SystemExit("没有切出任何章节，请检查 Markdown 标题层级")

    out_root = common.resolve(root, args.out)
    os.makedirs(out_root, exist_ok=True)
    combined = os.path.join(out_root, safe_name(title, 60) + ".pdf")
    print(f"=== {title}：{len(sections)} 个章节 -> PDF ===", flush=True)
    for s in sections:
        print(f"  {'#'*s['level'] if s['level'] else '(front)'} {s['title']}", flush=True)
    build_combined(md, title, sections, st, combined)
    print(f"\n合订本（封面+目录+书签）: {combined}", flush=True)
    if not args.no_split:
        split_dir = os.path.join(out_root, safe_name(title, 60), "split")
        made = build_split(title, sections, st, split_dir)
        print(f"分章文件: {split_dir}（{len(made)} 个）", flush=True)

    # 自检：页数 + 书签
    try:
        from pypdf import PdfReader
        r = PdfReader(combined)
        print(f"\n[自检] 合订本 {len(r.pages)} 页，书签 {len(r.outline)} 条：")
        for it in r.outline[:12]:
            if isinstance(it, list):
                continue
            try:
                pg = r.get_destination_page_number(it) + 1
            except Exception:
                pg = "?"
            print(f"   - {it.title} (p{pg})")
    except Exception as e:
        print(f"[自检] 跳过（{e}）")


if __name__ == "__main__":
    main()
