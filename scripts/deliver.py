#!/usr/bin/env python3
"""步骤 4：用 pandoc 把最终 Markdown 交付为 EPUB（可选 DOCX/TXT）。

用法：
  python scripts/deliver.py
  python scripts/deliver.py --docx   # 同时输出 DOCX
  python scripts/deliver.py --txt    # 同时输出纯文本
"""
import argparse
import os
import subprocess

import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="book.toml")
    ap.add_argument("--docx", action="store_true")
    ap.add_argument("--txt", action="store_true")
    args = ap.parse_args()

    cfg, root = common.load_config(args.config)
    b = cfg["book"]
    md = common.resolve(root, "output/" + b.get("output", b["title"] + ".md"))
    if not os.path.exists(md):
        raise SystemExit(f"未找到 {md}，请先跑 scripts/proofread.py")

    out_dir = common.resolve(root, "output")
    base = os.path.join(out_dir, b["title"])

    epub = base + ".epub"
    cmd = ["pandoc", md, "-o", epub,
           "--metadata", f"title={b['title']}",
           "--metadata", f"lang={b.get('lang', 'zh-CN')}",
           "--toc", "--toc-depth=2"]
    subprocess.run(cmd, check=True)
    print(f"EPUB -> {epub}")

    if args.docx:
        docx = base + ".docx"
        subprocess.run(["pandoc", md, "-o", docx], check=True)
        print(f"DOCX -> {docx}")
    if args.txt:
        txt = base + ".txt"
        subprocess.run(["pandoc", md, "-o", txt, "-t", "plain"], check=True)
        print(f"TXT -> {txt}")


if __name__ == "__main__":
    main()
