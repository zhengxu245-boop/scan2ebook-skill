# scan2ebook

把**扫描版/图片版中文书籍 PDF（无文字层）**端到端转成**结构化简体 Markdown + EPUB**。

管线：视觉大模型 OCR → opencc 繁简转换 → LLM 校对分章 → pandoc 出电子书。
全程断点续跑，忠实转录优先（不翻译、不总结、不改写）。

## 安装为 DSH skill

把它放进任意被扫描的 skill 根目录即可（目录 bundle 形式，含 `SKILL.md`）：

- `<projectRoot>/.dsh/skills/` 或 `<projectRoot>/.agents/skills/`
- `~/.dsh/skills/` 或 `~/.agents/skills/`
- 或自定义 skill 目录

示例：

```bash
# GitHub（海外 / 有代理）
git clone https://github.com/zhengxu245-boop/scan2ebook-skill.git ~/.agents/skills/scan2ebook

# Gitee（国内直连，内容同步）
git clone https://gitee.com/foround/scan2ebook-skill.git ~/.agents/skills/scan2ebook
```

## 直接当脚本用（不装 skill）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp examples/book.toml ./book.toml   # 改成你的书
# 依次执行五步，见 SKILL.md 或下方
.venv/bin/python scripts/ocr.py
.venv/bin/python scripts/simplify.py
.venv/bin/python scripts/detect.py --toc toc.txt
.venv/bin/python scripts/proofread.py
.venv/bin/python scripts/deliver.py
```

## 目录

- `SKILL.md` — skill 定义与完整指令（模型选型 + 踩坑清单）
- `scripts/` — 五步脚本 + `common.py` 共享工具
- `examples/book.toml` — 配置样例（含一本真实书的章节单元表）
- `requirements.txt` — Python 依赖

## 系统依赖

`gs`（Ghostscript）、`qpdf`、`pandoc`；macOS 可用 `brew install ghostscript qpdf pandoc`。

## 模型

默认走 paratera（OpenAI 兼容）的 `GLM-4V-Flash`（OCR）+ `GLM-4.5-Air`（校对），
`base_url` 可在 `book.toml` 里换成任意 OpenAI 兼容服务。详见 `SKILL.md` 的模型选型表。
