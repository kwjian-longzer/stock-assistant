#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书推送脚本
支持两种推送方式：
  1. Webhook：发送文本内容卡片（快速预览）
  2. Open API：上传文件并发送文件消息（完整报告文件）

Open API 流程：
  获取 tenant_access_token → 上传文件获取 file_key → 发送文件消息到群聊
"""

import sys
import os
import json
import argparse
import requests

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# 默认 Webhook（硬编码，确保新会话中也能使用）
DEFAULT_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/bf897c28-ab6c-4da0-9926-dc214a5f1c0b"

# 默认飞书应用凭证（硬编码，确保新会话中也能使用）
DEFAULT_APP_ID = "cli_aabeb7dc9a78dcb5"
DEFAULT_APP_SECRET = "pgh5y8ILHKYaSdjduOYF6dQVdUrxgewr"

# 飞书 Open API 基础 URL
FEISHU_BASE = "https://open.feishu.cn/open-apis"


# ---------------------------------------------------------------------------
# 配置管理
# ---------------------------------------------------------------------------

def load_config():
    """加载配置文件"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(key, value):
    """保存配置项"""
    config = load_config()
    config[key] = value
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False


def get_webhook():
    """获取Webhook地址"""
    webhook = os.environ.get("FEISHU_WEBHOOK", "")
    if webhook:
        return webhook
    config = load_config()
    return config.get("feishu_webhook", DEFAULT_WEBHOOK)


def get_app_id():
    """获取App ID"""
    app_id = os.environ.get("FEISHU_APP_ID", "")
    if app_id:
        return app_id
    config = load_config()
    return config.get("feishu_app_id", DEFAULT_APP_ID)


def get_app_secret():
    """获取App Secret"""
    secret = os.environ.get("FEISHU_APP_SECRET", "")
    if secret:
        return secret
    config = load_config()
    return config.get("feishu_app_secret", DEFAULT_APP_SECRET)


def get_chat_id():
    """获取目标群聊chat_id（可选，不配置则自动发现）"""
    chat_id = os.environ.get("FEISHU_CHAT_ID", "")
    if chat_id:
        return chat_id
    config = load_config()
    return config.get("feishu_chat_id", "")


# ---------------------------------------------------------------------------
# 飞书 Open API
# ---------------------------------------------------------------------------

def get_tenant_access_token():
    """获取 tenant_access_token
    
    Returns:
        str: tenant_access_token，失败返回 None
    """
    app_id = get_app_id()
    app_secret = get_app_secret()

    if not app_id or not app_secret:
        print("[WARN] 未配置飞书应用凭证（App ID / App Secret）")
        return None

    url = f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal"
    try:
        resp = requests.post(url, json={
            "app_id": app_id,
            "app_secret": app_secret,
        }, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            token = data.get("tenant_access_token")
            return token
        else:
            print(f"[ERROR] 获取 tenant_access_token 失败: {data}")
            return None
    except Exception as e:
        print(f"[ERROR] 获取 tenant_access_token 异常: {e}")
        return None


def list_chats(token):
    """获取机器人所在的群列表
    
    Args:
        token: tenant_access_token
        
    Returns:
        list: 群列表，每个元素包含 chat_id, name 等字段
    """
    url = f"{FEISHU_BASE}/im/v1/chats"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, params={"page_size": 50}, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("items", [])
        else:
            print(f"[WARN] 获取群列表失败: {data.get('msg', '未知错误')}")
            return []
    except Exception as e:
        print(f"[WARN] 获取群列表异常: {e}")
        return []


def find_target_chat(token):
    """找到目标群聊的 chat_id
    
    优先使用配置的 chat_id，否则自动发现第一个群
    
    Returns:
        str: chat_id，失败返回 None
    """
    # 1. 检查配置的 chat_id
    configured_chat_id = get_chat_id()
    if configured_chat_id:
        return configured_chat_id

    # 2. 自动发现
    chats = list_chats(token)
    if not chats:
        print("[WARN] 机器人未加入任何群聊，无法发送文件")
        print("       请将应用机器人添加到飞书群中（注意：应用机器人 ≠ Webhook自定义机器人）")
        return None

    # 取第一个群
    chat = chats[0]
    chat_id = chat.get("chat_id")
    chat_name = chat.get("name", "未知")
    print(f"[INFO] 自动选择群聊: {chat_name} ({chat_id})")
    return chat_id


def upload_file(token, file_path):
    """上传文件到飞书
    
    Args:
        token: tenant_access_token
        file_path: 本地文件路径
        
    Returns:
        str: file_key，失败返回 None
    """
    url = f"{FEISHU_BASE}/im/v1/files"
    headers = {"Authorization": f"Bearer {token}"}

    filename = os.path.basename(file_path)

    # .md 文件使用 stream 类型
    file_type = "stream"

    try:
        with open(file_path, 'rb') as f:
            files = {
                'file_type': (None, file_type),
                'file_name': (None, filename),
                'file': (filename, f, 'text/plain'),
            }
            resp = requests.post(url, headers=headers, files=files, timeout=60)
            data = resp.json()
            if data.get("code") == 0:
                file_key = data.get("data", {}).get("file_key")
                print(f"[OK] 文件上传成功: {filename} → file_key={file_key}")
                return file_key
            else:
                print(f"[ERROR] 文件上传失败: {data.get('msg', '未知错误')} (code={data.get('code')})")
                # 常见错误提示
                code = data.get("code")
                if code == 234007:
                    print("       → 应用未启用机器人能力，请在飞书开发者后台开启")
                elif code == 99991663:
                    print("       → 权限不足，请检查应用是否已添加'获取与上传图片或文件资源'权限")
                return None
    except Exception as e:
        print(f"[ERROR] 文件上传异常: {e}")
        return None


def send_file_message(token, chat_id, file_key):
    """发送文件消息到群聊
    
    Args:
        token: tenant_access_token
        chat_id: 目标群聊ID
        file_key: 上传文件返回的 file_key
        
    Returns:
        bool: 是否发送成功
    """
    url = f"{FEISHU_BASE}/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    params = {"receive_id_type": "chat_id"}

    payload = {
        "receive_id": chat_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}),
    }

    try:
        resp = requests.post(url, headers=headers, params=params,
                             json=payload, timeout=30)
        data = resp.json()
        if data.get("code") == 0:
            print(f"[OK] 文件消息发送成功!")
            return True
        else:
            print(f"[ERROR] 文件消息发送失败: {data.get('msg', '未知错误')} (code={data.get('code')})")
            code = data.get("code")
            if code == 230002:
                print("       → 机器人不在群组中，请将应用机器人添加到飞书群")
            elif code == 230006:
                print("       → 应用未启用机器人能力")
            elif code == 230013:
                print("       → 机器人可用范围未包含目标用户")
            elif code == 230027:
                print("       → 权限不足，请检查应用是否已添加'获取与发送单聊、群组消息'权限")
            return False
    except Exception as e:
        print(f"[ERROR] 文件消息发送异常: {e}")
        return False


def send_text_card_via_webhook(file_path):
    """通过 Webhook 发送文本内容卡片（快速预览）
    
    Args:
        file_path: 报告文件路径
        
    Returns:
        bool: 是否发送成功
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    filename = os.path.basename(file_path)
    title = filename.replace('.md', '')

    max_card_content = 28000
    if len(content) > max_card_content:
        content = content[:max_card_content] + "\n\n...[内容已截断，完整内容见文件]"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📊 {title}"
                },
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": content
                    }
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"完整报告文件已通过应用机器人单独发送"
                        }
                    ]
                }
            ]
        }
    }

    webhook = get_webhook()
    if not webhook:
        print("[WARN] 未配置Webhook，跳过文本预览")
        return False

    try:
        resp = requests.post(webhook, json=payload, timeout=30)
        result = resp.json()
        if result.get("code") == 0:
            print(f"[OK] Webhook文本预览发送成功")
            return True
        else:
            print(f"[WARN] Webhook发送失败: {result}")
            return False
    except Exception as e:
        print(f"[WARN] Webhook发送异常: {e}")
        return False


# ---------------------------------------------------------------------------
# 主推送函数
# ---------------------------------------------------------------------------

def push_to_feishu(file_path):
    """推送报告到飞书
    
    流程：
    1. 通过 Open API 上传文件并发送文件消息（主要）
    2. 通过 Webhook 发送文本内容卡片（预览）
    
    Args:
        file_path: 报告文件路径
        
    Returns:
        bool: 文件发送是否成功
    """
    if not os.path.exists(file_path):
        print(f"错误: 文件不存在 {file_path}")
        return False

    filename = os.path.basename(file_path)
    print(f"\n{'='*60}")
    print(f"  飞书推送: {filename}")
    print(f"{'='*60}")

    # --- 步骤1: 通过 Open API 发送文件 ---
    print("\n[步骤1] 通过Open API发送文件...")
    token = get_tenant_access_token()
    file_sent = False

    if token:
        print(f"[OK] 获取 tenant_access_token 成功")
        chat_id = find_target_chat(token)

        if chat_id:
            file_key = upload_file(token, file_path)
            if file_key:
                file_sent = send_file_message(token, chat_id, file_key)
    else:
        print("[WARN] 无法获取 tenant_access_token，跳过文件发送")
        print("       可能原因：")
        print("       1. App ID / App Secret 未配置或错误")
        print("       2. 网络连接问题")
        print("       3. 应用未发布或未启用机器人能力")

    # --- 步骤2: 通过 Webhook 发送文本预览 ---
    print(f"\n[步骤2] 通过Webhook发送文本预览...")
    webhook_sent = send_text_card_via_webhook(file_path)

    # --- 汇总 ---
    print(f"\n{'='*60}")
    if file_sent:
        print(f"  ✅ 文件发送成功 + Webhook{'成功' if webhook_sent else '跳过'}")
    elif webhook_sent:
        print(f"  ⚠️ 文件发送失败，但Webhook文本预览已发送")
    else:
        print(f"  ❌ 文件和Webhook均发送失败")
    print(f"{'='*60}")

    return file_sent or webhook_sent


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='推送报告到飞书')
    parser.add_argument('--file', help='报告文件路径')
    parser.add_argument('--config', help='配置飞书Webhook地址')
    parser.add_argument('--set-chat-id', help='配置目标群聊chat_id')
    parser.add_argument('--list-chats', action='store_true', help='列出机器人所在的群聊')
    parser.add_argument('--test', action='store_true', help='发送测试消息')
    args = parser.parse_args()

    # 配置 Webhook
    if args.config:
        save_config("feishu_webhook", args.config)
        test_payload = {
            "msg_type": "text",
            "content": {"text": "✅ 飞书Webhook配置成功！"}
        }
        try:
            resp = requests.post(args.config, json=test_payload, timeout=30)
            result = resp.json()
            if result.get("code") == 0:
                print("✅ Webhook测试消息发送成功！")
            else:
                print(f"❌ Webhook测试失败: {result}")
        except Exception as e:
            print(f"❌ Webhook测试异常: {e}")
        return

    # 配置 chat_id
    if args.set_chat_id:
        save_config("feishu_chat_id", args.set_chat_id)
        print(f"✅ chat_id 已保存: {args.set_chat_id}")
        return

    # 列出群聊
    if args.list_chats:
        print("正在获取群列表...")
        token = get_tenant_access_token()
        if not token:
            print("❌ 无法获取 token，请检查 App ID / App Secret")
            return
        chats = list_chats(token)
        if not chats:
            print("未找到任何群聊。请确认：")
            print("  1. 应用已启用机器人能力")
            print("  2. 应用机器人已被添加到群聊中")
            print("  3. 应用已发布且版本已通过审核")
            return
        print(f"\n找到 {len(chats)} 个群聊:")
        for i, chat in enumerate(chats):
            print(f"  {i+1}. {chat.get('name', '未知')} → chat_id: {chat.get('chat_id')}")
        return

    # 发送测试消息
    if args.test:
        print("正在发送测试消息...")
        token = get_tenant_access_token()
        if not token:
            print("❌ 无法获取 token")
            return
        chat_id = find_target_chat(token)
        if not chat_id:
            print("❌ 未找到目标群聊")
            return
        # 发送文本测试消息
        url = f"{FEISHU_BASE}/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": "✅ 飞书应用机器人测试消息！文件发送功能已就绪。"}),
        }
        try:
            resp = requests.post(url, headers=headers, params={"receive_id_type": "chat_id"},
                                 json=payload, timeout=30)
            data = resp.json()
            if data.get("code") == 0:
                print("✅ 测试消息发送成功！")
            else:
                print(f"❌ 测试消息发送失败: {data}")
        except Exception as e:
            print(f"❌ 测试消息发送异常: {e}")
        return

    # 推送文件
    if not args.file:
        parser.print_help()
        print("\n使用示例:")
        print("  python push_feishu.py --file reports/2026-06-23_午报.md")
        print("  python push_feishu.py --list-chats")
        print("  python push_feishu.py --test")
        return

    push_to_feishu(args.file)


if __name__ == "__main__":
    main()
