"""scan2ebook 共享工具：配置加载、API key、LLM 调用（OpenAI 兼容接口）。

所有脚本共用：加载 book.toml、解析相对路径、读取 API key、发起对话/视觉请求。
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    tomllib = None

_key_cache = {}


def load_config(path="book.toml"):
    """读取 TOML 配置，返回 (cfg, root)。root 为配置所在目录（项目根）。"""
    if tomllib is None:
        raise SystemExit("需要 Python 3.11+（内置 tomllib）")
    cfg_path = os.path.abspath(path)
    if not os.path.exists(cfg_path):
        raise SystemExit(f"未找到配置文件 {cfg_path}；请先参照 examples/book.toml 创建 book.toml")
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    return cfg, os.path.dirname(cfg_path)


def resolve(root, p):
    """把相对路径解析到项目根之下。"""
    return p if os.path.isabs(p) else os.path.join(root, p)


def api_url(cfg):
    return cfg.get("models", {}).get("base_url", "https://llmapi.paratera.com/v1/chat/completions")


def load_api_key(cfg):
    """优先级：环境变量 > book.toml models.api_key > DSH harness 凭据文件。"""
    env = cfg.get("models", {}).get("api_key_env", "PARATERA_API_KEY")
    if env in _key_cache:
        return _key_cache[env]
    if os.environ.get(env):
        _key_cache[env] = os.environ[env]
        return _key_cache[env]
    explicit = cfg.get("models", {}).get("api_key", "")
    if explicit:
        _key_cache[env] = explicit
        return explicit
    cred = os.path.expanduser("~/Library/Application Support/dsh-desktop/harness/.credentials.yaml")
    if os.path.exists(cred):
        for line in open(cred, encoding="utf-8", errors="replace"):
            m = re.match(rf"\s*{re.escape(env)}:\s*[\"']?([^\"'\s]+)", line)
            if m:
                _key_cache[env] = m.group(1)
                return _key_cache[env]
    raise SystemExit(f"未找到 API key：请设置环境变量 {env} 或在 book.toml 的 models.api_key 填入")


def _post(cfg, payload, timeout):
    key = load_api_key(cfg)
    req = urllib.request.Request(
        api_url(cfg), data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode())
    return j["choices"][0]["message"]["content"], j.get("usage", {})


def chat(cfg, messages, max_tokens, model=None, temperature=0, timeout=240):
    """纯文本对话（校对/分章用 proofread 模型）。"""
    payload = {
        "model": model or cfg.get("models", {}).get("proofread", "GLM-4.5-Air"),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    return _post(cfg, payload, timeout)


def vision(cfg, image_b64, text, max_tokens, model=None, temperature=0, timeout=120):
    """图像 + 文本（OCR 用视觉模型）。"""
    payload = {
        "model": model or cfg.get("models", {}).get("ocr", "GLM-4V-Flash"),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
            {"type": "text", "text": text},
        ]}],
    }
    return _post(cfg, payload, timeout)


def image_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def parse_pages(s, total):
    """'1,4-6' -> [1,4,5,6]；空 -> [1..total]。"""
    if not s.strip():
        return list(range(1, total + 1))
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


# ---------------------------------------------------------------
# 确定性杂质清洗（badcase 2026-09-10：workbuddy 入库反馈成品含 OCR 杂质）
# 只删"整行"可证伪杂质，绝不动正文：
#   A) `[第N页]` —— proofread 输入的分页占位符被校对 LLM 原样复读（occasionally）
#   B) OCR 客服废话 —— 视觉模型在空白/纯插图页不守"不要任何说明"指令输出的口水话
# ---------------------------------------------------------------

_PAGE_MARKER_RE = re.compile(r"^\s*\[第\s*\d+\s*页\]\s*$")

# GLM-4V-Flash 在空白/插图页上的实测变体（如 ocr/015,119,141,173,235,267,269,301,321）。
# 均为机器语境强特征词，正文行不会误中（整行匹配 + 长度上限双保险）。
_CHATTER_SUBSTRINGS = (
    "图中正文",                    # 我猜您想让我识别图中正文…（空白/只有插图）
    "识别图片中的文字",             # …我可以帮助您识别图片中的文字…
    "图中已有的文字内容",           # …帮助您识别图中已有的文字内容。
    "上传完整的图片",               # 请您重新上传完整的图片…
    "上传包含所有内容的完整图片",
    "为您提取图中所有文本",
    "我猜您可能没有上传",
    "如果您有其他要求，我将为您解答",
    "如果您需要的话，我可以帮助您识别",
    "如果您需要提取图中所有文本的话",
)
_CHATTER_MAX_LEN = 120  # 客服废话均为单句短行；超过此长度的行不可能是整行废话


def strip_artifacts(text):
    """确定性删除流水线杂质行（[第N页] 占位符、OCR 客服废话）。

    返回 (清洗后文本, 被删行列表)；仅删整行、只删可证伪杂质，
    多余空行折叠为最多一个空行（不影响诗歌/祈祷文结构）。
    """
    if not text:
        return text, []
    removed, kept = [], []
    for ln in text.split("\n"):
        s = ln.strip()
        if _PAGE_MARKER_RE.match(ln):
            removed.append(ln)
            continue
        if s and len(s) <= _CHATTER_MAX_LEN and any(k in s for k in _CHATTER_SUBSTRINGS):
            removed.append(ln)
            continue
        kept.append(ln)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return cleaned, removed


_COMMENT_RE = re.compile(r"^\s*<!--.*-->\s*$", re.S)


def is_comment_only(text):
    """整段只剩（单个）HTML 注释：用于合并时跳过"已清空"的单元块。"""
    s = text.strip()
    return bool(s) and bool(_COMMENT_RE.match(s))


# ---------------------------------------------------------------
# 成品级残体清洗（badcase 家族 #2：2026-09-10 复检《在祈祷中相遇》发现）
#   A) 页眉/标题残体：正文里独立成行的「第X章 某某」——本流水线标题恒由代码插入，
#      正文出现同形短行 = 印刷页眉被 OCR 读出、校对未删（实测 ocr/033.json 页尾
#      「第百章 天主的爱永不止息 033」）。
#   B) 标题重复：紧跟代码标题之后又重复同一标题的行（印刷章名行被当正文保留）。
# 仅用于"成品/缓存"层；OCR 与校对阶段尚无标题，那边用 strip_artifacts。
# ---------------------------------------------------------------

_MD_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_CHAPTER_PREFIX_RE = re.compile(r"^第\s*[一二三四五六七八九十百零〇\d]{1,4}\s*[章部节篇]\s*")
_TITLE_RESIDUE_RE = re.compile(r"^第\s*[一二三四五六七八九十百零〇\d]{1,4}\s*[章部节篇]\s+\S.{0,28}$")
_SENT_END_RE = re.compile(r"[。，；：！？、）)\]\"”』」…]\s*$")


def strip_md_residues(text, window=6):
    """成品级清洗：删标题残体（A）与紧跟标题的重复行（B）。返回 (cleaned, removed)。

    只作用于正文行，绝不碰 markdown 标题本身；行尾带句末标点的引用句（如
    「详见第五章 某某。」）不会被误删。

    B 的窗口按标题长度自适应：标题 ≥4 字时允许出现在其后 6 个非空行内
    （印刷章名行有时隔一行）；短标题（如「感谢」「前言」「受辱」）只认紧邻的第一个
    非空行——那是印刷章名页的强签名，避免误删正文里的同名小节标题。
    """
    if not text:
        return text, []
    removed, kept = [], []
    head_text, head_core, remaining = None, None, 0
    for ln in text.split("\n"):
        s = ln.strip()
        m = _MD_HEAD_RE.match(ln)
        if m:
            head_text = m.group(2).strip()
            head_core = _CHAPTER_PREFIX_RE.sub("", head_text).strip()
            long_enough = max(len(head_text), len(head_core or "")) >= 4
            remaining = window if long_enough else 1
            kept.append(ln)
            continue
        if not s:
            kept.append(ln)
            continue
        if remaining > 0:
            remaining -= 1
            if s == head_text or (head_core and s == head_core):
                removed.append(ln)
                continue
        if _TITLE_RESIDUE_RE.match(s) and not _SENT_END_RE.search(s):
            removed.append(ln)
            continue
        kept.append(ln)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return cleaned, removed


def suspect_lines(text):
    """只报告不修改：疑似页眉/标题式独立短行（供人工复核，如 clean.py --suspect）。"""
    out = []
    for i, ln in enumerate(text.split("\n"), 1):
        s = ln.strip()
        if not s or _MD_HEAD_RE.match(ln) or s.startswith(">"):
            continue
        if not (5 <= len(s) <= 34):
            continue
        if _SENT_END_RE.search(s):
            continue
        if _TITLE_RESIDUE_RE.match(s) or re.match(r"^[\u4e00-\u9fff][^。！？]{4,33}$", s):
            out.append((i, s))
    return out
