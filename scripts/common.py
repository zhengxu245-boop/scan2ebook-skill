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
