"""最小 OpenRouter API 健康检查。

加载 .env、用 bot_lite 同样的 model 名（google/gemini-3-flash-preview）发 1 条
最便宜的请求，看 HTTP status：

  200 + content -> key 正常
  401 -> key 失效/无权
  402 -> quota 用完
  其它 -> 看 body
"""

from __future__ import annotations

import os
import sys

# 加载 .env（跟 bot_lite 的解析逻辑一样）
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ[key.strip()] = value.strip().strip("'\"")

api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("api_key", "")
if not api_key:
    print("[FAIL] OPENROUTER_API_KEY 仍然为空，.env 修复没生效")
    sys.exit(1)

print(f"[OK] OPENROUTER_API_KEY loaded, length={len(api_key)}")

import requests

resp = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    json={
        "model": "google/gemini-3-flash-preview",
        "messages": [{"role": "user", "content": "reply with exactly: OK"}],
        "max_tokens": 10,
    },
    timeout=30,
)

print(f"[HTTP {resp.status_code}]")
print(f"body[:600]: {resp.text[:600]}")
