#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: main.py
模块定位: SAP2FAST 统一命令行 CLI 入口模块（兼容历史子命令与 1104 新版 `--mode` 参数）
================================================================================
【本文件核心作用】
1. 作为整个 SAP2FAST 项目的标准命令行入口，向下直接调用 `sap2fast_full_fix.py` 的核心类，
   彻底修复老版本 `main.py` 因模块间接口断层、变量未定义导致无法启动的问题。
2. 同时支持两种调用风格：
   - 风格一（历史运维习惯的子命令模式）：
     * `python main.py sync-sap --start-date 2024-03-01 --end-date 2024-03-31`：按日从 SAP HANA 同步并导出 43 列 FAST 模板
     * `python main.py reconcile --months 202403 202404`：按月执行 SAP 与 FAST 科目发生额差异核对并补导缺失凭证
     * `python main.py upload`：启动 Chrome CDP 自动登录 FAST 并循环上传 `output/` 下的 Excel 模板
     * `python main.py postfast`：启动 Chrome CDP 自动登录 FAST 并批量勾选 Post 到总账 (GL)
   - 风格二（1104 全量版 `--mode` 扩展模式）：
     * 直接将命令行参数无缝委托给 `sap2fast_full_fix.main()`，支持 `--mode local / export / reconcile / upload / post / all / delete-account / update-account / manage-mapping` 及 `--dry-run` 等全部安全参数。
================================================================================
"""

import os
import sys

# 自动挂载工作区内的 .pydeps 本地持久化依赖目录，避免因缺少全局 pip 包而报错
_PYDEPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pydeps")
if os.path.isdir(_PYDEPS_DIR) and _PYDEPS_DIR not in sys.path:
    sys.path.insert(0, _PYDEPS_DIR)

import arrow
from sap2fast_full_fix import (
    SAPExporter,
    DataReconciler,
    upload_to_fast,
    postToGl,
    main as sap2fast_main,
)


def main():
    """
    CLI 主调度函数：
    优先识别是否为 1104 新版 `--mode` / `--generate-sample` / `--audit` 参数；
    若是历史子命令 (`sync-sap` / `reconcile` / `postfast` / `upload`)，则按子命令分发执行。
    """
    # 1. 若用户传入了 1104 标准 --mode / --generate-sample / --audit 等参数，直接委托给 sap2fast_full_fix.main()
    if any(
        arg.startswith("--") or arg in ("local", "export", "all", "delete-account", "update-account", "manage-mapping")
        for arg in sys.argv[1:]
    ):
        if len(sys.argv) > 1 and sys.argv[1] not in ("sync-sap", "reconcile", "postfast", "upload"):
            return sap2fast_main()

    # 2. 解析历史兼容的四类子命令 (sync-sap / reconcile / postfast / upload)
    import argparse
    parser = argparse.ArgumentParser(description="SAP2FAST CLI 执行工具 (1104 全量修复版)")
    subparsers = parser.add_subparsers(dest="command", required=True, help="子命令类型")

    # 子命令 1: sync-sap（按起止日期从 SAP HANA 抽取凭证、转换并导出 43 列 FAST 模板）
    sync_parser = subparsers.add_parser("sync-sap", help="从 SAP 同步数据并导出为 43 列越南 FAST 模板")
    sync_parser.add_argument(
        "--start-date",
        type=lambda s: arrow.get(s, "YYYY-MM-DD"),
        required=True,
        help="开始日期 (格式: YYYY-MM-DD)",
    )
    sync_parser.add_argument(
        "--end-date",
        type=lambda s: arrow.get(s, "YYYY-MM-DD"),
        required=True,
        help="结束日期 (格式: YYYY-MM-DD)",
    )
    sync_parser.add_argument("--company-code", type=str, default="2390", help="SAP 公司代码（默认 2390）")

    # 子命令 2: reconcile（按月核对 SAP 与 FAST 科目发生额，自动反查差额凭证并重导）
    reconcile_parser = subparsers.add_parser("reconcile", help="执行 SAP 与 FAST 账目月度核对")
    reconcile_parser.add_argument("--months", nargs="+", required=True, help="需要对账的月份列表 (格式: YYYYMM)")

    # 子命令 3: postfast（启动 Chrome CDP 自动登录并批量勾选过账至总账）
    subparsers.add_parser("postfast", help="批量勾选过账到越南 FAST 总账 (Post to GL)")

    # 子命令 4: upload（启动 Chrome CDP 自动登录并循环上传 output/ 目录下的 Excel 模板）
    subparsers.add_parser("upload", help="批量上传 Excel 凭证模板到越南 FAST 系统")

    args = parser.parse_args()

    # 根据解析出的子命令调用对应的核心处理器
    if args.command == "sync-sap":
        exporter = SAPExporter()
        exporter.export_by_date_range(
            args.start_date.format("YYYY-MM-DD"),
            args.end_date.format("YYYY-MM-DD"),
            company_code=args.company_code,
        )
    elif args.command == "reconcile":
        reconciler = DataReconciler()
        reconciler.reconcile_months(args.months)
    elif args.command == "postfast":
        postToGl()
    elif args.command == "upload":
        upload_to_fast()


if __name__ == "__main__":
    main()
