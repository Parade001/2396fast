#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: main.py
模块定位: SAP2FAST 统一入口模块（默认启动 3000 端口 Web 工作台，同时兼容全部 CLI 子命令）
================================================================================
【本文件核心作用】
1. 当无参数启动或传入 `--port` / `--host`（如容器平台启动或直接执行 `python main.py`）时，
   零延迟直接启动 `app.py` 的 Web 可视化工作台服务（默认监听 `0.0.0.0:3000`）。
2. 当传入 CLI 业务参数时，按需加载依赖并支持两种调用风格：
   - 风格一（历史运维习惯的子命令模式）：
     * `python main.py sync-sap --start-date 2024-03-01 --end-date 2024-03-31`
     * `python main.py reconcile --months 202403 202404`
     * `python main.py upload`
     * `python main.py postfast`
   - 风格二（1104 全量版 `--mode` 扩展模式）：
     * 直接委托给 `sap2fast_full_fix.main()`。
================================================================================
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

_EXT_PYDEPS = "/tmp/.pydeps"
if os.path.isdir(_EXT_PYDEPS) and _EXT_PYDEPS not in sys.path:
    sys.path.insert(0, _EXT_PYDEPS)

_LOCAL_PYDEPS = os.path.join(BASE_DIR, ".pydeps")
if os.path.isdir(_LOCAL_PYDEPS) and _LOCAL_PYDEPS not in sys.path:
    sys.path.insert(0, _LOCAL_PYDEPS)


def _should_start_web_server(argv: list) -> bool:
    """
    判断是否应启动 Web 可视化工作台：
    - 无任何命令行参数 (`python main.py`)
    - 或传入了 `--port` / `--host` / `--kill-port` / `serve` / `web`
    """
    if not argv:
        return True
    web_flags = {"--port", "--host", "--kill-port", "serve", "web"}
    if any(arg in web_flags or arg.startswith("--port=") or arg.startswith("--host=") for arg in argv):
        return True
    return False


def main():
    argv = sys.argv[1:]

    # 1. 默认或传入 --port / --host 时，毫秒级启动 Web 可视化工作台 (0.0.0.0:3000)
    if _should_start_web_server(argv):
        import argparse
        from app import start_workbench_server

        parser = argparse.ArgumentParser(description="SAP2FAST 1104 可视化 Web 工作台")
        _env_port = os.environ.get("PORT", "3000")
        _default_port = 3000 if _env_port in ("8080", "8000", "") else int(_env_port)
        parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
        parser.add_argument("--port", type=int, default=_default_port)
        parser.add_argument("--kill-port", action="store_true")
        args, _ = parser.parse_known_args(argv)
        start_workbench_server(host=args.host, preferred_port=args.port, kill_port=args.kill_port)
        return 0

    # 2. CLI 模式下按需确保运行时依赖就绪
    try:
        import sitecustomize
        sitecustomize.ensure_runtime_deps()
    except Exception:
        pass

    from sap2fast_full_fix import (
        SAPExporter,
        DataReconciler,
        upload_to_fast,
        postToGl,
        main as sap2fast_main,
    )

    # 若传入了 1104 标准 --mode / --generate-sample / --audit 等参数，直接委托给 sap2fast_full_fix.main()
    if any(
        arg.startswith("--") or arg in ("local", "export", "all", "delete-account", "update-account", "manage-mapping")
        for arg in argv
    ):
        if argv[0] not in ("sync-sap", "reconcile", "postfast", "upload"):
            return sap2fast_main()

    # 解析历史兼容的四类子命令 (sync-sap / reconcile / postfast / upload)
    import argparse
    from datetime import datetime

    def _parse_date_str(s: str) -> str:
        datetime.strptime(s.strip(), "%Y-%m-%d")
        return s.strip()

    parser = argparse.ArgumentParser(description="SAP2FAST CLI 执行工具 (1104 全量修复版)")
    subparsers = parser.add_subparsers(dest="command", required=True, help="子命令类型")

    sync_parser = subparsers.add_parser("sync-sap", help="从 SAP 同步数据并导出为 43 列越南 FAST 模板")
    sync_parser.add_argument("--start-date", type=_parse_date_str, required=True, help="开始日期 (YYYY-MM-DD)")
    sync_parser.add_argument("--end-date", type=_parse_date_str, required=True, help="结束日期 (YYYY-MM-DD)")
    sync_parser.add_argument("--company-code", type=str, default="2390", help="SAP 公司代码（默认 2390）")

    reconcile_parser = subparsers.add_parser("reconcile", help="执行 SAP 与 FAST 账目月度核对")
    reconcile_parser.add_argument("--months", nargs="+", required=True, help="需要对账的月份列表 (格式: YYYYMM)")

    subparsers.add_parser("postfast", help="批量勾选过账到越南 FAST 总账 (Post to GL)")
    subparsers.add_parser("upload", help="批量上传 Excel 凭证模板到越南 FAST 系统")

    args = parser.parse_args(argv)

    if args.command == "sync-sap":
        exporter = SAPExporter()
        exporter.export_by_date_range(args.start_date, args.end_date, company_code=args.company_code)
    elif args.command == "reconcile":
        reconciler = DataReconciler()
        reconciler.reconcile_months(args.months)
    elif args.command == "postfast":
        postToGl()
    elif args.command == "upload":
        upload_to_fast()
    return 0


if __name__ == "__main__":
    main()
