#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识固化层 (Knowledge Persistor)
================================
将进化引擎的学习成果固化到GitHub持久化文件，实现跨session知识继承。

职责：
  1. 收集进化引擎+外部学习器的产出
  2. 更新knowledge/目录下的JSON/MD文件
  3. 自动git commit + push到GitHub
  4. 确保新session git clone后能继承全部历史经验

集成方式：
  from evolution.knowledge_persistor import persist_and_push
  # 在进化引擎run()之后调用
  persist_and_push(evolution_results, external_results, date_str)
"""

import json
import os
import sys
import subprocess
import datetime
from typing import Dict, Any, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

KNOWLEDGE_DIR = os.path.join(SCRIPT_DIR, "knowledge")
ENGINE_CHANGELOG_PATH = os.path.join(KNOWLEDGE_DIR, "engine_changelog.md")
PROMPT_EVOLUTION_PATH = os.path.join(KNOWLEDGE_DIR, "prompt_evolution.md")
ACCURACY_BENCHMARK_PATH = os.path.join(KNOWLEDGE_DIR, "accuracy_benchmark.json")


def persist_evolution_results(evolution_results: dict, date_str: str) -> dict:
    """将进化引擎的结果固化到knowledge/文件

    Args:
        evolution_results: evolution_engine.run()的返回值
        date_str: 日期 YYYY-MM-DD

    Returns:
        dict: {"files_updated": [...], "changelog_entry": "..."}
    """
    files_updated = []
    changelog_parts = []

    diagnosis = evolution_results.get("diagnosis", {})
    deployed = evolution_results.get("deployed", [])
    monitor = evolution_results.get("monitor", {})

    # 1. 追加到engine_changelog.md
    changelog_entry = _format_changelog_entry(
        date_str, diagnosis, deployed, monitor
    )
    _append_to_md(ENGINE_CHANGELOG_PATH, changelog_entry)
    files_updated.append("knowledge/engine_changelog.md")
    changelog_parts.append(changelog_entry)

    # 2. 如果有部署，factor_weights.json已被engine.py直接更新
    # 这里只记录变更日志
    if deployed:
        for dep in deployed:
            for f in dep.get("files_modified", []):
                if f not in files_updated:
                    files_updated.append(f)

    return {
        "files_updated": files_updated,
        "changelog_entry": "\n".join(changelog_parts),
    }


def persist_external_lessons(external_results: dict, date_str: str) -> dict:
    """将外部学习器的成果固化

    Args:
        external_results: external_learner.run()的返回值
        date_str: 日期

    Returns:
        dict: {"files_updated": [...], "new_signals": [...]}
    """
    files_updated = []
    new_signals = []

    # external_learner.run()内部已调用_persist_lessons写入external_lessons.md
    # 这里补充提取需要传递给其他文件的信息

    review = external_results.get("review", {})
    blind_spots = external_results.get("blind_spots", {})
    external_views = external_results.get("external_views", {})
    patterns = external_results.get("patterns", {})

    # 提取新信号（来自外部观点对齐）
    new_signals = external_views.get("new_signals_to_add", [])
    if new_signals:
        # 写入prompt_evolution.md，供Agent下次写报告时参考
        prompt_patch = _format_prompt_patch(date_str, new_signals, review)
        _append_to_md(PROMPT_EVOLUTION_PATH, prompt_patch)
        files_updated.append("knowledge/prompt_evolution.md")

    # 提取组合因子模式（来自模式发现）
    successful_combos = patterns.get("successful_combinations", [])
    if successful_combos:
        _update_combination_factors(successful_combos)
        files_updated.append("knowledge/factor_weights.json")

    return {
        "files_updated": files_updated,
        "new_signals": new_signals,
    }


def persist_and_push(
    evolution_results: dict,
    external_results: dict,
    date_str: str,
    auto_push: bool = True,
) -> dict:
    """固化全部学习成果并推送到GitHub

    Args:
        evolution_results: 进化引擎结果
        external_results: 外部学习器结果
        date_str: 日期
        auto_push: 是否自动git push（测试时可设为False）

    Returns:
        dict: {"persisted": {...}, "pushed": bool, "commit_sha": "..."}
    """
    print("=" * 60)
    print(f"知识固化层 启动 @ {date_str}")
    print("=" * 60)

    # 1. 固化进化引擎结果
    evo_persisted = persist_evolution_results(evolution_results, date_str)
    print(f"  [进化] 更新文件: {evo_persisted['files_updated']}")

    # 2. 固化外部学习器结果
    ext_persisted = persist_external_lessons(external_results, date_str)
    print(f"  [外部] 更新文件: {ext_persisted['files_updated']}")
    if ext_persisted["new_signals"]:
        print(f"  [外部] 新信号: {ext_persisted['new_signals']}")

    all_files = list(
        set(evo_persisted["files_updated"] + ext_persisted["files_updated"])
    )

    # 3. git commit + push
    pushed = False
    commit_sha = ""
    if auto_push and all_files:
        pushed, commit_sha = _git_commit_and_push(all_files, date_str)
        if pushed:
            print(f"  [Git] 已推送: {commit_sha}")
        else:
            print(f"  [Git] 推送失败（不影响本地固化）")
    elif not all_files:
        print(f"  [Git] 无文件变更，跳过推送")

    print("=" * 60)

    return {
        "persisted": {
            "evolution": evo_persisted,
            "external": ext_persisted,
        },
        "pushed": pushed,
        "commit_sha": commit_sha,
        "files_updated": all_files,
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _format_changelog_entry(
    date_str: str, diagnosis: dict, deployed: list, monitor: dict
) -> str:
    """格式化引擎变更日志条目"""
    lines = [
        f"\n## {date_str} 进化引擎运行",
        f"- 整体命中率: {diagnosis.get('overall_rate', 0):.0%}",
        f"- 样本量: {diagnosis.get('sample_size', 0)}",
        f"- 市场状态: {diagnosis.get('market_regime', 'unknown')}",
        f"- 失败模式: {len(diagnosis.get('failure_patterns', []))}个",
    ]

    # 失败模式摘要
    for fp in diagnosis.get("failure_patterns", [])[:5]:
        lines.append(f"  - [{fp.get('type')}] {fp.get('factor', fp.get('level', ''))}: {fp.get('severity', '')}")

    # 部署摘要
    if deployed:
        lines.append(f"- 部署改进: {len(deployed)}个")
        for dep in deployed:
            lines.append(f"  - {dep.get('deploy_mode', 'unknown')}: {dep.get('files_modified', [])}")
    else:
        lines.append("- 部署改进: 0个（无通过验证的假设）")

    # 监控状态
    if monitor:
        status = monitor.get("status", "unknown")
        lines.append(f"- 监控状态: {status}")
        if status == "rollback_triggered":
            lines.append(f"  ⚠️ 触发回滚: {monitor.get('rollback_reason', '')}")

    lines.append(f"- 时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    return "\n".join(lines)


def _format_prompt_patch(
    date_str: str, new_signals: list, review: dict
) -> str:
    """格式化提示词补丁"""
    lines = [
        f"\n## {date_str} 提示词进化补丁",
        f"### 新增信号关键词",
    ]
    for signal in new_signals:
        lines.append(f"- {signal}")

    # 主线偏差
    deviation = review.get("deviation", "")
    if deviation:
        lines.append(f"\n### 主线偏差提醒")
        lines.append(f"- {deviation}")
        missed = review.get("missed_sectors", [])
        if missed:
            lines.append(f"- 遗漏板块: {', '.join(missed)}")

    lines.append("")
    return "\n".join(lines)


def _update_combination_factors(successful_combos: list) -> None:
    """更新factor_weights.json中的组合因子"""
    try:
        with open(
            os.path.join(KNOWLEDGE_DIR, "factor_weights.json"), "r", encoding="utf-8"
        ) as f:
            data = json.load(f)

        combos = data.get("combination_factors", {})
        for combo in successful_combos:
            factors = combo.get("factors", [])
            if len(factors) < 2:
                continue
            key = "+".join(factors)
            if key not in combos:
                combos[key] = {
                    "condition": " AND ".join(factors),
                    "extra_weight": 5,
                    "description": f"组合因子({combo.get('rate', 0):.0%}命中率)",
                    "hit_rate_history": [combo.get("rate", 0)],
                    "created_date": datetime.datetime.now().strftime("%Y-%m-%d"),
                    "status": "discovered",
                }
            else:
                # 更新历史命中率
                combos[key]["hit_rate_history"].append(combo.get("rate", 0))
                # 如果命中率持续高，提升权重
                rates = combos[key]["hit_rate_history"]
                if len(rates) >= 3 and sum(rates[-3:]) / 3 >= 0.75:
                    combos[key]["extra_weight"] = min(
                        20, combos[key]["extra_weight"] + 2
                    )
                    combos[key]["status"] = "validated"

        data["combination_factors"] = combos
        data["_meta"]["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d")

        with open(
            os.path.join(KNOWLEDGE_DIR, "factor_weights.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [警告] 更新组合因子失败: {e}")


def _append_to_md(path: str, content: str) -> None:
    """追加内容到markdown文件"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)


def _git_commit_and_push(files: list, date_str: str) -> tuple:
    """git add + commit + push

    Args:
        files: 要提交的文件列表（相对项目根目录的路径）
        date_str: 日期

    Returns:
        (success: bool, commit_sha: str)
    """
    try:
        # 确保在项目根目录
        for f in set(files):
            # 转为相对路径
            rel_path = os.path.relpath(
                os.path.join(SCRIPT_DIR, f) if not os.path.isabs(f) else f, SCRIPT_DIR
            )
            subprocess.run(
                ["git", "add", rel_path],
                cwd=SCRIPT_DIR,
                capture_output=True,
                timeout=10,
            )

        commit_msg = f"evolution: knowledge evolution {date_str}"

        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode != 0:
            # 可能是nothing to commit
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                return True, ""
            return False, ""

        # 获取commit sha
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        )
        sha = sha_result.stdout.strip()[:8]

        # push
        push_result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if push_result.returncode != 0:
            print(f"  [Git] push stderr: {push_result.stderr[:200]}")
            return False, sha

        return True, sha

    except subprocess.TimeoutExpired:
        print(f"  [Git] 操作超时")
        return False, ""
    except Exception as e:
        print(f"  [Git] 异常: {e}")
        return False, ""


def load_knowledge_for_agent() -> dict:
    """供Agent在撰写报告前调用的知识加载函数

    读取knowledge/目录下的所有文件，返回结构化知识包，
    让Agent在写报告时能参考历史教训和最新进化规则。

    Returns:
        dict: {
            "factor_weights": {...},
            "lessons_learned": "...",
            "external_lessons": "...",
            "prompt_evolution": "...",
            "accuracy_benchmark": {...},
        }
    """
    knowledge = {}

    # 因子权重
    try:
        with open(
            os.path.join(KNOWLEDGE_DIR, "factor_weights.json"), "r", encoding="utf-8"
        ) as f:
            knowledge["factor_weights"] = json.load(f)
    except Exception:
        knowledge["factor_weights"] = {}

    # 失败案例
    try:
        with open(
            os.path.join(KNOWLEDGE_DIR, "lessons_learned.md"), "r", encoding="utf-8"
        ) as f:
            knowledge["lessons_learned"] = f.read()
    except Exception:
        knowledge["lessons_learned"] = ""

    # 外部学习
    try:
        with open(
            os.path.join(KNOWLEDGE_DIR, "external_lessons.md"), "r", encoding="utf-8"
        ) as f:
            knowledge["external_lessons"] = f.read()
    except Exception:
        knowledge["external_lessons"] = ""

    # 提示词进化
    try:
        with open(
            os.path.join(KNOWLEDGE_DIR, "prompt_evolution.md"), "r", encoding="utf-8"
        ) as f:
            knowledge["prompt_evolution"] = f.read()
    except Exception:
        knowledge["prompt_evolution"] = ""

    # 准确率基线
    try:
        with open(
            os.path.join(KNOWLEDGE_DIR, "accuracy_benchmark.json"), "r", encoding="utf-8"
        ) as f:
            knowledge["accuracy_benchmark"] = json.load(f)
    except Exception:
        knowledge["accuracy_benchmark"] = {}

    return knowledge


if __name__ == "__main__":
    # 独立测试：加载知识包
    print("=== 知识固化层 独立测试 ===")
    k = load_knowledge_for_agent()
    print(f"因子权重: {len(k.get('factor_weights', {}).get('factors', {}))}个因子")
    print(f"失败案例: {len(k.get('lessons_learned', ''))}字符")
    print(f"外部学习: {len(k.get('external_lessons', ''))}字符")
    print(f"提示词进化: {len(k.get('prompt_evolution', ''))}字符")
    benchmark = k.get("accuracy_benchmark", {})
    print(f"准确率基线: 整体={benchmark.get('overall', {}).get('hit_rate', 0):.0%}")
    print("=== 测试完成 ===")
