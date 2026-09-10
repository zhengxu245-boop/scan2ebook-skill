---
name: scan2ebook
description: "把扫描版/图片版中文书籍 PDF（无文字层）端到端转成结构化简体 Markdown 与 EPUB：视觉大模型 OCR → opencc 繁转简 → LLM 校对分章 → pandoc 出书。当用户要 OCR 一本书、繁体转简体、把旧扫描书整理成电子书时使用，含断点续跑、确定性杂质清洗与模型选型踩坑清单。"
whenToUse: "当用户要把扫描版中文书籍 PDF 转成可检索的简体电子书（Markdown/EPUB/DOCX），或要求 OCR 一本书、繁体转简体、把旧书整理成结构化电子书时使用。"
metadata:
  pipeline: [ocr, simplify, detect, proofread, deliver, clean]
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

## 六步流程

在项目根（放 `book.toml` 和 `books/*.pdf` 的地方）依次执行：

1. **OCR**：`python scripts/ocr.py` — gs 渲染每页 → 视觉模型逐字转录 → `output/ocr/{page:03d}.json`。断点续跑，已存在的页自动跳过。**空页规则已内置**：空白/纯插图页只输出空串；万一模型仍输出"客服废话"，保存前会被确定性规则清成空页。
2. **转字种**：`python scripts/simplify.py` — opencc 繁→简（或反向）→ `output/simplified/*.json`。
3. **探测章节**：写 `toc.txt`（每行一个标题，`# ` 前缀=部级，其余=章级），跑 `python scripts/detect.py --toc toc.txt` → 打印各章候选起始页 + 生成 `output/units.detected.toml`。**必须人工核对起始页**（见下方坑 #1），核对后把 `[[units]]` 粘贴进 `book.toml`。
4. **校对分章**：`python scripts/proofread.py` — 标题由代码按 `[[units]]` 确定性插入，LLM 只清洗正文 → `output/<title>.md`。缓存到 `output/units/`，失败重跑本脚本即可续跑。**清洗双层兜底**：LLM 输出写入缓存前与最终合并时都过 `common.strip_artifacts` 确定性规则（删 `[第N页]` 占位复读、OCR 客服废话）；合并写盘前再过成品级 `common.strip_md_residues`（删页眉/标题残体与紧跟标题的重复行）；部/章标题恒由代码输出，正文为空的部扉页单元只丢正文不丢标题。
5. **交付**：`python scripts/deliver.py`（加 `--docx`/`--txt` 可同时出 DOCX/纯文本）→ `output/<title>.epub`。**内置出口闸**：交付前扫描成品，发现流水线杂质直接报错拒绝生成（先清洗再交付）。
6. **入库前检查**（可选但推荐）：`python scripts/clean.py --check output/<title>.md` — 出口扫描四类问题（`[第N页]` 占位、OCR 客服废话、页眉/标题残体、紧跟标题的重复行），发现即 exit 1；`python scripts/clean.py <file>` 就地修复（只删整行可证伪杂质，绝不碰正文）；`python scripts/clean.py --suspect <file>` 只报告疑似短行，供人工复核。

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

**坑 #5（2026-09-10 badcase：workbuddy 入库反馈成品含杂质）：空白页让 OCR 输出"客服废话"、校对复读 `[第N页]` 占位符，都会漏进成品。** 根因：①视觉模型对空白/纯插图页不守"不要任何说明"，输出"我猜您想让我识别图中正文…／请您重新上传完整的图片…"式整句（334 页书实测 9 页中招）；②proofread 输入用 `[第{p}页]` 分页占位，LLM 在几乎无正文的部扉页单元会把占位符原样复读；③清洗全靠 LLM 自觉、无出口校验。对策（已内置，别再手工兜）：ocr prompt 显式声明空页只输出空串、保存前 strip 废话成空页；proofread prompt 禁占位符/机器口吻，且缓存写入前与合并时用 `common.strip_artifacts` **确定性规则兜底**（整行 `[第N页]` + 客服话术强特征子串，只删整行可证伪杂质、上限 120 字符，正文不受影响）；合并时标题恒由代码输出（区分"有缓存但正文空"与"缓存缺失"）；deliver.py 与 `clean.py --check` 出口闸，杂质不给出书。空正文的部扉页单元缓存用 HTML 注释占位（≥10 字节防重跑误判缺失），注释不落成品。

**坑 #6（2026-09-10 复检《在祈祷中相遇》发现）：页眉残体与"标题重复"也会混进成品。** 表现：①正文里独立成行出现「第X章 某某」——实测源自 `ocr/033.json` 页尾「第百章 天主的爱永不止息 033」（OCR 把"第五章"读成"第百章"，校对猜回"第五章"却当正文留下）；②紧跟代码标题之后又重复同一标题行（印刷章名页行，如「感谢」「如同耶稣一般地宽恕」——OCR 页首就是「感谢\n\n史考特…」）。对策（已内置 proofread 合并 + clean.py + deliver 出口闸）：`common.strip_md_residues` 删「第X章/部 某某」形独立短行（行尾带句末标点的引用句不删）与紧跟标题的重复行；窗口按标题长度自适应——标题 ≥4 字时看其后 6 个非空行，短标题（「感谢」「前言」「受辱」）只认紧邻的第一个非空行，避免误删正文里同名的小节标题。`clean.py --suspect` 列出疑似短行供人工复核。**教训：同类检查要按"家族"扫，别只扫已报告的形态**——本书 badcase 报告只提占位符与客服废话，页眉残体是顺带复检才发现的。

## 质量原则

- **忠实 > 完整纠错**：OCR 和校对都不许改写、增删、翻译、总结。
- 章节标题以用户提供的目录（`toc.txt` / `[[units]]`）为权威，不臆造。
- 校对只做：合并断行（祈祷文/诗歌分行保留）、修明显 OCR 错字、还原被误识为「·」的标点、删页眉页脚页码。
- 目录页/封面页/部扉页（纯印刷标题、可能空白）不入正文；正文为空的单元不吞代码标题。
- 印刷章名行与页眉不得在正文重复出现：标题由代码唯一提供，正文里出现同形独立短行即残体（交付前用出口闸拦）。
- 清洗只删"整行"可证伪杂质（占位符行 + 客服话术）；正文里"我猜您…"这类对话句不属于杂质。
- 不确定的错字宁可保留原文，不要猜改。
