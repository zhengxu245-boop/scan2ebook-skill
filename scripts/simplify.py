#!/usr/bin/env python3
"""步骤 2：把 OCR 结果用 opencc 转换字种（繁体→简体 或 简体→繁体），保留页码。

输入 output/ocr/{page:03d}.json -> 输出 output/simplified/{page:03d}.json
转换方向由 book.toml 的 book.convert 决定（t2s / t2sp / s2t / tw2sp / ""=不转）。

用法：
  python scripts/simplify.py
  python scripts/simplify.py --pages 1,4-6
  python scripts/simplify.py --config t2s   # 覆盖 book.convert
"""
import argparse
import json
import os

import common

try:
    from opencc import OpenCC
except ImportError:
    OpenCC = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="book.toml")
    ap.add_argument("--pages", default="", help="逗号分隔，留空为全部已 OCR 页")
    ap.add_argument("--convert", default=None, help="覆盖 book.convert（t2s/t2sp/s2t/tw2sp/空=不转）")
    args = ap.parse_args()

    cfg, root = common.load_config(args.config)
    convert = args.convert if args.convert is not None else cfg.get("book", {}).get("convert", "t2s")
    ocr_dir = common.resolve(root, "output/ocr")
    out_dir = common.resolve(root, "output/simplified")

    if args.pages.strip():
        files = [f"{int(x):03d}.json" for x in args.pages.split(",")]
    else:
        files = sorted(f for f in os.listdir(ocr_dir) if f.endswith(".json"))
    if not files:
        raise SystemExit("没有可转换的 OCR 页，请先跑 scripts/ocr.py")

    if convert:
        if OpenCC is None:
            raise SystemExit("需要 opencc：pip install opencc-python-reimplemented")
        cc = OpenCC(convert)
    os.makedirs(out_dir, exist_ok=True)

    for fn in files:
        src = os.path.join(ocr_dir, fn)
        d = json.load(open(src, encoding="utf-8"))
        if convert:
            d["text_simplified"] = cc.convert(d["text"])
            d["convert_config"] = convert
        else:
            d["text_simplified"] = d["text"]
            d["convert_config"] = ""
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print(f"{fn}: {len(d['text'])} -> {len(d['text_simplified'])} chars")
    print(f"完成，共 {len(files)} 页 -> {out_dir}")


if __name__ == "__main__":
    main()
