import os
import json
import requests
import sys
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ================= 配置区 =================
PACKAGE_NAME = os.environ.get('PACKAGE_NAME')
FEISHU_WEBHOOK = os.environ.get('FEISHU_WEBHOOK_URL')
AI_KEY = os.environ.get('AI_REPLY_KEY')
AI_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_MODEL = "google/gemini-3-flash-preview"

# 1. 鉴权：连接 Google Play
def get_service():
    key_content = os.environ.get('GP_JSON_KEY')
    if not key_content:
        raise ValueError("❌ GP_JSON_KEY 环境变量未设置")
    info = json.loads(key_content)
    creds = service_account.Credentials.from_service_account_info(info)
    return build('androidpublisher', 'v3', credentials=creds)

# 2. 读取 6000 字话术包
def get_skill_pack():
    file_path = 'skill.txt'
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    return "你是一名专业的 App 客服，请用礼貌、专业的语气回复用户。"


def smart_truncate(content, limit=345):
    """
    截断：在限制字数内，尽量保留完整的句子
    """
    if len(content) <= limit:
        return content
    # 1. 尝试寻找最后一个完整的标点符号
    # 兼容中英文：。 ！ ？ . ! ?
    truncated = content[:limit]
    last_punctuation = -1
    for char in ['。', '！', '？', '.', '!', '?']:
        pos = truncated.rfind(char)
        if pos > last_punctuation:
            last_punctuation = pos

    if last_punctuation != -1:
        return truncated[:last_punctuation + 1]

    last_space = truncated.rfind(' ')
    if last_space != -1:
        return truncated[:last_space] + "..."

    return truncated + "..."


# 3. 公共 AI 调用
def call_ai(prompt, temperature=0.3):
    if not AI_KEY:
        print("❌ AI 调用失败: AI_REPLY_KEY 环境变量未设置")
        return None
    try:
        res = requests.post(AI_URL, headers={"Authorization": f"Bearer {AI_KEY}"},
                            json={"model": GEMINI_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": temperature})
        if res.status_code != 200:
            print(f"❌ AI 调用失败: HTTP {res.status_code}, 响应: {res.text[:300]}")
            return None
        if not res.text:
            print("❌ AI 调用失败: 空响应体")
            return None
        try:
            response_data = res.json()
        except json.JSONDecodeError as e:
            print(f"❌ AI 调用失败: JSON 解析失败 - {e}, 原始响应: {res.text[:300]}")
            return None
        if 'choices' not in response_data or not response_data['choices']:
            print(f"❌ AI 调用失败: 无效的 API 响应 - {response_data}")
            return None
        return response_data['choices'][0]['message']['content'].strip()
    except requests.exceptions.RequestException as e:
        print(f"❌ AI 调用失败: 网络请求异常 - {e}")
        return None
    except Exception as e:
        print(f"❌ AI 调用失败: {type(e).__name__} - {e}")
        return None

# 4. 核心 AI 逻辑：生成回复
def generate_ai_reply(text, star, skill_pack, is_update=False):
    update_note = "【注意：这是用户的追评，请针对其更新的内容回答，并感谢其反馈更新】" if is_update else ""
    prompt = f"""
    技能包：\n{skill_pack}
    任务：{update_note}
    用户评价（{star}星）: {text}
    要求：
    1.相同语种回复
    2.严禁超过320字符。
    3.直接输出内容。
    4.如果内容过多，请优先保留核心解决方案，删掉客套话。
    5.回复好评不要过于模板同质化，可以适当增加一些个性化的内容。
    6.用户遇到问题就要让用户去support@rosychat.ai反馈，这很重要！
    """
    content = call_ai(prompt, temperature=0.3)
    return smart_truncate(content) if content else None

# 5. AI 汇总：生成 PM 简报
def generate_pm_report(data_list):
    if not data_list: return "今日无新增回复记录。"
    reviews_context = "\n".join([f"[{d['star']}星] 用户: {d['text'][:30]} | AI: {d['reply'][:30]}" for d in data_list[:30]])
    prompt = f"你是一名 App PM。根据今日数据生成报告：\n{reviews_context}\n要求：总结主要问题，审计AI质量，给出1条产品建议。使用 Emoji，Markdown 格式。"
    return call_ai(prompt, temperature=0.5) or "❌ 报告生成失败"

# 6. 推送：飞书发送
def push_to_feishu(report_md):
    if not FEISHU_WEBHOOK: return
    data = {"msg_type": "text", "content": {"text": f"📊 Google Play 自动回复报告\n{'-'*20}\n{report_md}"}}
    requests.post(FEISHU_WEBHOOK, json=data)

# ================= 主逻辑 =================

def main():
    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    service = get_service()
    skill_pack = get_skill_pack()
    time_threshold = int(time.time()) - (days_back * 24 * 60 * 60)

    print(f"🚀 启动模式：回溯 {days_back} 天 | 目标包名: {PACKAGE_NAME}")

    next_token = None
    report_collector = []
    page = 0

    while True:
        page += 1
        result = service.reviews().list(packageName=PACKAGE_NAME, maxResults=100, token=next_token).execute()
        reviews = result.get('reviews', [])
        next_token = result.get('nextPageToken')

        if not reviews: break
        print(f"📖 正在扫描第 {page} 页...")

        for r in reviews:
            comments = r.get('comments', [])
            user_c = comments[0]['userComment']
            dev_c = comments[1]['developerComment'] if len(comments) > 1 else None

            user_time = int(user_c['lastModified']['seconds'])
            dev_time = int(dev_c['lastModified']['seconds']) if dev_c else 0

            # 核心判断逻辑：从未回过 OR 用户更新时间晚于我们回复的时间
            if user_time >= time_threshold and user_time > dev_time:
                review_id = r['reviewId']
                text = user_c.get('text', '').strip()
                star = user_c.get('starRating', 0)
                is_update = bool(dev_c)

                print(f"🎯 命中目标! ID: {review_id[:6]} {'(追评)' if is_update else ''}")

                reply = generate_ai_reply(text, star, skill_pack, is_update)
                if reply:
                    try:
                        service.reviews().reply(packageName=PACKAGE_NAME, reviewId=review_id, body={'replyText': reply}).execute()
                        report_collector.append({"star": star, "text": text, "reply": reply})
                        time.sleep(0.5)
                    except Exception as e: print(f"❌ 回复失败: {e}")

        # 翻页控制：如果本页最后一条比截止线还旧，且不是首页（兼容排序波动），则停止
        last_item_time = int(reviews[-1]['comments'][0]['userComment']['lastModified']['seconds'])
        if last_item_time < time_threshold or not next_token or page >= 10:
            break

    # --- 任务收尾：生成报告并推送 ---
    if report_collector:
        print("\n📈 正在生成 AI 报告并推送飞书...")
        report = generate_pm_report(report_collector)
        push_to_feishu(report)

    print(f"✅ 任务完成，本次处理 {len(report_collector)} 条评论。")

if __name__ == "__main__":
    main()
