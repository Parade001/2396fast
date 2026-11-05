#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: sapfast_full_fix.py
模块定位: SAP2FAST (1104 全量闭环版) 兼容别名主入口文件
================================================================================
【本文件核心作用】
1. 作为 `sap2fast_full_fix.py` 的 100% 等价同名入口文件，方便习惯输入 `sapfast_full_fix.py`
   （少写数字 2）的财务与运维人员直接通过命令行或脚本调用全部 1104 闭环功能。
2. 完整导出 `sap2fast_full_fix.py` 中的所有类与函数符号：
   - `ProcessConfig`：转换管线全局配置（含跨月日期策略、可选成本中心列、离线去重开关等）；
   - `AccountConfig`：科目与客商 YAML 映射加载器（含 6051000001 -> 51181 外部收入硬锁）；
   - `DataProcessor`：SAP 17 列凭证 -> 越南 FAST 8 步核心转换引擎；
   - `SAPExporter`：SAP HANA 按日增量抽取、凭证号防重与 43 列 FAST 模板分卷导出器；
   - `DataReconciler`：SAP vs FAST 月度发生额对账、反查缺失凭证与错账删除器；
   - `MappingManager`：CFO 需求 6 科目/客商映射新增、修改与多对一安全锁管理器；
   - `modify_fast_database` / `update_fast_account`：FAST 库表维护工具（含 `--dry-run` 安全预览闸）；
   - `upload_to_fast` / `postToGl`：CFO 需求 7 网页全自动登录、循环上传与批量 Post 过账编排器。

【命令行调用示例】
- 离线样表生成 + 43 列模板转换 + 借贷平账审计：
  `python sapfast_full_fix.py --generate-sample --audit`
- 库表维护 Dry-Run 安全预览：
  `python sapfast_full_fix.py --mode delete-account --target-account 6428 --dry-run`
================================================================================
"""

import os
import sys

# 自动挂载工作区 .pydeps 本地依赖目录，确保离线或容器重启后可直接导入第三方库
_PYDEPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pydeps")
if os.path.isdir(_PYDEPS_DIR) and _PYDEPS_DIR not in sys.path:
    sys.path.insert(0, _PYDEPS_DIR)

# 全量导入并暴露 sap2fast_full_fix.py 中的所有公开类、函数与常量
from sap2fast_full_fix import *  # noqa: F401,F403
from sap2fast_full_fix import main

if __name__ == "__main__":
    # 当直接以 `python sapfast_full_fix.py ...` 启动时，转发至主程序 CLI 入口
    main()
