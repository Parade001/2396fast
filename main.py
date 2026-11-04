#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAP2FAST CLI 主入口 (main.py) —— 已与 sap2fast_full_fix.py (1104 全量版) 完整统一
支持子命令兼容模式 (sync-sap / reconcile / postfast / upload) 与标准 --mode 参数模式。
"""
import sys
import arrow
from sap2fast_full_fix import (
    SAPExporter,
    DataReconciler,
    upload_to_fast,
    postToGl,
    main as sap2fast_main,
)


def main():
    # 若使用标准 --mode / --generate-sample / --audit 参数，直接委托给 sap2fast_full_fix.main()
    if any(arg.startswith("--") or arg in ("local", "export", "all", "delete-account", "update-account", "manage-mapping") for arg in sys.argv[1:]):
        if len(sys.argv) > 1 and sys.argv[1] not in ("sync-sap", "reconcile", "postfast", "upload"):
            return sap2fast_main()

    import argparse
    parser = argparse.ArgumentParser(description="SAP2FAST CLI 执行工具 (1104 全量修复版)")
    subparsers = parser.add_subparsers(dest="command", help="可用命令列表")

    parser_sync = subparsers.add_parser("sync-sap", help="按日增量同步 SAP 数据")
    parser_sync.add_argument("--date", type=str, default="", help="同步日期 (格式: YYYYMMDD)")

    parser_recon = subparsers.add_parser("reconcile", help="执行月度对账")
    parser_recon.add_argument("--yyyymm", type=str, required=True, help="指定财务账期 (格式: YYYYMM)")

    parser_postfast = subparsers.add_parser("postfast", help="FAST 网页批量 Post 到总账")
    parser_postfast.add_argument("--headless", action="store_true", help="无头模式")

    parser_upload = subparsers.add_parser("upload", help="FAST 网页批量上传导入模板")
    parser_upload.add_argument("--dir", type=str, default="output", help="待上传目录")
    parser_upload.add_argument("--headless", action="store_true", help="无头模式")

    args = parser.parse_args()
    if args.command == "sync-sap":
        if args.date:
            try:
                arrow.get(args.date, "YYYYMMDD")
            except ValueError:
                print("[同步SAP] 日期格式错误: 格式必须为 YYYYMMDD")
                return
        SAPExporter(args.date or "").run()
    elif args.command == "reconcile":
        ym = args.yyyymm
        if len(ym) != 6 or not ym.isdigit() or not (2023 <= int(ym[:4]) <= 2055) or not (1 <= int(ym[4:6]) <= 12):
            print("[月度对账] 财务账期格式错误: 格式必须为 YYYYMM")
            return
        DataReconciler(ym).fetch_sap_to_fast()
    elif args.command == "postfast":
        postToGl(headless=args.headless)
    elif args.command == "upload":
        upload_to_fast(input_dir=args.dir, headless=args.headless)
    else:
        sap2fast_main()


if __name__ == "__main__":
    main()
