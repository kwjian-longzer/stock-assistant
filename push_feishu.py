#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书推送脚本
支持推送Markdown格式报告到飞书群机器人
"""

import sys
import os
import json
import argparse
import requests

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# 默认 Webhook（硬编码，确保新会话中也能使用）
DEFAULT_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/bf897c28-ab6c-4da0-9926-dc214a5f1c0b"


def get_feishu_webhook():
    """获取飞书Webhook，优先级：环境变量 > 配置文件 > 默认值"""
    # 1. 优先从环境变量读取
    webhook = os.environ.get("FEISHU_WEBHOOK", "")
    if webhook:
        return webhook

    # 2. 从配置文件读取
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                webhook = config.get("feishu_webhook", "")
                if webhook:
                    return webhook
        except Exception as e:
            print(f"读取配置文件失败: {e}")

    # 3. 使用默认值
    return DEFAULT_WEBHOOK


def save_feishu_webhook(webhook):
    """保存飞书Webhook到配置文件"""
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception:
            pass

    config["feishu_webhook"] = webhook

    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"飞书Webhook已保存到配置文件: {CONFIG_FILE}")
        return True
    except Exception as e:
        print(f"保存配置文件失败: {e}")
        return False


def push_to_feishu(file_path):
    """推送报告到飞书"""
    if not os.path.exists(file_path):
        print(f"错误: 文件不存在 {file_path}")
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 获取文件名作为标题
    filename = os.path.basename(file_path)
    title = filename.replace('.md', '')

    # 飞书interactive消息content最大30720字节，这里用28000留余量
    max_card_content = 28000
    if len(content) > max_card_content:
        content = content[:max_card_content] + "\n\n...[内容已截断]"

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
                            "content": f"报告完整内容请查看文件: {file_path}"
                        }
                    ]
                }
            ]
        }
    }

    FEISHU_WEBHOOK = get_feishu_webhook()

    if not FEISHU_WEBHOOK:
        print("警告: 未配置飞书Webhook，跳过推送")
        print(f"报告内容预览（前500字符）:\n{content[:500]}")
        print(f"\n请配置Webhook：")
        print(f"  方法1 - 环境变量: export FEISHU_WEBHOOK='your_webhook_url'")
        print(f"  方法2 - 配置文件: python push_feishu.py --config 'your_webhook_url'")
        return False

    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=30)
        result = resp.json()
        if result.get("code") == 0:
            print(f"飞书推送成功: {file_path}")
            return True
        else:
            print(f"飞书推送失败: {result}")
            return False
    except Exception as e:
        print(f"飞书推送异常: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='推送报告到飞书')
    parser.add_argument('--file', help='报告文件路径')
    parser.add_argument('--config', help='配置飞书Webhook地址')
    args = parser.parse_args()

    if args.config:
        save_feishu_webhook(args.config)
        # 尝试发送测试消息
        test_payload = {
            "msg_type": "text",
            "content": {"text": "✅ 飞书机器人配置成功！股票助手已就绪。"}
        }
        try:
            resp = requests.post(args.config, json=test_payload, timeout=30)
            result = resp.json()
            if result.get("code") == 0:
                print("测试消息发送成功！")
            else:
                print(f"测试消息发送失败: {result}")
        except Exception as e:
            print(f"测试消息发送异常: {e}")
        return

    if not args.file:
        parser.print_help()
        return

    push_to_feishu(args.file)


if __name__ == "__main__":
    main()
