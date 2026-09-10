#!/usr/bin/env python3
"""步骤 3：按章校对分章，生成最终结构化 Markdown。

关键设计：标题（部/章）由代码根据 book.toml 的 [[units]] 确定性插入，
LLM 只负责正文清洗（合并段落、修 OCR 错字、去页眉页脚），绝不生成标题——
这样可彻底避免「页眉被 OCR 误读成章节名」导致标题错乱的坑（见 SKILL.md）。

自动切分：每个单元按 proofread.max_pages 拆成小请求（大请求易超时），
结果缓存到 output/units/{NN}.{MM}.md，可断点续跑。

用法：
  python scripts/proofread.py
  python scripts/proofread.py --model GLM-4.5-Air   # 覆盖校对模型
"""
import argparse
import json
import os
import sys
import time

import common


def load_pages(root):
    pages = {}
    sim_dir = common.resolve(root, "output/simplified")
    if not os.path.isdir(sim_dir):
        raise SystemExit("未找到 output/simplified，请先跑 scripts/simplify.py")
    for fn in sorted(os.listdir(sim_dir)):
        if fn.endswith(".json"):
            d = json.load(open(os.path.join(sim_dir, fn), encoding="utf-8"))
            pages[d["page"]] = d.get("text_simplified", d["text"])
    return pages


def system_prompt(cfg):
    b = cfg.get("book", {})
    title = b.get("title", "本书")
    return (
        "你是资深中文图书校对编辑。下面是《" + title + "》某一节正文的 OCR 识别文字。"
        "文字含 OCR 错字、多余断行、页眉页脚和页码噪声。\n\n"
        "请完成：\n"
        "1. 把断行合并成自然段落；但祈祷文、圣经引文、诗歌的分行要保留换行；\n"
        "2. 修正明显的 OCR 错字，保持原意，不增删内容、不润色、不改写；被误识为「·」的标点按语境还原为「，」「。」「；」等（人名间隔号保留）；\n"
        "3. 删除页眉、页脚、页码等噪声（如每页顶部重复的书名、底部的数字页码）；\n"
        "4. 不要输出任何标题（章节标题我会自己加）；若开头就是「第X章/第X部」之类的标题行，请忽略它，只输出正文段落。\n"
        "5. 删除输入里 `[第N页]` 之类的占位标记，以及任何与书无关的机器说明/对话残句"
        "（例如「我猜您想让我识别图中正文…」「请您重新上传图片…」这种客服口吻的整句）。\n\n"
        "只输出干净的正文，不要任何解释或标题。"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="book.toml")
    ap.add_argument("--model", default=None, help="覆盖配置里的校对模型")
    ap.add_argument("--max-pages", type=int, default=None, help="覆盖配置里的 max_pages")
    args = ap.parse_args()

    cfg, root = common.load_config(args.config)
    if args.model:
        cfg.setdefault("models", {})["proofread"] = args.model
    b = cfg["book"]
    pr = cfg.get("proofread", {})
    max_pages = args.max_pages or pr.get("max_pages", 8)
    max_tokens = pr.get("max_tokens", 16384)
    units = cfg.get("units", [])
    if not units:
        raise SystemExit("book.toml 里没有 [[units]]；请先跑 scripts/detect.py 生成并填入")

    pages = load_pages(root)
    out_md = common.resolve(root, "output/" + b.get("output", b["title"] + ".md"))
    unit_dir = common.resolve(root, "output/units")
    os.makedirs(unit_dir, exist_ok=True)
    sys_prompt = system_prompt(cfg)

    # 逐个单元 -> 按 max_pages 拆子块 -> LLM 清洗（缓存续跑）
    for idx, u in enumerate(units, 1):
        s, e, level, heading = u["start"], u["end"], u.get("level", 2), u["title"]
        sub_ranges = [(a, min(a + max_pages - 1, e)) for a in range(s, e + 1, max_pages)]
        for si, (a, z) in enumerate(sub_ranges):  # 注意：不能用 b，会遮蔽外层 b = cfg["book"]
            pfile = os.path.join(unit_dir, f"{idx:02d}.{si:02d}.md")
            if os.path.exists(pfile) and os.path.getsize(pfile) >= 10:
                continue
            body = "\n\n".join(f"[第{p}页]\n{pages[p]}" for p in range(a, z + 1) if p in pages)
            print(f"=== [{idx}/{len(units)}] {heading} 页 {a}-{z}（子块 {si+1}/{len(sub_ranges)}，输入 {len(body)} 字）===", flush=True)
            ok = False
            for attempt in range(4):
                try:
                    md, usage = common.chat(cfg, [{"role": "system", "content": sys_prompt},
                                                  {"role": "user", "content": body}], max_tokens)
                    # 确定性兜底：清掉占位符复读/客服废话，不依赖 LLM 自觉
                    md, removed = common.strip_artifacts(md)
                    if removed:
                        print(f"  [clean] 删除杂质 {len(removed)} 行: "
                              + "; ".join(r[:20] for r in removed), file=sys.stderr)
                    md = md.strip()
                    # 缓存文件必须 >=10 字节，否则下次重跑会被当作缺失而重新调 LLM
                    if len(md.encode("utf-8")) < 10:
                        md = "<!-- （无正文） -->"
                    with open(pfile, "w", encoding="utf-8") as f:
                        f.write(md + "\n")
                    print(f"  -> 输出 {len(md)} 字, usage={usage.get('total_tokens')}", flush=True)
                    ok = True
                    break
                except Exception as ex:
                    print(f"  attempt {attempt+1} 失败: {ex}", file=sys.stderr, flush=True)
                    time.sleep(5 * (attempt + 1))
            if not ok:
                print(f"  [{heading} {a}-{b}] 全部失败（重跑本脚本可续跑）", file=sys.stderr, flush=True)
            time.sleep(8)

    # 合并（标题由代码插入）
    head = [f"# {b['title']}"]
    if b.get("subtitle"):
        head.append(f"> {b['subtitle']}")
    if b.get("author") or b.get("translator"):
        head.append(">")
    if b.get("author"):
        head.append(f"> {b['author']}")
    if b.get("translator"):
        head.append(f"> {b['translator']}")
    parts = ["\n".join(head) + "\n"]

    for idx, u in enumerate(units, 1):
        s, e, level, heading = u["start"], u["end"], u.get("level", 2), u["title"]
        sub_ranges = [(a, min(a + max_pages - 1, e)) for a in range(s, e + 1, max_pages)]
        chunks, cached = [], False
        for si in range(len(sub_ranges)):
            pfile = os.path.join(unit_dir, f"{idx:02d}.{si:02d}.md")
            if not os.path.exists(pfile):
                continue
            cached = True
            raw = open(pfile, encoding="utf-8").read()
            body, _ = common.strip_artifacts(raw)
            body = body.strip()
            # 空 / 只剩注释的块（如已清空的部扉页单元）不产生任何输出，但标题仍由代码输出
            if body and not common.is_comment_only(body):
                chunks.append(body)
        if not cached:
            parts.append(f"<!-- 缺 {heading} -->\n")
            continue
        h = "#" * max(1, min(6, level))
        parts.append(f"{h} {heading}\n\n" + "\n\n".join(chunks) + "\n")
    final = "\n\n".join(parts).strip() + "\n"
    # 成品级兜底：删页眉/标题残体与紧跟标题的重复行（不依赖 LLM 自觉）
    final, removed_res = common.strip_md_residues(final)
    if removed_res:
        print(f"[clean] 合并后删除标题残体/重复行 {len(removed_res)} 行: "
              + "; ".join(r.strip()[:24] for r in removed_res), file=sys.stderr)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(final)
    print(f"\n完成 -> {out_md}（{len(final)} 字）")


if __name__ == "__main__":
    main()
