#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书推送脚本
支持两种推送方式：
  1. Open API：上传文件并发送文件消息（完整报告文件）
  2. Webhook：发送重要提醒+金股摘要卡片（不发送全文）

Open API 流程：
  获取 tenant_access_token → 上传文件获取 file_key → 发送文件消息到群聊
"""

import sys
import os
import json
import re
import argparse
import subprocess
import requests

# 统一配置管理：从环境变量或 config.json 读取敏感信息
from settings import get_feishu_webhook, get_feishu_app_config

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# 默认 Webhook（从 settings.py 统一读取：环境变量优先，其次 config.json）
DEFAULT_WEBHOOK = get_feishu_webhook()

# 默认飞书应用凭证（从 settings.py 统一读取：环境变量优先，其次 config.json）
_feishu_app_config = get_feishu_app_config()
DEFAULT_APP_ID = _feishu_app_config["app_id"]
DEFAULT_APP_SECRET = _feishu_app_config["app_secret"]

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


def extract_summary_from_report(content):
    """从报告MD内容中提取重要提醒和金股

    Args:
        content: 报告MD文本内容

    Returns:
        tuple: (title, alerts_text, gold_stocks)
            - title: 报告标题
            - alerts_text: 重要提醒文本（最多500字）
            - gold_stocks: 金股列表，每个元素为 dict(name, code, level, reason)
    """
    lines = content.split('\n')
    title = ""
    alerts = []
    gold_stocks = []
    current_section = ""
    current_gold = None

    for line in lines:
        line_stripped = line.strip()

        # 提取标题（第一个 # 开头的行）
        if line_stripped.startswith('#') and not title:
            title = line_stripped.lstrip('#').strip()

        # 识别章节
        if '第零章' in line or '重要提醒' in line or '风险提示' in line:
            current_section = "alert"
        elif '金股' in line or '第五章' in line:
            current_section = "gold"
        elif line_stripped.startswith('##') or line_stripped.startswith('###'):
            current_section = ""

        # 收集重要提醒内容
        if current_section == "alert" and line_stripped and not line_stripped.startswith('#'):
            alerts.append(line_stripped)

        # 收集金股内容（结构化解析）
        if current_section == "gold":
            # 匹配金股标题：#### 金股一：领益智造（002600.SZ）—— 强推荐
            gold_header_match = re.match(
                r'^#{1,6}\s*金股[一二三四五六七八九十\d]+\s*[：:]\s*(.+?)[（(](.+?)[）)]',
                line_stripped
            )
            if gold_header_match:
                if current_gold:
                    gold_stocks.append(current_gold)
                level = ""
                level_match = re.search(r'[—\-]+\s*(.+)$', line_stripped)
                if level_match:
                    level = level_match.group(1).strip()
                current_gold = {
                    "name": gold_header_match.group(1).strip(),
                    "code": gold_header_match.group(2).strip(),
                    "level": level,
                    "reason": "",
                }
            elif current_gold and ('推理链' in line_stripped or '推荐理由' in line_stripped):
                reason = re.sub(
                    r'^\*{0,2}\s*(?:推理链|推荐理由)\s*[：:]\s*\*{0,2}',
                    '',
                    line_stripped
                ).strip()
                if reason:
                    current_gold["reason"] = reason

    if current_gold:
        gold_stocks.append(current_gold)

    # 拼接重要提醒文本，限制500字
    alerts_text = '\n'.join(alerts[:20])
    if len(alerts_text) > 500:
        alerts_text = alerts_text[:500] + "..."

    return title, alerts_text, gold_stocks


def send_summary_via_webhook(file_path):
    """通过 Webhook 发送重要提醒+金股摘要卡片（不发送全文）

    v3.0: 改为发送网站链接+简报卡片，不再发送全文

    Args:
        file_path: 报告文件路径

    Returns:
        bool: 是否发送成功
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    title, alerts_text, gold_stocks = extract_summary_from_report(content)

    if not title:
        filename = os.path.basename(file_path)
        title = filename.replace('.md', '')

    if not alerts_text:
        alerts_text = "暂无重要提醒"

    # 构造金股推荐文本
    gold_lines = []
    for i, stock in enumerate(gold_stocks[:3], 1):  # v3.0: 只取Top3金股
        line = f"**{i}. {stock['name']}（{stock['code']}）**"
        if stock['level']:
            line += f" —— {stock['level']}"
        if stock['reason']:
            reason = stock['reason']
            if len(reason) > 80:
                reason = reason[:80] + "..."
            line += f"\n   {reason}"
        gold_lines.append(line)
    gold_text = '\n'.join(gold_lines) if gold_lines else "暂无金股推荐"

    # 从文件名提取日期和类型
    base_name = os.path.basename(file_path)
    date_str = base_name.split('_')[0] if '_' in base_name else ""

    # v3.0: 网站链接
    SITE_URL = "https://kwjian-longzer.github.io/stock-assistant/"

    # 提取一句话总结（取重要提醒前150字）
    one_line_summary = alerts_text.replace('\n', ' ')[:150]
    if len(alerts_text) > 150:
        one_line_summary += "..."

    # v3.0: 构造交互卡片（链接+简报模式）
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
                        "content": f"**📈 一句话总结**\n{one_line_summary}"
                    }
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**⭐ 金股速览**\n{gold_text}"
                    }
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "🔗 点击查看完整报告"
                            },
                            "url": SITE_URL,
                            "type": "primary"
                        }
                    ]
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"数据更新: {date_str} | 完整数据可视化见网站"
                        }
                    ]
                }
            ]
        }
    }

    webhook = get_webhook()
    if not webhook:
        print("[WARN] 未配置Webhook，跳过摘要推送")
        return False

    try:
        resp = requests.post(webhook, json=payload, timeout=30)
        result = resp.json()
        if result.get("code") == 0:
            print(f"[OK] Webhook推送成功（链接+简报+{len(gold_stocks[:3])}只金股）")
            return True
        else:
            print(f"[WARN] Webhook发送失败: {result}")
            return False
    except Exception as e:
        print(f"[WARN] Webhook发送异常: {e}")
        return False


# ---------------------------------------------------------------------------
# v5: 飞书文档创建（将报告MD转为飞书在线文档）
# ---------------------------------------------------------------------------

# 观澜踏浪项目飞书文件夹token
FEISHU_PROJECT_FOLDER = "XJm7f2TlGliK0fdXCPLctUIpnMg"


def create_feishu_doc(file_path):
    """v5: 将报告MD文件导入为飞书在线文档

    使用 lark-cli drive +import 将Markdown报告转为飞书Docx，
    放入「观澜踏浪项目」文件夹。

    Args:
        file_path: 报告MD文件路径

    Returns:
        str: 飞书文档URL，失败返回None
    """
    import subprocess

    filename = os.path.basename(file_path)
    # 从文件名提取日期和类型 (如 2026-06-28_晨报.md)
    name_without_ext = os.path.splitext(filename)[0]

    print(f"\n[v5飞书文档] 创建飞书文档: {name_without_ext}")

    try:
        result = subprocess.run(
            ["lark-cli", "drive", "+import",
             "--file", file_path,
             "--folder-token", FEISHU_PROJECT_FOLDER,
             "--type", "docx",
             "--name", f"观澜踏浪纪 — {name_without_ext}",
             "--as", "user"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            # 从输出中提取文档URL/token
            output = result.stdout + result.stderr
            print(f"  [OK] 飞书文档创建成功")
            # 尝试从输出提取token
            import re
            token_match = re.search(r'token[:\s]+([A-Za-z0-9]+)', output)
            if token_match:
                doc_token = token_match.group(1)
                doc_url = f"https://ycnzu4p76s2k.feishu.cn/docx/{doc_token}"
                print(f"  [OK] 文档链接: {doc_url}")
                return doc_url
            print(f"  [INFO] 输出: {output[:200]}")
            return True
        else:
            print(f"  [WARN] 飞书文档创建失败: {result.stderr[:200]}")
            return None
    except FileNotFoundError:
        print("  [WARN] lark-cli未安装，跳过飞书文档创建")
        return None
    except Exception as e:
        print(f"  [WARN] 飞书文档创建异常: {e}")
        return None


def send_feishu_message_with_doc(file_path, doc_url=None):
    """v5: 通过Webhook发送包含飞书文档链接的消息卡片

    Args:
        file_path: 报告文件路径
        doc_url: 飞书文档URL（可选）

    Returns:
        bool: 是否发送成功
    """
    webhook = get_webhook()
    if not webhook:
        print("[WARN] 未配置Webhook，跳过消息发送")
        return False

    filename = os.path.basename(file_path)
    date_str = filename.split('_')[0] if '_' in filename else ""
    name_without_ext = os.path.splitext(filename)[0]

    # 读取报告提取金股和摘要
    gold_stocks = []
    alerts_text = ""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # 提取金股
        import re as _re
        gold_section = _re.search(r'## .*?金股.*?\n(.*?)(?=\n## |\Z)', content, _re.DOTALL)
        if gold_section:
            for line in gold_section.group(1).split('\n'):
                if '|' in line and '---' not in line and '名称' not in line:
                    parts = [p.strip() for p in line.split('|')[1:-1]]
                    if len(parts) >= 2 and parts[0]:
                        gold_stocks.append(parts[:3])
    except Exception:
        pass

    gold_lines = []
    for g in gold_stocks[:3]:
        gold_lines.append(f"• {g[0]} ({g[1]})")
    gold_text = '\n'.join(gold_lines) if gold_lines else "暂无金股推荐"

    SITE_URL = "https://kwjian-longzer.github.io/stock-assistant/"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📊 观澜踏浪纪 — {name_without_ext}"
                },
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**📈 金股速览**\n{gold_text}"
                    }
                },
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": []
                }
            ]
        }
    }

    # 添加按钮
    actions = payload["card"]["elements"][2]["actions"]
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "📄 飞书文档"},
        "url": doc_url or SITE_URL,
        "type": "primary"
    })
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "🌐 网站查看"},
        "url": SITE_URL,
        "type": "default"
    })

    try:
        resp = requests.post(webhook, json=payload, timeout=30)
        result = resp.json()
        if result.get("code") == 0:
            print(f"[OK] 飞书消息发送成功（含文档链接+网站链接）")
            return True
        else:
            print(f"[WARN] 消息发送失败: {result}")
            return False
    except Exception as e:
        print(f"[WARN] 消息发送异常: {e}")
        return False


# ---------------------------------------------------------------------------
# 钱三强选股结果MD文件生成
# ---------------------------------------------------------------------------

def generate_qsq_md_report(qsq_json_path, report_date_str):
    """从 qian_sanqiang_results.json 生成可读的MD文件

    Args:
        qsq_json_path: qian_sanqiang_results.json 文件路径
        report_date_str: 报告日期字符串，如 "2026-06-24"

    Returns:
        str: 生成的MD文件路径，失败返回 None
    """
    if not os.path.exists(qsq_json_path):
        print(f"[WARN] 钱三强选股结果文件不存在: {qsq_json_path}")
        return None

    try:
        with open(qsq_json_path, 'r', encoding='utf-8') as f:
            qsq = json.load(f)
    except Exception as e:
        print(f"[ERROR] 读取选股结果失败: {e}")
        return None

    trade_date = qsq.get('trade_date', report_date_str)
    summary = qsq.get('summary', {})
    selected = qsq.get('selected_stocks', [])
    two_of_three = qsq.get('two_of_three_stocks', [])

    lines = []
    lines.append(f"# 钱三强选股结果（{report_date_str}）")
    lines.append("")
    lines.append(f"> 交易日: {trade_date} | 资金流向日期: {qsq.get('moneyflow_date', '数据暂缺')}")
    lines.append(f"> 参与计算股票: {summary.get('total_stocks', '数据暂缺')} 只")
    lines.append("")
    lines.append("## 选股统计")
    lines.append("")
    lines.append(f"| 条件 | 通过数量 |")
    lines.append(f"|------|---------|")
    lines.append(f"| 第一强(多条件创新高) | {summary.get('pass_di_yi_qiang', '数据暂缺')} 只 |")
    lines.append(f"| 第二强(智能换手率) | {summary.get('pass_di_er_qiang', '数据暂缺')} 只 |")
    lines.append(f"| 第三强(资金共振) | {summary.get('pass_di_san_qiang', '数据暂缺')} 只 |")
    lines.append(f"| **三强合一(最终选股)** | **{summary.get('pass_all_three', '数据暂缺')} 只** |")
    lines.append("")

    # 三强合一选股
    if selected:
        lines.append("## 三强合一选股结果")
        lines.append("")
        lines.append("| 序号 | 代码 | 名称 | 行业 | 收盘价 | 涨幅 | 换手率 | 机构资金(万) | 游资资金(万) | EMA55角度 |")
        lines.append("|------|------|------|------|--------|------|--------|-------------|-------------|-----------|")
        for i, s in enumerate(selected, 1):
            lines.append(
                f"| {i} | {s.get('ts_code','')} | {s.get('name','')} | {s.get('industry','')} | "
                f"{s.get('close','')} | {s.get('pct_chg','')}% | {s.get('turnover_rate','')}% | "
                f"{s.get('jigou_zijin','')} | {s.get('youzi_zijin','')} | {s.get('ema55_angle','')}° |"
            )
        lines.append("")

    # 满足两强的股票
    if two_of_three:
        lines.append("## 满足两强条件股票（前30只）")
        lines.append("")
        lines.append("| 序号 | 代码 | 名称 | 行业 | 收盘价 | 涨幅 | 换手率 | 机构资金(万) | 游资资金(万) | 通过条件 |")
        lines.append("|------|------|------|------|--------|------|--------|-------------|-------------|---------|")
        for i, s in enumerate(two_of_three[:30], 1):
            conds = []
            if s.get('di_yi_qiang'): conds.append("第一强")
            if s.get('di_er_qiang'): conds.append("第二强")
            if s.get('di_san_qiang'): conds.append("第三强")
            cond_str = "+".join(conds)
            lines.append(
                f"| {i} | {s.get('ts_code','')} | {s.get('name','')} | {s.get('industry','')} | "
                f"{s.get('close','')} | {s.get('pct_chg','')}% | {s.get('turnover_rate','')}% | "
                f"{s.get('jigou_zijin','')} | {s.get('youzi_zijin','')} | {cond_str} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("*本文件由钱三强选股公式自动生成，数据来源: Tushare API*")
    lines.append(f"*生成时间: {report_date_str}*")

    md_content = '\n'.join(lines)

    # 保存到reports目录
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
    if not os.path.exists(reports_dir):
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)

    md_path = os.path.join(reports_dir, f"{report_date_str}_钱三强选股.md")
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"[OK] 钱三强选股MD文件已生成: {md_path}")
    return md_path


# ---------------------------------------------------------------------------
# VIP信息表MD文件生成（v2.0）
# ---------------------------------------------------------------------------

def generate_vip_md_from_summary(data_dir, report_date_str):
    """从 data_summary.json 的 chapter_vip 生成VIP信息表MD文件

    Args:
        data_dir: data 目录路径
        report_date_str: 报告日期，如 "2026-06-25"

    Returns:
        str: 生成的MD文件路径，无数据返回 None
    """
    summary_path = os.path.join(data_dir, "data_summary.json")
    if not os.path.exists(summary_path):
        return None

    try:
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
    except Exception as e:
        print(f"[WARN] 读取data_summary.json失败: {e}")
        return None

    chapter_vip = summary.get("chapter_vip", {})
    if not isinstance(chapter_vip, dict):
        print("[SKIP] chapter_vip 非字典，跳过VIP信息表MD生成")
        return None

    # v2: 只要文章数>0就生成MD（即使vip_stocks为空，也含文章清单+催化主题）
    total_articles = chapter_vip.get("total_articles", 0)
    has_stocks = bool(chapter_vip.get("vip_stocks"))
    if total_articles == 0 and not has_stocks:
        print("[SKIP] chapter_vip 无文章无股票，跳过VIP信息表MD生成")
        return None

    print(f"[VIP] 生成VIP信息表MD: {total_articles} 篇文章, "
          f"{'有' if has_stocks else '无'}股票匹配")

    # 用 vip_extractor 的 MD 生成函数
    try:
        from vip_extractor import generate_vip_md_report as _gen_vip_md
        md_path = _gen_vip_md(chapter_vip, report_date_str)
        return md_path
    except ImportError:
        # fallback: 直接生成简单MD
        return _generate_vip_md_fallback(chapter_vip, report_date_str)
    except Exception as e:
        print(f"[WARN] VIP信息表MD生成失败: {e}")
        return None


def _generate_vip_md_fallback(vip_table, report_date_str):
    """VIP信息表MD生成（fallback，不依赖vip_extractor）"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    reports_dir = os.path.join(script_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    lines = []
    lines.append(f"# VIP研报信息表（{report_date_str}）")
    lines.append("")
    total_articles = vip_table.get("total_articles", 0)
    total_extracted = vip_table.get("total_extracted", 0)
    themes = vip_table.get("catalyst_themes", [])
    lines.append(f"> VIP文章: {total_articles} 篇 | "
                 f"匹配股票: {total_extracted} 只 | "
                 f"催化主题: {len(themes)} 个")
    lines.append("")

    # 催化主题
    if themes:
        lines.append("## 催化主题汇总")
        lines.append("")
        lines.append("| 关键词 | 出现次数 |")
        lines.append("|--------|---------|")
        for t in themes[:15]:
            lines.append(f"| {t.get('keyword','')} | {t.get('mentions',0)} |")
        lines.append("")

    # 文章清单
    article_list = vip_table.get("article_list", [])
    if article_list:
        lines.append("## VIP文章清单")
        lines.append("")
        lines.append("| 序号 | 类型 | 催化关键词 | 股票匹配 | 标题 |")
        lines.append("|------|------|-----------|---------|------|")
        for i, a in enumerate(article_list, 1):
            kws = ", ".join(a.get("keywords", [])[:5])
            matched = ", ".join(a.get("matched_stocks", [])) if a.get("has_stock_match") else "-"
            lines.append(f"| {i} | {a.get('type','')} | {kws} | {matched} | {a.get('title','')[:40]} |")
        lines.append("")

    # 股票表
    stocks = vip_table.get("vip_stocks", [])
    if stocks:
        lines.append("## VIP研报关联股票")
        lines.append("")
        lines.append("| 序号 | 代码 | 名称 | 板块 | 行业 | 催化关键词 | 来源研报 |")
        lines.append("|------|------|------|------|------|-----------|---------|")
        for i, s in enumerate(stocks, 1):
            keywords = ", ".join(s.get("catalyst_keywords", [])[:5])
            lines.append(
                f"| {i} | {s.get('stock_code','')} | {s.get('stock_name','')} | "
                f"{s.get('sector','')} | {s.get('industry','')} | {keywords} | {s.get('source_article','')[:40]} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(f"*生成时间: {report_date_str}*")

    md_path = os.path.join(reports_dir, f"{report_date_str}_VIP信息表.md")
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"[OK] VIP信息表MD文件已生成(fallback): {md_path}")
    return md_path


# ---------------------------------------------------------------------------
# Git 自动提交与推送（v2.0: 报告和数据存入仓库）
# ---------------------------------------------------------------------------

def git_commit_and_push(report_path):
    """将报告和数据文件提交到 GitHub 仓库

    在飞书推送成功后自动执行，确保报告MD、数据摘要、钱三强选股结果
    持久化到仓库，供后续质量评估和周末汇总使用。

    Args:
        report_path: 本次推送的报告文件路径

    Returns:
        bool: 是否成功提交并推送
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"\n{'='*60}")
    print("  [步骤5] Git 提交报告与数据到仓库")
    print(f"{'='*60}")

    # 确保在项目根目录执行 git 命令
    git_cmd_prefix = ["git", "-C", script_dir]

    def run_git(args, check=False):
        """执行 git 命令并返回结果"""
        cmd = git_cmd_prefix + args
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if check and result.returncode != 0:
                print(f"  [ERROR] git {' '.join(args)} 失败: {result.stderr.strip()}")
            return result
        except subprocess.TimeoutExpired:
            print(f"  [ERROR] git {' '.join(args)} 超时")
            return None
        except Exception as e:
            print(f"  [ERROR] git {' '.join(args)} 异常: {e}")
            return None

    # 确保 git remote 有认证 token（用于 push）
    remote_check = run_git(["remote", "get-url", "origin"])
    if remote_check and remote_check.stdout and "x-access-token" not in remote_check.stdout:
        # 尝试从 config.json 读取 git_token
        git_token = ""
        config_path = os.path.join(script_dir, "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
                git_token = cfg.get("git_token", "")
            except Exception:
                pass
        # 尝试从 GIT_TOKEN 环境变量读取
        if not git_token:
            git_token = os.environ.get("GIT_TOKEN", "")
        # 配置 remote URL
        if git_token:
            auth_url = f"https://x-access-token:{git_token}@github.com/kwjian-longzer/stock-assistant.git"
            run_git(["remote", "set-url", "origin", auth_url])
            print("  [OK] git remote 已配置认证token")
        else:
            print("  [WARN] 无认证token，git push 可能失败")

    # 检查是否在 git 仓库中
    rev_parse = run_git(["rev-parse", "--git-dir"])
    if not rev_parse or rev_parse.returncode != 0:
        print("  [SKIP] 不在 git 仓库中，跳过提交")
        return False

    # 设置 git 用户信息（自动任务环境可能未配置）
    run_git(["config", "user.name", "stock-assistant-bot"])
    run_git(["config", "user.email", "bot@stock-assistant"])

    # 从报告文件名提取日期和类型
    base_name = os.path.basename(report_path)
    report_label = base_name.replace('.md', '')

    # 收集要提交的文件
    files_to_add = []

    # 1. 报告MD文件
    if os.path.exists(report_path):
        files_to_add.append(report_path)

    # 2. 钱三强选股MD文件（如果有）
    reports_dir = os.path.join(script_dir, "reports")
    date_str = base_name.split('_')[0] if '_' in base_name else ""
    qsq_md = os.path.join(reports_dir, f"{date_str}_钱三强选股.md")
    if os.path.exists(qsq_md):
        files_to_add.append(qsq_md)

    # 2.1 v2.0: VIP信息表MD文件（如果有）
    vip_md = os.path.join(reports_dir, f"{date_str}_VIP信息表.md")
    if os.path.exists(vip_md):
        files_to_add.append(vip_md)

    # 3. 数据摘要文件
    data_summary_path = os.path.join(script_dir, "data", "data_summary.json")
    if os.path.exists(data_summary_path):
        files_to_add.append(data_summary_path)

    # 4. 钱三强选股结果JSON
    qsq_json_path = os.path.join(script_dir, "data", "qian_sanqiang_results.json")
    if os.path.exists(qsq_json_path):
        files_to_add.append(qsq_json_path)

    # 5. v2.0: 电报归档目录（增量归档，跨任务共享）
    archive_dir = os.path.join(script_dir, "data", "cls_telegraph_archive")
    if os.path.isdir(archive_dir):
        # 添加目录下所有 .json 文件
        for fname in os.listdir(archive_dir):
            if fname.endswith('.json'):
                files_to_add.append(os.path.join(archive_dir, fname))

    # 6. v3.0: 网站数据目录（docs/data/）
    docs_data_dir = os.path.join(script_dir, "docs", "data")
    if os.path.isdir(docs_data_dir):
        for root, dirs, files in os.walk(docs_data_dir):
            for fname in files:
                if fname.endswith('.json'):
                    files_to_add.append(os.path.join(root, fname))

    # 7. v3.0: 热度数据文件
    heat_data_path = os.path.join(script_dir, "data", "heat_data.json")
    if os.path.exists(heat_data_path):
        files_to_add.append(heat_data_path)

    if not files_to_add:
        print("  [SKIP] 没有需要提交的文件")
        return False

    # git add 各文件
    for f in files_to_add:
        rel_path = os.path.relpath(f, script_dir)
        run_git(["add", rel_path])
        print(f"  [ADD] {rel_path}")

    # 检查是否有暂存的变更
    diff_result = run_git(["diff", "--cached", "--quiet"])
    if diff_result and diff_result.returncode == 0:
        print("  [SKIP] 没有新的变更需要提交")
        return True  # 不是错误，只是没有变更

    # git commit
    commit_msg = f"report: {report_label} 报告+数据自动提交"
    commit_result = run_git(["commit", "-m", commit_msg], check=True)
    if not commit_result or commit_result.returncode != 0:
        print("  [ERROR] git commit 失败")
        return False
    print(f"  [OK] 提交成功: {commit_msg}")

    # git push
    push_result = run_git(["push", "origin", "main"], check=True)
    if not push_result or push_result.returncode != 0:
        print("  [ERROR] git push 失败")
        return False

    print(f"  [OK] 已推送到 GitHub (main)")
    print(f"{'='*60}")
    return True


# ---------------------------------------------------------------------------
# 主推送函数
# ---------------------------------------------------------------------------

def push_to_feishu(file_path):
    """推送报告到飞书

    流程：
    1. 通过 Open API 上传并发送文件（报告MD + 钱三强选股MD + VIP信息表MD）
    2. 通过 Webhook 发送重要提醒+金股摘要（不发送全文）

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

    # --- 准备钱三强选股MD文件 ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "data")
    qsq_json_path = os.path.join(data_dir, "qian_sanqiang_results.json")

    # 从报告文件名提取日期 (如 2026-06-24_晚报.md -> 2026-06-24)
    base_name = os.path.basename(file_path)
    date_str = base_name.split('_')[0] if '_' in base_name else ""

    # --- v3.0: 飞书只推链接+简报卡片，不再推送全文MD文件 ---
    # v5: 新增飞书文档创建 + 消息发送（含文档链接）
    # 全文报告通过网站查看: https://kwjian-longzer.github.io/stock-assistant/

    # --- 步骤1: 通过 Webhook 发送链接+简报卡片 ---
    print("\n[步骤1] 通过Webhook发送链接+简报卡片...")
    webhook_sent = send_summary_via_webhook(file_path)

    # --- 步骤1.5(v5): 创建飞书在线文档 ---
    print("\n[步骤1.5] v5: 创建飞书在线文档...")
    feishu_doc_url = create_feishu_doc(file_path)
    if feishu_doc_url:
        # 有飞书文档时，额外发一条含文档链接的消息
        print("\n[步骤1.6] v5: 发送飞书消息（含文档链接）...")
        send_feishu_message_with_doc(file_path, feishu_doc_url)

    # --- 步骤3: v3.0 网站数据生成 ---
    # Bug#9修复: report_generator.finalize() 已在步骤4调用过 site_builder，
    # 此处不再重复调用，避免双重构建和潜在的数据竞争
    print(f"\n[步骤3] 网站数据已由 report_generator 生成，跳过")
    site_ok = True

    # --- 步骤4: v3.0 金股回测 ---
    if site_ok:
        print(f"\n[步骤4] 运行金股回测...")
        try:
            import gold_stock_backtest
            gold_stock_backtest.run_backtest()
            print("[OK] 金股回测完成")
        except Exception as e:
            print(f"[WARN] 金股回测失败: {e}")

    # --- 步骤5: Git 提交报告与数据到仓库 ---
    git_ok = git_commit_and_push(file_path)

    # --- 汇总 ---
    print(f"\n{'='*60}")
    files_sent = []
    # file_sent/qsq_sent/vip_sent 在步骤2中可能未定义（Webhook-only模式），默认为False
    file_sent = locals().get('file_sent', False)
    qsq_sent = locals().get('qsq_sent', False)
    vip_sent = locals().get('vip_sent', False)
    if file_sent:
        files_sent.append("报告")
    if qsq_sent:
        files_sent.append("选股")
    if vip_sent:
        files_sent.append("VIP信息表")
    if files_sent:
        print(f"  飞书: {'+'.join(files_sent)}文件发送成功 + 摘要推送{'成功' if webhook_sent else '跳过'}")
    elif webhook_sent:
        print(f"  飞书: 文件发送失败，但摘要推送已发送")
    else:
        print(f"  飞书: 文件和摘要推送均发送失败")
    print(f"  仓库: {'提交+推送成功' if git_ok else '跳过或失败'}")
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
