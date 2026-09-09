#!/usr/bin/env python3
"""步骤 2.5：从目录标题列表探测各章节的起始页，生成 book.toml 的 [[units]] 块。

用途：校对分章需要「每个单元（章/部/前言/附录）的起始页」。这一步用 OCR 文本
自动定位每个标题出现在哪一页的页首，并打印候选页上下文供人工核对——因为页眉
常被 OCR 误读成「第X章 标题」，必须人工确认（详见 SKILL.md 避坑清单）。

匹配规则（稳健，针对扫描书噪声）：
  1. 去掉页首运行页眉（以页码数字开头，或含书名的那一行）；
  2. 标题按「页首前缀」匹配：规范化后的标题必须是该页页首（去空白与点线后）的前缀——
     这样目录页里罗列在中间的标题、被误读成「第十二章」的页眉都不会误命中；
  3. 整页正文里同时命中 >=3 个标题的页视为「目录/索引页」，整体排除。

输入：
  toc.txt —— 每行一个标题，前缀决定级别：
      "# " 开头  -> 级别 1（部标题，输出 # 第X部）
      其它（含"## "）-> 级别 2（章/节，输出 ## 第X章）
      空行或 "-" 开头的行跳过

输出：
  1. 每个标题的候选起始页 + 该页前 60 字上下文（用于核对）
  2. output/units.detected.toml —— 可直接粘贴进 book.toml 的 [[units]] 块

用法：
  python scripts/detect.py --toc toc.txt
"""
import argparse
import json
import os
import re

import common


def norm(s):
    return re.sub(r"\s+", "", s)


def flatten(s):
    """去空白与目录点线（……），得到紧凑文本。"""
    return re.sub(r"[\s.…·・．•\u2026]+", "", s)


def page_head(text, n=60, book_title=""):
    """去掉首行运行页眉后的页首片段（已去空白与点线）。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        first = norm(lines[0])
        if re.match(r"^\d{2,4}", first) or (book_title and norm(book_title) in first):
            lines = lines[1:]
    return flatten("".join(lines))[:n]


def load_pages(root):
    d = {}
    sim_dir = common.resolve(root, "output/simplified")
    if not os.path.isdir(sim_dir):
        sim_dir = common.resolve(root, "output/ocr")
    for fn in sorted(os.listdir(sim_dir)):
        if fn.endswith(".json"):
            j = json.load(open(os.path.join(sim_dir, fn), encoding="utf-8"))
            d[j["page"]] = j.get("text_simplified", j["text"])
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="book.toml")
    ap.add_argument("--toc", required=True, help="目录标题列表文件（每行一个标题）")
    ap.add_argument("--context", type=int, default=60, help="候选页打印的上下文长度")
    args = ap.parse_args()

    cfg, root = common.load_config(args.config)
    pages = load_pages(root)
    max_page = max(pages)
    book_title = cfg.get("book", {}).get("title", "")

    toc = []
    for line in open(args.toc, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("-"):
            continue
        level = 1 if line.lstrip().startswith("# ") else 2
        title = line.lstrip().lstrip("#").strip()
        if title:
            toc.append((level, title))

    # 目录/索引页：整页（去点线后）同时命中 >=3 个标题
    title_set = {norm(t) for _, t in toc}
    def toc_like(text):
        flat = flatten(text)
        return sum(1 for t in title_set if t and t in flat) >= 3

    units = []
    print(f"共 {len(toc)} 个标题，页范围 1-{max_page}（已排除目录页）\n")
    for i, (level, title) in enumerate(toc):
        t = norm(title)
        hits = []
        for p in sorted(pages):
            if toc_like(pages[p]):
                continue
            if page_head(pages[p], args.context, book_title).startswith(t):
                hits.append(p)
        first = hits[0] if hits else None
        units.append((level, title, first))
        flag = "" if first else "  ⚠️ 未找到（标题可能被 OCR 误读，请人工定位）"
        if len(hits) > 1:
            flag += f"（多候选 {hits}，需核对）"
        print(f"[{i+1}] {title}  起始页 {first}{flag}")
        for p in hits[:4]:
            print(f"      p{p}: {pages[p][:60].replace(chr(10), ' | ')}")

    # 生成 units 块
    lines_out = []
    for i, (level, title, start) in enumerate(units):
        end = (units[i + 1][2] - 1) if (i + 1 < len(units) and units[i + 1][2]) else max_page
        lines_out.append("[[units]]")
        lines_out.append(f"start = {start or 1}")
        lines_out.append(f"end = {end}")
        lines_out.append(f"level = {level}")
        lines_out.append(f'title = "{title}"')
        lines_out.append("")
    out_path = common.resolve(root, "output/units.detected.toml")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out))
    print(f"\n已生成 {out_path}（请核对起始页后粘贴进 book.toml）")


if __name__ == "__main__":
    main()
