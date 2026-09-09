---
name: scan2ebook
description: "把扫描版/图片版中文书籍 PDF（无文字层）端到端转成结构化简体 Markdown 与 EPUB：视觉大模型 OCR → opencc 繁转简 → LLM 校对分章 → pandoc 出书。当用户要 OCR 一本书、繁体转简体、把旧扫描书整理成电子书时使用，含断点续跑与模型选型踩坑清单。"
whenToUse: "当用户要把扫描版中文书籍 PDF 转成可检索的简体电子书（Markdown/EPUB/DOCX），或要求 OCR 一本书、繁体转简体、把旧书整理成结构化电子书时使用。"
metadata:
  pipeline: [ocr, simplify, detect, proofread, deliver]
  api: "paratera (OpenAI 兼容)"
  models: ["GLM-4V-Flash", "GLM-4.5-Air"]
---

# scan2ebook：扫描版中文书 → 结构化简体电子书

把一本**没有文字层的扫描版中文书 PDF** 端到端转成**结构化简体 Markdown + EPUB**。
忠实转录优先：OCR 不翻译不总结，校对不润色不改写，只修明显错字、合并断行、去页眉页脚。

## 前置依赖（一次性）

系统工具（缺哪个装哪个）：

- `gs`（Ghostscript，渲染 PDF 页面）
- `qpdf`（读 PDF 页数）
- `pandoc`（Markdown → EPUB/DOCX）

Python 依赖（装进项目 `.venv`）：

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

API key：环境变量 `PARATERA_API_KEY`，或 DSH harness 的 `~/.credentials.yaml`，或 `book.toml` 里 `models.api_key` 直填。

## 五步流程

在项目根（放 `book.toml` 和 `books/*.pdf` 的地方）依次执行：

1. **OCR**：`python scripts/ocr.py` — gs 渲染每页 → 视觉模型逐字转录 → `output/ocr/{page:03d}.json`。断点续跑，已存在的页自动跳过。
2. **转字种**：`python scripts/simplify.py` — opencc 繁→简（或反向）→ `output/simplified/*.json`。
3. **探测章节**：写 `toc.txt`（每行一个标题，`# ` 前缀=部级，其余=章级），跑 `python scripts/detect.py --toc toc.txt` → 打印各章候选起始页 + 生成 `output/units.detected.toml`。**必须人工核对起始页**（见下方坑 #1），核对后把 `[[units]]` 粘贴进 `book.toml`。
4. **校对分章**：`python scripts/proofread.py` — 标题由代码按 `[[units]]` 确定性插入，LLM 只清洗正文 → `output/<title>.md`。缓存到 `output/units/`，失败重跑本脚本即可续跑。
5. **交付**：`python scripts/deliver.py`（加 `--docx`/`--txt` 可同时出 DOCX/纯文本）→ `output/<title>.epub`。

## 配置（book.toml）

参照 `examples/book.toml`。核心字段：

- `[book]`：`pdf`（相对路径）、`title`/`author`/`translator`、`lang`（EPUB 语言）、`convert`（opencc：`t2s`/`s2t`/`""`）、`ocr_script`（`trad`=繁体原文 / `simp`=简体）。
- `[models]`：`base_url`、`api_key`、`ocr`、`proofread`。
- `[ocr]`：`dpi`（默认 200，别用 300 太慢）、`max_tokens`。
- `[proofread]`：`max_pages`（每子块最多页数，默认 8）、`max_tokens`。
- `[[units]]`：`start`/`end`（PDF 页码 1-based）、`level`（1=部 `#`，2=章 `##`）、`title`。

## 模型选型与踩坑（务必读）

实测于 paratera（OpenAI 兼容 `https://llmapi.paratera.com/v1/chat/completions`）：

| 模型 | 用途 | 结论 |
|---|---|---|
| `GLM-4V-Flash` | OCR | ✅ 可用；**`max_tokens ≤ 1024`**，超了报 400 |
| `GLM-4.5-Air` | 校对 | ✅ 可用、快、忠实，但偶漏改个别错字（可接受） |
| `PaddleOCR-VL-0.9B/1.5` | OCR | ❌ 坏的（multipart/字符串都报错） |
| `GLM-5.3-Flash` | 校对 | ❌ 强推理，小输入也烧几千 token 且会幻觉，禁用 |
| `GLM-4V`/`4.5V`/`4.6V` | OCR | 可用，上限更大但更慢，`Flash` 够用 |

**坑 #1（最耗时）：页眉被 OCR 误读成章节标题。** 扫描书每页顶部页眉常是「页码 书名」，OCR 可能把它误读成「第X章 某某」；如果让 LLM 自己判断章节边界，它就会信以为真，插出一堆重复/错位标题。**对策：标题永远由代码按 `[[units]]` 的页码确定性插入，LLM 只清洗正文、禁止输出标题**（proofread.py 已内置）。

**坑 #2：大请求超时/断连。** 单次送太多页（约 >5k 字）极易 `SSL UNEXPECTED_EOF` / 超时 / 400。对策：`max_pages` 控制在 8 以内，逐子块请求 + 断点缓存续跑。

**坑 #3：接口限流。** 连续大量请求会触发限流。对策：子块之间 `sleep 8s`，失败重试带退避（已内置）。

**坑 #4：OCR 输出截断。** 视觉模型 `max_tokens` 到顶会截断长页。对策：completion 接近上限时把页面上下切两半（10% 重叠）分别识别再拼接（ocr.py 已内置，需 Pillow）。

## 质量原则

- **忠实 > 完整纠错**：OCR 和校对都不许改写、增删、翻译、总结。
- 章节标题以用户提供的目录（`toc.txt` / `[[units]]`）为权威，不臆造。
- 校对只做：合并断行（祈祷文/诗歌分行保留）、修明显 OCR 错字、还原被误识为「·」的标点、删页眉页脚页码。
- 不确定的错字宁可保留原文，不要猜改。
