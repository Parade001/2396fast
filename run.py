# -*- coding: utf-8 -*-
"""
SAP2FAST 快速运行脚本 (run.py) —— 1104 全量修复版
默认生成 17 列原始 SAP 测试样表并完成 8 步转换 + 43 列 FAST 模板导出 + 离线平账审计。
"""
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
    logger = setup_logger(__name__)
    sample_path = generate_raw_sap_sample_file()
    exported = reprocess_local_files(input_pattern=sample_path, output_dir="output")
    report = audit_local_files(input_pattern="output/fixed_sample_raw_sap*.xlsx", output_dir="output")
    logger.info(f"离线样表转换完成: {exported} | 审计报告: {report}")


def run_reconcile(yyyymm_list=None):
    logger = setup_logger(__name__)
    for ym in (yyyymm_list or ["202408"]):
        d = DataReconciler(ym)
        d.fetch_sap_to_fast()
        logger.info(f"会计期间 {ym} 导出完成")


if __name__ == "__main__":
    main()
