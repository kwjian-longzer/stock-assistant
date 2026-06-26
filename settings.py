#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一配置管理 - 从环境变量或config.json读取敏感信息

确保仓库可以安全公开（GitHub Pages免费托管）
"""

import os
import json

# config.json 在 .gitignore 中，不会被提交
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _load_config():
    """从 config.json 加载配置（如果存在）"""
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


_config = _load_config()


def get_tushare_token():
    """获取 Tushare Token（优先环境变量，其次 config.json）"""
    return (
        os.environ.get("TUSHARE_TOKEN")
        or _config.get("tushare_token")
        or ""
    )


def get_feishu_webhook():
    """获取飞书 Webhook URL"""
    return (
        os.environ.get("FEISHU_WEBHOOK")
        or _config.get("feishu_webhook")
        or _config.get("webhook_url")
        or ""
    )


def get_feishu_app_config():
    """获取飞书 App 配置（Open API）"""
    return {
        "app_id": os.environ.get("FEISHU_APP_ID") or _config.get("app_id", ""),
        "app_secret": os.environ.get("FEISHU_APP_SECRET") or _config.get("app_secret", ""),
        "chat_id": os.environ.get("FEISHU_CHAT_ID") or _config.get("chat_id", ""),
    }


def get_site_url():
    """获取网站URL"""
    return (
        os.environ.get("SITE_URL")
        or _config.get("site_url")
        or "https://kwjian-longzer.github.io/stock-assistant/"
    )
