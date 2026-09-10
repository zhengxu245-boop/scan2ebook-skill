#!/usr/bin/env python3
"""步骤 1：扫描版 PDF 逐页 OCR（视觉大模型，忠实转录）。

流程：gs 渲染页面(JPEG) -> 调视觉模型识别 -> 存 output/ocr/{page:03d}.json
断点续跑：已存在且非空的页会跳过。
截断兜底：若输出接近 max_tokens（可能被截断），把页面上下切两半分别识别再拼接。

用法：
  python scripts/ocr.py                 # 跑全部页
  python scripts/ocr.py --pages 1,4-6   # 只跑指定页
  python scripts/ocr.py --dpi 300       # 覆盖配置里的 dpi
  python scripts/ocr.py --force         # 强制重跑
"""
import argparse
import json
import os
import subprocess
import sys
import time

import common


def render_page(pdf, work_dir, page, dpi):
    os.makedirs(work_dir, exist_ok=True)
    jpg = os.path.join(work_dir, f"page_{page:03d}.jpg")
    cmd = ["gs", "-dNOPAUSE", "-dBATCH", "-sDEVICE=jpeg", "-dJPEGQ=85", f"-r{dpi}",
           f"-dFirstPage={page}", f"-dLastPage={page}", f"-sOutputFile={jpg}", pdf]
    subprocess.run(cmd, check=True, capture_output=True)
    return jpg


def ocr_prompt(cfg):
    """按 ocr_script 决定目标字种。"""
    script = cfg.get("book", {}).get("ocr_script", "trad")
    blank_rule = ("若页面为空白、无文字或只有插图而无法转录，请直接输出空字符串，"
                  "不要输出任何猜测、说明或对话。")
    if script == "simp":
        return ("请忠实逐字识别这张书页图片中的全部文字，按原文从上到下的顺序转录，"
                "并转为简体中文输出。不要翻译、不要总结、不要添加任何说明。"
                "页面上的页眉、页脚和页码也一并转录。" + blank_rule)
    return ("请忠实逐字识别这张书页图片中的全部文字，按原文从上到下的顺序转录。"
            "保持繁体字原样（不要转成简体），不要翻译、不要总结、不要添加任何说明。"
            "页面上的页眉、页脚和页码也一并转录。" + blank_rule)


def ocr_page(cfg, jpg, max_tokens, trunc_threshold):
    """识别单页；若接近输出上限则上下切半分别识别拼接。返回 (text, usage)。"""
    prompt = ocr_prompt(cfg)
    text, usage = common.vision(cfg, common.image_b64(jpg), prompt, max_tokens)
    if usage.get("completion_tokens", 0) < trunc_threshold:
        return text, usage

    try:
        from PIL import Image
    except ImportError:
        print("  [warn] Pillow 未安装，跳过截断切半，本页可能不完整", file=sys.stderr, flush=True)
        return text, usage

    im = Image.open(jpg)
    w, h = im.size
    overlap = int(h * 0.10)
    mid = h // 2
    parts, usages = [], []
    for (top, bottom) in [(0, mid + overlap), (mid - overlap, h)]:
        crop = im.crop((0, top, w, bottom))
        crop_path = jpg + ".crop.jpg"
        crop.save(crop_path, "JPEG", quality=88)
        t, u = common.vision(cfg, common.image_b64(crop_path), prompt, max_tokens)
        parts.append(t)
        usages.append(u)
        os.remove(crop_path)
    merged = "\n".join(parts)
    tot = {"completion_tokens": usage.get("completion_tokens", 0) + sum(u.get("completion_tokens", 0) for u in usages),
           "prompt_tokens": usage.get("prompt_tokens", 0) + sum(u.get("prompt_tokens", 0) for u in usages)}
    tot["total_tokens"] = tot["completion_tokens"] + tot["prompt_tokens"]
    return merged, tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="book.toml")
    ap.add_argument("--pages", default="", help="逗号分隔页码范围，如 1,4-6；留空跑全部")
    ap.add_argument("--dpi", type=int, default=None, help="覆盖配置里的 dpi")
    ap.add_argument("--model", default=None, help="覆盖配置里的 OCR 模型")
    ap.add_argument("--force", action="store_true", help="强制重跑已存在的页")
    args = ap.parse_args()

    cfg, root = common.load_config(args.config)
    pdf = common.resolve(root, cfg["book"]["pdf"])
    ocr_cfg = cfg.get("ocr", {})
    dpi = args.dpi or ocr_cfg.get("dpi", 200)
    max_tokens = ocr_cfg.get("max_tokens", 1024)
    trunc_threshold = ocr_cfg.get("trunc_threshold", int(max_tokens * 0.97))
    out_dir = common.resolve(root, "output/ocr")
    work_dir = common.resolve(root, "output/work")
    model = args.model

    total = int(subprocess.run(["qpdf", "--show-npages", pdf], capture_output=True,
                               text=True, check=True).stdout.strip())
    pages = common.parse_pages(args.pages, total)
    os.makedirs(out_dir, exist_ok=True)

    done = skipped = failed = 0
    for p in pages:
        out = os.path.join(out_dir, f"{p:03d}.json")
        if not args.force and os.path.exists(out) and os.path.getsize(out) > 2:
            skipped += 1
            continue
        ok = False
        for attempt in range(4):
            try:
                jpg = render_page(pdf, work_dir, p, dpi)
                # 需要临时换模型时，直接改 cfg 的 models.ocr
                if model:
                    cfg.setdefault("models", {})["ocr"] = model
                text, usage = ocr_page(cfg, jpg, max_tokens, trunc_threshold)
                # 兜底：模型不守"不要说明"指令、在空白/插图页输出客服废话时清成空
                text, removed = common.strip_artifacts(text)
                if removed:
                    print(f"page {p}: 删除 {len(removed)} 行客服废话，按空白页处理", file=sys.stderr)
                text = text.strip()
                with open(out, "w", encoding="utf-8") as f:
                    json.dump({"page": p, "model": model or cfg["models"].get("ocr"),
                               "dpi": dpi, "text": text, "usage": usage},
                              f, ensure_ascii=False, indent=2)
                done += 1
                print(f"[{done + skipped}/{len(pages)}] page {p}: {len(text)} chars, "
                      f"{usage.get('total_tokens')} tok", flush=True)
                os.remove(jpg)
                ok = True
                break
            except Exception as e:
                print(f"page {p} attempt {attempt + 1} failed: {e}", file=sys.stderr, flush=True)
                time.sleep(2 * (attempt + 1))
        if not ok:
            failed += 1
        time.sleep(0.3)
    print(f"\n完成：成功 {done}，跳过 {skipped}，失败 {failed}，共 {len(pages)} 页")


if __name__ == "__main__":
    main()
