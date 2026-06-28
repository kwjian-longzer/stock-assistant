#!/bin/bash
# ============================================================
# setup.sh — 观澜踏浪项目环境初始化脚本 v5.0
# ============================================================
# 用途：定时任务在新沙盒中执行时，项目代码和配置文件不存在。
#       本脚本完成：配置创建 → 依赖安装 → 数据库初始化。
# 用法：
#   export CONFIG_JSON='{"tushare_token":"...","feishu_webhook":"...","app_id":"...","app_secret":"...","fxbaogao_api_key":"..."}'
#   cd /workspace/stock-assistant && bash setup.sh
# ============================================================

set -e
cd "$(dirname "$0")"
echo "============================================================"
echo "观澜踏浪 环境初始化 v5.0"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ----------------------------------------------------------
# 1. 创建 config.json（如果不存在且 CONFIG_JSON 已设置）
# ----------------------------------------------------------
if [ ! -f config.json ]; then
    if [ -n "$CONFIG_JSON" ]; then
        echo "$CONFIG_JSON" > config.json
        echo "[OK] config.json 已从 CONFIG_JSON 环境变量创建"
    else
        echo "[WARN] config.json 不存在且 CONFIG_JSON 未设置，创建空配置"
        echo '{"tushare_token":"","feishu_webhook":"","app_id":"","app_secret":"","fxbaogao_api_key":""}' > config.json
    fi
else
    echo "[OK] config.json 已存在，跳过"
fi

# ----------------------------------------------------------
# 2. 安装 Python 依赖
# ----------------------------------------------------------
echo ""
echo "--- 安装 Python 依赖 ---"
pip install tushare requests beautifulsoup4 --break-system-packages -q 2>/dev/null || true
echo "[OK] Python 依赖安装完成"

# ----------------------------------------------------------
# 3. 初始化数据库（如果 stock.db 不存在）
# ----------------------------------------------------------
echo ""
echo "--- 初始化数据库 ---"
if [ ! -f data/stock.db ]; then
    mkdir -p data
    python3 -c "
from db import Database
db = Database()
print(f'[OK] 数据库已创建: {db.db_path}')
print(f'     表数: {len(db.TABLES)}')
" 2>/dev/null || echo "[WARN] 数据库初始化失败，将在首次运行时自动创建"
else
    echo "[OK] data/stock.db 已存在，跳过"
fi

# ----------------------------------------------------------
# 4. 验证关键文件存在
# ----------------------------------------------------------
echo ""
echo "--- 验证关键文件 ---"
for f in SKILL.md config.json settings.py db.py cls_collector.py report_generator.py data_collector.py gold_stock_discovery.py heat_tracker.py insight_engine.py api_server.py push_feishu.py site_builder.py; do
    if [ -f "$f" ]; then
        echo "  [OK] $f"
    else
        echo "  [MISSING] $f"
    fi
done

echo ""
echo "============================================================"
echo "环境初始化完成"
echo "============================================================"
