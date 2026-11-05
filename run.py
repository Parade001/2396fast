#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: run.py
模块定位: SAP2FAST (1104 全量闭环版) 离线快速演示与月度对账快捷脚本
================================================================================
【本文件核心作用】
1. 提供零配置的一键快速运行入口（无需输入任何命令行参数）：
   - 自动在 `samples/sample_raw_sap.xlsx` 生成包含典型业务场景的 17 列原始 SAP 凭证样表
     （涵盖外部销售收入 `6051000001`、应付账款无客商兜底 `331111->A0001`、2借2贷复合凭证拆分、
     USD 外币分录及跨期同名凭证号防重测试）；
   - 调用 `reprocess_local_files()` 完成 8 步准则转换并导出 43 列标准越南 FAST 导入模板；
   - 调用 `audit_local_files()` 对导出的 43 列模板进行逆向解析与本位币 (VND)/外币 (USD) 借贷平账审计。
2. 保留 `run_reconcile(yyyymm_list)` 辅助函数，方便运维人员在内网环境下快速触发指定月份列表的
   SAP vs FAST 数据库发生额对账。

【修复说明】
- 老版本 `run.py` 直接在模块顶层连接内网数据库且引用了不存在的方法，在离线环境一运行即报错崩溃；
- 1104 版将其重构为默认执行本地样表生成 + 43 列模板转换 + 离线平账审计的安全快捷入口。
================================================================================
"""

import os
import sys

# 自动挂载工作区 .pydeps 本地依赖目录
_PYDEPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pydeps")
if os.path.isdir(_PYDEPS_DIR) and _PYDEPS_DIR not in sys.path:
    sys.path.insert(0, _PYDEPS_DIR)

from sap2fast_full_fix import (
    ProcessConfig,
    DataProcessor,
    DataReconciler,
    generate_raw_sap_sample_file,
    reprocess_local_files,
    audit_local_files,
    setup_logger,
)


def main():
    """
    默认执行流程：
    1. 生成 17 列原始 SAP 测试样表 (`samples/sample_raw_sap.xlsx`)
    2. 转换为 43 列越南 FAST 导入模板 (`output/fixed_sample_raw_sap.xlsx_1.result.xlsx`)
    3. 对输出文件执行 VND 本位币与 USD 外币借贷平衡体检，输出 Excel 审计报告
    """
    logger = setup_logger(__name__)
    # 步骤 1：生成 17 列标准 SAP 测试样表
    sample_path = generate_raw_sap_sample_file()
    # 步骤 2：执行 8 步转换并导出 43 列 FAST 导入模板
    exported = reprocess_local_files(input_pattern=sample_path, output_dir="output")
    # 步骤 3：对导出的 43 列模板执行逆向解析与平账审计
    report = audit_local_files(input_pattern="output/fixed_sample_raw_sap*.xlsx", output_dir="output")
    logger.info(f"离线样表转换完成: {exported} | 审计报告: {report}")


def run_reconcile(yyyymm_list=None):
    """
    内网数据库月度对账辅助函数：
    传入形如 `['202403', '202404']` 的财务期间列表，对比 SAP HANA 与 FAST MSSQL 科目发生额差异。
    """
    target_months = yyyymm_list or ["202403"]
    reconciler = DataReconciler()
    reconciler.reconcile_months(target_months)


if __name__ == "__main__":
    main()
