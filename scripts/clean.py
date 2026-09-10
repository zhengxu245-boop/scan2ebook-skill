#!/usr/bin/env python3
"""流水线杂质清洗 / 出口检查（badcase 2026-09-10 后新增）。

三类规则（见 common.py）：
  A) `[第N页]` 分页占位符复读 + OCR 客服废话          —— strip_artifacts（整行）
  B) 页眉/标题残体「第X章 某某」独立成行              —— strip_md_residues
  C) 紧跟代码标题之后重复同一标题的行                  —— strip_md_residues（同上）

用法：
  python scripts/clean.py output/<title>.md [更多文件...]      # 就地清洗，打印被删行
  python scripts/clean.py --check output/<title>.md            # 只检查；有杂质则 exit 1
  python scripts/clean.py --suspect output/<title>.md          # 只报告疑似短行，供人工复核

出口闸：入库/交付前跑 --check（deliver.py 已内置同款检查）。
"""
import argparse
import os
import sys

import common


def process(path, mode="fix"):
    if not os.path.exists(path):
        print(f"[skip] 不存在: {path}", file=sys.stderr)
        return 0
    with open(path, encoding="utf-8") as f:
        text = f.read()
    removed = []
    cleaned, r1 = common.strip_artifacts(text)
    cleaned2, r2 = common.strip_md_residues(cleaned)
    removed += r1
    removed += r2
    cleaned = cleaned2

    if mode == "suspect":
        sus = common.suspect_lines(cleaned)
        print(f"[suspect] {path}: 疑似页眉/标题式短行 {len(sus)} 处（只报告，不修改）")
        for ln, s in sus[:40]:
            print(f"    L{ln}: {s}")
        if len(sus) > 40:
            print(f"    … 其余 {len(sus) - 40} 处省略")
        return 0

    if not removed:
        print(f"[clean] {path}: 无杂质")
        return 0

    tag = "FAIL" if mode == "check" else "fix"
    print(f"[{tag}] {path}: 发现 {len(removed)} 行杂质：")
    for i, ln in enumerate(removed, 1):
        print(f"    {i}. {ln[:70]}{'…' if len(ln) > 70 else ''}")
    if mode == "check":
        return len(removed)
    with open(path, "w", encoding="utf-8") as f:
        f.write(cleaned)
    print("  -> 已就地清洗，剩余杂质应为 0（可再跑 --check 复核）")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", help="要清洗/检查的 md 文件")
    ap.add_argument("--check", action="store_true", help="只检查不修改；发现杂质 exit 1")
    ap.add_argument("--suspect", action="store_true", help="只报告疑似短行，供人工复核")
    args = ap.parse_args()
    mode = "suspect" if args.suspect else ("check" if args.check else "fix")
    bad = 0
    for f in args.files:
        bad += process(f, mode)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
