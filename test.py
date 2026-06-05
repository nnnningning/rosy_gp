import requests
import os

import requests
import os

# 🔴 添加这一行，检查环境变量
print(f"AI_KEY的值: {os.environ.get('AI_REPLY_KEY')}")

AI_KEY = os.environ.get('AI_REPLY_KEY')
AI_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_MODEL = "deepseek/deepseek-v4-pro"
res = requests.post(AI_URL,
    headers={"Authorization": f"Bearer {AI_KEY}"},
    json={
        "model": GEMINI_MODEL,
        "messages": [{"role": "user", "content": "测试"}],
        "max_tokens": 10
    })

print(f"状态码: {res.status_code}")
print(f"响应: {res.text}")