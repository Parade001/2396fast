#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: app.py
模块定位: SAP2FAST (1104 全量闭环版) 可视化 Web 运维、转换与平账审计工作台服务端
================================================================================
【本文件核心作用】
1. 提供基于浏览器访问的 SAP2FAST 可视化操作界面（默认监听 0.0.0.0:3000），无需记忆复杂
   命令行参数即可完成：
   - 模块 1：SAP 17 列原始凭证 -> 越南 FAST 43 列标准导入模板转换与在线逐行预览；
   - 模块 2：离线本位币 (VND) 与外币 (USD) 借贷平账体检审计（支持 17 列原表与 43 列模板）；
   - 模块 3：CFO 需求 6 科目/客商映射管理器（6.1 防重添加、6.21 多表同步改客商、6.22 多对一安全锁）；
   - 模块 4：FAST 库表破坏性维护操作 Dry-Run SQL 安全预览闸（删科目、改科目、删错账）与需求 7 说明；
   - 模块 5：1104 版本验收报告、功能对比评估报告、README 与 CFO 会议纪要在线审阅；
   - 顶部工具栏：一键运行 1104 冒烟验收测试（tests/_smoke_test.py）与生成 17 列测试样表。

【1104 本次关键修复说明】
1. 解决 Windows 本地启动报错 `[WinError 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次`：
   - 原因：Windows 本地 3000 端口已被其他进程（如上一次未关闭的 Python/Node 进程）占用，或容器启动器
     因重型库 (`pandas`/`openpyxl`) 顶层导入耗时超过 1.5 秒而抢先占用了 3000 端口；
   - 修复方案 A（零延迟端口绑定）：改用 Python 标准库 `http.server.ThreadingHTTPServer` 先在 20 毫秒内
     完成端口绑定，随后在后台守护线程及接口调用时按需惰性加载 `pandas`/`openpyxl`/`sap2fast_full_fix`；
   - 修复方案 B（Windows 端口占用自动检测与自动顺延切换）：启动前通过 `_check_and_resolve_port()` 检测
     目标端口是否被占用；若在本地环境发现 3000 端口被占用，自动调用 `netstat -ano` 识别占用进程 PID，
     支持 `--kill-port` 自动清理旧进程，或自动顺延绑定至 `3001 ~ 3020` 可用端口并打印访问地址，彻底杜绝
     `WinError 10048` 崩溃。
2. 自动挂载本地持久化依赖目录 `.pydeps`，确保无论在离线容器还是本地环境均能稳定加载依赖。

【启动方式】
- 默认启动（优先 3000 端口，被占用时自动顺延至 3001+）：
  `python app.py`
- 指定自定义端口启动：
  `python app.py --port 8080`
- Windows 下若 3000 端口被旧进程占用，自动终止占用进程并绑定 3000：
  `python app.py --port 3000 --kill-port`
================================================================================
"""

import os
import sys
import glob
import json
import time
import socket
import argparse
import mimetypes
import threading
import subprocess
import traceback
import http.server
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Optional, List, Dict, Any, Tuple

# ------------------------------------------------------------------------------
# 1. 自动挂载项目外部的 /tmp/.pydeps 临时依赖目录（保持 Git 工作区 100% 纯净，不含第三方包）
# ------------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
_EXT_PYDEPS = "/tmp/.pydeps"
if os.path.isdir(_EXT_PYDEPS) and _EXT_PYDEPS not in sys.path:
    sys.path.insert(0, _EXT_PYDEPS)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------------------
# 2. 惰性加载核心业务模块（避免在绑定端口前导入重型库导致启动超时或端口抢占）
# ------------------------------------------------------------------------------
_CORE_LOCK = threading.Lock()
_s2f_module = None
_pd_module = None
_openpyxl_module = None


def _get_core_modules():
    """
    按需惰性导入重型计算模块（openpyxl, pandas, sap2fast_full_fix）。
    这样 HTTP 服务可以在 20ms 内瞬间完成端口绑定，随后在后台或首次请求时加载业务模块。
    """
    global _s2f_module, _pd_module, _openpyxl_module
    if _s2f_module is not None and _pd_module is not None and _openpyxl_module is not None:
        return _s2f_module, _pd_module, _openpyxl_module
    with _CORE_LOCK:
        try:
            import sitecustomize
            sitecustomize.ensure_runtime_deps()
        except Exception:
            pass
        if _openpyxl_module is None:
            import openpyxl as _ox
            _openpyxl_module = _ox
        if _pd_module is None:
            import pandas as _pd
            _pd_module = _pd
        if _s2f_module is None:
            import sap2fast_full_fix as _s2f
            _s2f_module = _s2f
    return _s2f_module, _pd_module, _openpyxl_module


# ------------------------------------------------------------------------------
# 3. 辅助业务函数：读取生成的 43 列 FAST 模板 Excel 并转换为前端表格预览结构
# ------------------------------------------------------------------------------
def _preview_fast_template(xlsx_path: str, max_rows: int = 50) -> List[Dict[str, Any]]:
    """
    读取导出的 43 列无表头 FAST 导入模板文件（第 1~5 行留空，第 6 行起为 CTY 数据），
    提取关键业务列（单位、凭证号、过账日期、币种、汇率、FAST科目、客商、借贷原币/本位币、摘要、组号）
    供前端工作台表格直观核对。
    """
    _, _, openpyxl = _get_core_modules()
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    rows_out: List[Dict[str, Any]] = []
    # 越南 FAST 导入模板从第 6 行开始写入正式分录数据
    for r_idx in range(6, min(ws.max_row + 1, 6 + max_rows)):
        cty = ws.cell(row=r_idx, column=1).value
        if cty is None and ws.cell(row=r_idx, column=2).value is None:
            continue
        post_date = ws.cell(row=r_idx, column=3).value or ""
        ex_rate = ws.cell(row=r_idx, column=5).value
        dr_orig = ws.cell(row=r_idx, column=11).value or 0
        cr_orig = ws.cell(row=r_idx, column=12).value or 0
        dr_vnd = ws.cell(row=r_idx, column=13).value or 0
        cr_vnd = ws.cell(row=r_idx, column=14).value or 0
        rows_out.append({
            "excel_row": r_idx,                                   # Excel 物理行号（>=6）
            "col_1_cty": cty or "",                               # A列(1): 核算单位标志 CTY
            "col_2_voucher_no": ws.cell(row=r_idx, column=2).value or "",   # B列(2): 凭证号（含 _A/_B 后缀）
            "col_3_post_date": post_date,                         # C列(3): 过账日期 dd/mm/yyyy
            "col_3_posting_date": post_date,                      # 兼容前端字段名 col_3_posting_date
            "col_4_currency": ws.cell(row=r_idx, column=4).value or "",     # D列(4): 币种 (VND / USD)
            "col_5_exchange_rate": ex_rate,                       # E列(5): 汇率
            "col_5_ex_rate": ex_rate,                             # 兼容前端字段名 col_5_ex_rate
            "col_7_fast_account": ws.cell(row=r_idx, column=7).value or "", # G列(7): 越南 FAST 科目代码
            "col_9_partner": ws.cell(row=r_idx, column=9).value or "",      # I列(9): 客商编码 (A0001~A0007等)
            "col_11_debit_orig": dr_orig,                         # K列(11): 借方原币金额 (S)
            "col_11_dr_orig": dr_orig,                            # 兼容前端字段名 col_11_dr_orig
            "col_12_credit_orig": cr_orig,                        # L列(12): 贷方原币金额 (H)
            "col_12_cr_orig": cr_orig,                            # 兼容前端字段名 col_12_cr_orig
            "col_13_debit_vnd": dr_vnd,                           # M列(13): 借方本位币金额 (S)
            "col_13_dr_vnd": dr_vnd,                              # 兼容前端字段名 col_13_dr_vnd
            "col_14_credit_vnd": cr_vnd,                          # N列(14): 贷方本位币金额 (H)
            "col_14_cr_vnd": cr_vnd,                              # 兼容前端字段名 col_14_cr_vnd
            "col_15_summary": ws.cell(row=r_idx, column=15).value or "",    # O列(15): 分录摘要文本
            "col_43_group_id": ws.cell(row=r_idx, column=43).value if ws.cell(row=r_idx, column=43).value is not None else "",  # AQ列(43): 拆分组号
        })
    wb.close()
    return rows_out


# ------------------------------------------------------------------------------
# 4. API 路由业务处理器（对应前端工作台的 5 大功能模块）
# ------------------------------------------------------------------------------
def handle_api_status() -> Dict[str, Any]:
    """
    [GET /api/overview & GET /api/status]
    获取系统整体健康状态、account.yaml 映射统计、6051000001->51181 外部收入锁定状态、
    本地 input/ 与 output/ 凭证文件列表、默认 43 列模板预览行与平账审计摘要。
    """
    s2f, pd, _ = _get_core_modules()
    cfg_path = BASE_DIR / "config" / "account.yaml"
    acc_cfg = s2f.AccountConfig.load(str(cfg_path), force_reload=True)
    account_map = acc_cfg.get("account", {}) or {}
    fkber_map = acc_cfg.get("fkber", {}) or {}
    vendor_map = acc_cfg.get("vendor", {}) or {}
    customer_map = acc_cfg.get("customer", {}) or {}

    account_count = len(account_map)
    fkber_count = sum(len(v) for v in fkber_map.values() if isinstance(v, dict))
    vendor_count = len(vendor_map)
    customer_count = len(customer_map)

    # 校验 CFO 1103 拍板决策：6051000001 在普通科目层必须锁定为外部收入 51181
    locked_6051 = str(account_map.get("6051000001", ""))
    fkber_2000_6051 = str((fkber_map.get("2000", {}) or {}).get("6051000001", ""))

    # 确保至少有一份默认样表转换结果供首页直接展示
    out_xlsx_list = sorted(glob.glob(str(BASE_DIR / "output" / "*.xlsx")))
    if not out_xlsx_list:
        sample_p = s2f.generate_raw_sap_sample_file()
        s2f.reprocess_local_files(
            input_pattern=sample_p,
            output_dir=str(BASE_DIR / "output"),
        )
        out_xlsx_list = sorted(glob.glob(str(BASE_DIR / "output" / "*.xlsx")))

    template_preview = _preview_fast_template(out_xlsx_list[0]) if out_xlsx_list else []

    # 读取或生成平账审计记录
    audit_rows: List[Dict[str, Any]] = []
    try:
        audit_res = handle_api_audit({"pattern": "output/*.xlsx"})
        audit_rows = audit_res.get("rows", [])
    except Exception:
        audit_rows = []

    input_files = sorted([os.path.basename(p) for p in glob.glob(str(BASE_DIR / "input" / "*.xlsx"))])
    output_files = sorted([os.path.basename(p) for p in out_xlsx_list])
    sample_files = sorted([os.path.basename(p) for p in glob.glob(str(BASE_DIR / "samples" / "*.xlsx"))])

    mapping_audit_log = BASE_DIR / "logs" / "mapping_change_audit.log"
    db_audit_log = BASE_DIR / "logs" / "db_maintenance_audit.log"

    return {
        "ok": True,
        "version": "1104-FULL-CLOSED-LOOP",
        "account_6051000001_target": locked_6051,
        "account_mapping_count": account_count,
        "fkber_mapping_count": fkber_count,
        "input_file_count": len(input_files),
        "output_file_count": len(output_files),
        "input_files_count": len(input_files),
        "output_files_count": len(output_files),
        "template_preview": template_preview,
        "audit_rows": audit_rows,
        "mapping_stats": {
            "account_count": account_count,
            "fkber_count": fkber_count,
            "vendor_count": vendor_count,
            "customer_count": customer_count,
            "account_6051000001": locked_6051,
            "fkber_2000_6051000001": fkber_2000_6051,
            "is_6051000001_locked_51181": (locked_6051 == "51181"),
        },
        "files": {
            "input_count": len(input_files),
            "input_files": input_files,
            "output_count": len(output_files),
            "output_files": output_files,
            "sample_files": sample_files,
        },
        "audit_logs": {
            "mapping_audit": mapping_audit_log.read_text(encoding="utf-8").splitlines()[-20:] if mapping_audit_log.exists() else [],
            "db_audit": db_audit_log.read_text(encoding="utf-8").splitlines()[-20:] if db_audit_log.exists() else [],
        },
    }


def handle_api_generate_sample() -> Dict[str, Any]:
    """
    [POST /api/generate-sample]
    一键生成 17 列原始 SAP 测试样表（samples/sample_raw_sap.xlsx），并立即执行
    DataProcessor 8 步转换与 43 列 FAST 模板导出，返回前 50 行模板预览数据。
    """
    s2f, _, _ = _get_core_modules()
    sample_path = s2f.generate_raw_sap_sample_file()
    exported = s2f.reprocess_local_files(
        input_pattern=sample_path,
        output_dir=str(BASE_DIR / "output"),
    )
    preview = _preview_fast_template(exported[0]) if exported else []
    return {
        "ok": True,
        "sample_path": os.path.relpath(sample_path, BASE_DIR),
        "exported_files": [os.path.relpath(p, BASE_DIR) for p in exported],
        "preview_rows": preview,
    }


def handle_api_convert(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    [POST /api/sample-convert & POST /api/convert]
    执行本地文件转换（支持 17 列原始 SAP 凭证或 43 列历史 FAST 模板逆向解析），
    支持选择跨月补记日期策略（period_end / cpudt）、成本中心输出列（6 / 8 / 10）与离线跨期凭证 _A/_B 防重。
    """
    s2f, _, _ = _get_core_modules()
    input_pattern = payload.get("pattern") or payload.get("input_pattern") or "samples/sample_raw_sap.xlsx"
    cross_month_policy = payload.get("cross_month_date_mode") or payload.get("cross_month_policy") or "period_end"
    cost_center_col = payload.get("cost_center_col")
    if cost_center_col is not None and str(cost_center_col).strip() != "":
        cost_center_col = int(cost_center_col)
    else:
        cost_center_col = None

    if "offline_voucher_dedup" in payload:
        offline_dedup = bool(payload.get("offline_voucher_dedup"))
    else:
        offline_dedup = bool(payload.get("offline_dedup", True))

    target_pattern = str(BASE_DIR / input_pattern) if not os.path.isabs(input_pattern) else input_pattern
    if not glob.glob(target_pattern):
        s2f.generate_raw_sap_sample_file()

    cfg = s2f.ProcessConfig(
        cross_month_date_mode=cross_month_policy,
        cost_center_col=cost_center_col,
        offline_voucher_dedup=offline_dedup,
    )
    exported = s2f.reprocess_local_files(
        input_pattern=target_pattern,
        output_dir=str(BASE_DIR / "output"),
        process_cfg=cfg,
    )
    preview = _preview_fast_template(exported[0]) if exported else []
    return {
        "ok": True,
        "exported_files": [os.path.relpath(p, BASE_DIR) for p in exported],
        "preview_rows": preview,
    }


def handle_api_audit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    [POST /api/audit]
    扫描指定目录下的全部 Excel 凭证文件（支持 17 列原始 SAP 与 43 列无表头 FAST 结果模板），
    逐文件核验本位币 (VND) 与外币 (USD) 借贷是否平衡，并生成汇总体检报告。
    """
    s2f, pd, _ = _get_core_modules()
    pattern = payload.get("pattern", "output/*.xlsx")
    full_pattern = str(BASE_DIR / pattern) if not os.path.isabs(pattern) else pattern
    files = sorted(glob.glob(full_pattern))
    if not files:
        sample_p = s2f.generate_raw_sap_sample_file()
        s2f.reprocess_local_files(
            input_pattern=sample_p,
            output_dir=str(BASE_DIR / "output"),
        )
        files = sorted(glob.glob(str(BASE_DIR / "output" / "*.xlsx")))

    report_path = s2f.audit_local_files(
        input_pattern=full_pattern,
        output_dir=str(BASE_DIR / "output" / "audit_reports"),
    )
    if report_path and os.path.exists(report_path):
        df = pd.read_csv(report_path)
        records = df.fillna("").to_dict(orient="records")
    else:
        records = []
    return {
        "ok": True,
        "report_path": os.path.relpath(report_path, BASE_DIR) if report_path else "",
        "records": records,
        "rows": records,
    }


def handle_api_mapping(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    [POST /api/mappings/action & POST /api/mapping]
    执行 CFO 需求 6 科目与客商映射管理操作：
    - 6.1 add-account / add-partner：新增映射并校验一对一/多对一防重；
    - 6.21 update-partner：修改客商映射并按可配置范围 (d11/ct00/m11/dmkh) 生成同步 SQL；
    - 6.22 update-account：修改科目映射并触发多对一安全锁拦截（需 force_multi 显式放行）。
    """
    s2f, _, _ = _get_core_modules()
    action = payload.get("action", "")
    sap_code = str(payload.get("sap_code", "")).strip()
    fast_code = str(payload.get("fast_code", "")).strip()
    fkber = payload.get("fkber")
    if fkber in ("", "-", None):
        fkber = None
    partner_type = payload.get("partner_type", "vendor")
    partner_sync_scope = payload.get("partner_sync_scope", "d11,ct00")
    force_multi = bool(payload.get("force_multi", False))
    dry_run = bool(payload.get("dry_run", True))

    yaml_path = str(BASE_DIR / "config" / "account.yaml")
    scope_tuple = tuple(t.strip() for t in partner_sync_scope.split(",") if t.strip())
    mgr = s2f.MappingManager(yaml_path=yaml_path)

    if action == "check-multi":
        acc_node = mgr.cfg.get("account") or {}
        related = [str(k) for k, v in acc_node.items() if str(v).strip() == fast_code]
        for fk, sub_dict in (mgr.cfg.get("fkber") or {}).items():
            if isinstance(sub_dict, dict):
                for k, v in sub_dict.items():
                    if str(v).strip() == fast_code:
                        related.append(f"{fk}:{k}")
        is_one_to_one = len(set(related)) <= 1
        return {
            "ok": True,
            "fast_code": fast_code,
            "is_one_to_one": is_one_to_one,
            "related_sap_accounts": related,
            "message": f"FAST 科目 {fast_code} 关联了 {len(related)} 个 SAP 科目: {related}",
        }
    elif action == "add-account":
        ok = mgr.add_account_mapping(
            sap_account=sap_code,
            fast_account=fast_code,
            fkber=fkber,
            keep_log=not dry_run,
        )
        return {
            "ok": ok,
            "message": "新增科目映射成功" if ok else "新增科目映射被风控锁拦截（原科目已存在）",
        }
    elif action == "add-partner":
        ok = mgr.add_partner_mapping(
            sap_partner=sap_code,
            fast_partner=fast_code,
            partner_type=partner_type,
            keep_log=not dry_run,
        )
        return {
            "ok": ok,
            "message": "新增客商映射成功" if ok else "新增客商映射被拦截（SAP 客商或 FAST 客商代码已存在，禁止多对一）",
        }
    elif action == "update-partner":
        ok = mgr.update_partner_mapping(
            sap_partner=sap_code,
            new_fast_partner=fast_code,
            partner_type=partner_type,
            sync_fast_db=False,
            sync_tables=scope_tuple,
            dry_run=dry_run,
            keep_log=True,
        )
        return {
            "ok": ok,
            "message": f"客商映射修改 {'(Dry-Run 预览)' if dry_run else ''} 成功，同步表范围={scope_tuple}" if ok else "客商映射修改被拦截（原 SAP 客商不存在或新 FAST 客商冲突）",
        }
    elif action == "update-account":
        ok = mgr.update_account_mapping(
            sap_account=sap_code,
            new_fast_account=fast_code,
            fkber=fkber,
            force_multi=force_multi,
            sync_fast_db=False,
            dry_run=dry_run,
            keep_log=True,
        )
        return {
            "ok": ok,
            "message": f"科目映射修改 {'(Dry-Run 预览)' if dry_run else ''} 成功" if ok else "[6.22 多对一安全锁拦截] 原科目关联的 FAST 科目存在多对一关系，原则上禁止单独修改（需勾选 force-multi 强制授权）",
        }
    return {"ok": False, "message": f"未知操作类型: {action}"}


def handle_api_mapping_search(q: str) -> Dict[str, Any]:
    """
    [GET /api/mappings?search=... & GET /api/mapping/search?q=...]
    按关键字检索 config/account.yaml 中的普通科目映射与功能范围 (fkber) 科目映射。
    """
    s2f, _, _ = _get_core_modules()
    cfg_path = BASE_DIR / "config" / "account.yaml"
    acc_cfg = s2f.AccountConfig.load(str(cfg_path))
    account_map = acc_cfg.get("account", {}) or {}
    fkber_map = acc_cfg.get("fkber", {}) or {}

    q_clean = q.strip().lower()
    results = []

    for sap_acc, fast_acc in account_map.items():
        s_str, f_str = str(sap_acc), str(fast_acc)
        if not q_clean or q_clean in s_str.lower() or q_clean in f_str.lower():
            item = {
                "scope": "account (普通科目高优层)",
                "layer": "account (普通科目高优层)",
                "fkber": "-",
                "sap_code": s_str,
                "fast_code": f_str,
            }
            results.append(item)

    for fk, sub in fkber_map.items():
        if not isinstance(sub, dict):
            continue
        fk_str = str(fk)
        for sap_acc, fast_acc in sub.items():
            s_str, f_str = str(sap_acc), str(fast_acc)
            if not q_clean or q_clean in s_str.lower() or q_clean in f_str.lower() or q_clean in fk_str.lower():
                item = {
                    "scope": f"fkber ({fk_str})",
                    "layer": f"fkber ({fk_str})",
                    "fkber": fk_str,
                    "sap_code": s_str,
                    "fast_code": f_str,
                }
                results.append(item)

    mapping_audit_log = BASE_DIR / "logs" / "mapping_change_audit.log"
    audit_lines = []
    if mapping_audit_log.exists():
        for line in mapping_audit_log.read_text(encoding="utf-8").splitlines()[-20:]:
            try:
                audit_lines.append(json.loads(line))
            except Exception:
                audit_lines.append(line)

    return {
        "ok": True,
        "total": len(results),
        "items": results[:100],
        "rows": results[:100],
        "audit_logs": audit_lines,
    }


def handle_api_db_dry_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    [POST /api/db-maintenance/preview & POST /api/db-dry-run]
    执行破坏性库表维护操作（modify_fast_database 按科目删错账、update_fast_account 批量改科目、
    remove_fast_errors 删期间错账凭证）的 Dry-Run SQL 安全预览，不连接真实数据库即可审查全部 SQL。
    """
    s2f, _, _ = _get_core_modules()
    op_type = payload.get("op_type", "delete-account")
    target_account = str(payload.get("fast_code") or payload.get("target_account") or "6428").strip()
    old_account = str(payload.get("old_fast_code") or payload.get("old_account") or "131111").strip()
    new_account = str(payload.get("new_fast_code") or payload.get("new_account") or "131112").strip()
    yyyymm = str(payload.get("fiscalym") or payload.get("yyyymm") or "202403").strip()
    voucher_nos_str = str(payload.get("vouchers") or payload.get("voucher_nos") or "1000000001,1000000002").strip()

    if op_type == "delete-account":
        res = s2f.modify_fast_database(
            account_no=target_account,
            dry_run=True,
            confirm=True,
        )
        sql_list = res.get("sql_preview", [])[:20]
        return {
            "ok": True,
            "op_type": op_type,
            "sql_count": res["sql_count"],
            "preview_sqls": sql_list,
            "preview": {
                "sql_count": res["sql_count"],
                "sql_preview": sql_list,
            },
        }
    elif op_type == "update-account":
        res = s2f.update_fast_account(
            old_account=old_account,
            new_account=new_account,
            dry_run=True,
            confirm=True,
        )
        sql_list = res.get("sql_preview", [])[:20]
        return {
            "ok": True,
            "op_type": op_type,
            "sql_count": res["sql_count"],
            "preview_sqls": sql_list,
            "preview": {
                "sql_count": res["sql_count"],
                "sql_preview": sql_list,
            },
        }
    elif op_type in ("remove-errors", "remove-fast-errors"):
        v_set = {v.strip() for v in voucher_nos_str.split(",") if v.strip()}
        year = int(yyyymm[:4]) if len(yyyymm) >= 6 and yyyymm[:4].isdigit() else 2024
        period = int(yyyymm[4:6]) if len(yyyymm) >= 6 and yyyymm[4:6].isdigit() else 3
        rec = s2f.DataReconciler(yyyymm=yyyymm)
        rec.remove_fast_errors(gjahr=year, monat=period, so_ct=v_set, dry_run=True, confirm=True)
        # 构造与 remove_fast_errors 一致的级联删除预览 SQL
        ym_fast = f"{year}{(period + 3 if period <= 9 else period - 9):02d}"
        in_str = ",".join(f"'{v}'" for v in sorted(v_set))
        preview_sqls = [
            f"update m11${ym_fast} set status = 1 where so_ct IN ({in_str})",
            f"delete from r00${ym_fast} where so_ct IN ({in_str})",
            f"delete from ct00 where so_ct IN ({in_str})",
            f"delete from c11$000000 where stt_rec in (select stt_rec from m11${ym_fast} where so_ct IN ({in_str}))",
            f"delete from i11${ym_fast} where stt_rec in (select stt_rec from m11${ym_fast} where so_ct IN ({in_str}))",
            f"delete from d11${ym_fast} where so_ct IN ({in_str})",
            f"delete from m11${ym_fast} where so_ct IN ({in_str})",
        ]
        return {
            "ok": True,
            "op_type": op_type,
            "sql_count": len(preview_sqls),
            "preview_sqls": preview_sqls,
            "preview": {
                "sql_count": len(preview_sqls),
                "sql_preview": preview_sqls,
            },
        }
    return {"ok": False, "message": f"未知维护操作类型: {op_type}"}


import hashlib


def _mask_pwd(pwd: str) -> str:
    if not pwd:
        return ""
    if len(pwd) <= 2:
        return "*" * len(pwd)
    return pwd[0] + ("•" * max(4, len(pwd) - 2)) + pwd[-1]


def _cred_fingerprint(username: str, password: str) -> str:
    u = str(username or "").strip()
    p = str(password or "")
    if not u and not p:
        return ""
    return hashlib.sha256(f"{u}:{p}".encode("utf-8")).hexdigest()[:12]


def handle_api_fast_credentials_get() -> Dict[str, Any]:
    """
    [GET /api/fast-credentials]
    读取并检验 config/fast_credentials.yaml、.env 与 config/account.yaml 的多级凭据优先级、
    YAML 语法合法性、密码掩码状态及最终生效配置（注：此处仅反映本地文件读取状态，
    账号密码是否真实有效、是否过期或 10.7.3.230 是否可达，必须由 /api/fast-login-verify 真实访问目标 URL 判定）。
    """
    from utils.login_handler import load_fast_credentials, _parse_yaml_with_fallback
    cred_path = BASE_DIR / "config" / "fast_credentials.yaml"
    raw_yaml = cred_path.read_text(encoding="utf-8") if cred_path.exists() else ""
    yaml_valid = True
    yaml_err = ""
    cred_dict: Dict[str, Any] = {}
    try:
        if raw_yaml:
            cred_dict = _parse_yaml_with_fallback(raw_yaml) or {}
    except Exception as e:
        yaml_valid = False
        yaml_err = str(e)

    eff = load_fast_credentials(str(BASE_DIR / "config"))
    fa = cred_dict.get("fast_auth") or {}
    cd = cred_dict.get("chrome_cdp") or {}
    sel = cred_dict.get("selectors") or {}

    yaml_user = str(fa.get("username") or "").strip()
    yaml_pass = str(fa.get("password") or "")
    env_user = os.getenv("FAST_USER", "").strip()
    env_pass = os.getenv("FAST_PASSWORD", "")

    active_source = (
        ".env (环境变量高优层)" if (env_user and env_pass)
        else "config/fast_credentials.yaml (方案A独立凭据文件)" if (yaml_user and yaml_pass)
        else "未配置完整账密 (待填写)"
    )

    eff_user = str(eff.get("username") or "").strip()
    eff_pass = str(eff.get("password") or "")
    eff_unit = str(eff.get("unit") or "CTY")
    eff_lang = str(eff.get("language") or "en")
    eff_fp = _cred_fingerprint(eff_user, eff_pass)
    yaml_fp = _cred_fingerprint(yaml_user, yaml_pass)
    is_ready = bool(yaml_valid and eff_user and eff_pass)

    return {
        "ok": True,
        "file_exists": cred_path.exists(),
        "credential_file": "config/fast_credentials.yaml",
        "yaml_syntax_valid": yaml_valid,
        "yaml_syntax_error": yaml_err,
        "is_ready_for_auto_login": is_ready,
        "active_source": active_source,
        "effective": {
            "login_url": eff.get("login_url", "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"),
            "username": eff_user,
            "has_password": bool(eff_pass),
            "password_length": len(eff_pass),
            "masked_password": _mask_pwd(eff_pass),
            "fingerprint": eff_fp,
            "unit": eff_unit,
            "language": eff_lang,
        },
        "priority_chain": [
            {
                "rank": 1,
                "source": "命令行参数 (--fast-user / --fast-password)",
                "status": "运行时最高优先级 (按需覆盖)",
                "username": "(CLI 运行时注入)",
                "has_password": False,
            },
            {
                "rank": 2,
                "source": ".env 环境变量 (FAST_USER / FAST_PASSWORD)",
                "status": "已配置并生效 (待连接目标URL实测)" if (env_user and env_pass) else "未配置 (跳过)",
                "username": env_user or "-",
                "has_password": bool(env_pass),
                "fingerprint": _cred_fingerprint(env_user, env_pass),
            },
            {
                "rank": 3,
                "source": "config/fast_credentials.yaml (方案 A 推荐)",
                "status": f"已填写账密 (掩码 {_mask_pwd(yaml_pass)} · 需点击测试真实访问目标URL校验)" if (yaml_user and yaml_pass) else "未填写完整账号或密码",
                "username": yaml_user or "(空)",
                "has_password": bool(yaml_pass),
                "password_length": len(yaml_pass),
                "masked_password": _mask_pwd(yaml_pass),
                "fingerprint": yaml_fp,
            },
            {
                "rank": 4,
                "source": "config/account.yaml (chrome 兜底节点)",
                "status": "默认空值兜底",
                "username": "(空)",
                "has_password": False,
            },
        ],
        "config_data": {
            "fast_auth": {
                "login_url": str(fa.get("login_url") or "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"),
                "username": yaml_user,
                "password": yaml_pass,
                "unit": str(fa.get("unit") or "CTY"),
                "language": str(fa.get("language") or "en"),
            },
            "chrome_cdp": {
                "chrome_path": str(cd.get("chrome_path") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                "remote_debugging_port": int(cd.get("remote_debugging_port") or 9222),
                "max_retries": int(cd.get("max_retries") or 3),
                "close_browser_on_exit": bool(cd.get("close_browser_on_exit", True)),
            },
            "selectors": sel,
        },
        "raw_yaml": raw_yaml,
        "cli_verify_command": 'python -c "from utils.login_handler import verify_fast_login_live; import json; print(json.dumps(verify_fast_login_live(), ensure_ascii=False, indent=2))"',
    }


def handle_api_fast_credentials_save(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    [POST /api/fast-credentials]
    更新并保存 config/fast_credentials.yaml（不设本地静态假锁，保存后由前端或用户直接发起对目标 URL 10.7.3.230 的真实浏览器登录测试）。
    """
    status_before = handle_api_fast_credentials_get()
    cur = status_before["config_data"]
    fa = cur["fast_auth"]
    cd = cur["chrome_cdp"]
    sel = cur.get("selectors") or {}

    login_url = str(payload.get("login_url", fa["login_url"])).strip() or "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"
    username = str(payload.get("username", fa["username"])).strip()
    password = str(payload.get("password", fa["password"]))
    unit = str(payload.get("unit", fa["unit"])).strip() or "CTY"
    language = str(payload.get("language", fa["language"])).strip() or "en"
    chrome_path = str(payload.get("chrome_path", cd["chrome_path"])).strip()
    cdp_port = int(payload.get("remote_debugging_port", cd["remote_debugging_port"]) or 9222)

    yaml_text = f"""# ==============================================================================
# 越南 FAST 财务系统 (FAW_TIMBERLAND) 登录凭据与网页自动化配置文件
# 文件路径: config/fast_credentials.yaml
# ==============================================================================
# 【给财务与运维人员的填写说明】
# 1. 您只需在下方 `fast_auth` 区域的双引号 "" 内填写您的 FAST 用户名和密码即可；
# 2. 优先级顺序（高 -> 低）：
#    命令行参数 (--fast-user) > .env 环境变量 (FAST_USER) > 本文件 (config/fast_credentials.yaml) > config/account.yaml
# 3. 安全建议：若项目使用 Git 多人协作，建议将真实密码填写在根目录 `.env` 或本文件中，
#    并避免将含有真实生产密码的文件推送到公开仓库。
# ==============================================================================

fast_auth:
  # 越南 FAST 登录页面地址（内网默认地址）
  login_url: {json.dumps(login_url, ensure_ascii=False)}

  # ★ 请在此处填写您的越南 FAST 登录账号（例如: "ADMIN" 或您的财务工号）
  username: {json.dumps(username, ensure_ascii=False)}

  # ★ 请在此处填写您的越南 FAST 登录密码
  password: {json.dumps(password, ensure_ascii=False)}

  # 核算单位代码（默认 "CTY"）
  unit: {json.dumps(unit, ensure_ascii=False)}

  # 登录界面语言自动切换设置：
  # FAST 登录界面默认是越南语 (vi)，建议保持为 "en"（自动切换为英文界面后再登录，确保菜单与 TB 表均为英文）
  # 可选值: "en" (English 英文界面) | "vi" (Tiếng Việt 越南语界面)
  language: {json.dumps(language, ensure_ascii=False)}

chrome_cdp:
  # 本地 Google Chrome 可执行文件路径（Windows 默认路径如下，程序也会自动探测）
  chrome_path: '{chrome_path}'
  # Chrome 远程调试端口（默认 9222）
  remote_debugging_port: {cdp_port}
  # 自动化步骤最大重试次数
  max_retries: 3
  # 任务完成后是否自动关闭浏览器
  close_browser_on_exit: true

# FAST 页面关键 F12 DOM 元素选择器配置（支持根据现场版本微调）
selectors:
  # 登录页元素 (http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx)
  username_input: {json.dumps(sel.get("username_input", "input[id='LoginExtender_txtUserName']"))}
  password_input: {json.dumps(sel.get("password_input", "input[id='LoginExtender_txtPassword']"))}
  unit_input: {json.dumps(sel.get("unit_input", "input[name='txtUnit'], input[id*='Unit'], select[id*='Unit']"))}
  language_select: {json.dumps(sel.get("language_select", "select[id*='Language'], select[id*='lang'], a[id*='English'], input[value='E']"))}
  login_button: {json.dumps(sel.get("login_button", "button[id='LoginExtender_Ok'], input[id='LoginExtender_Ok']"))}
  # 登录后英文主界面 & TB 表 (Trial Balance) 常用导航文本
  menu_general_ledger: "General Ledger"
  menu_journal_entry: "Journal Entry"
  menu_post_to_gl: "Post to GL"
  menu_trial_balance: "Trial Balance"
"""
    cred_path = BASE_DIR / "config" / "fast_credentials.yaml"
    cred_path.write_text(yaml_text, encoding="utf-8")
    fresh = handle_api_fast_credentials_get()
    fresh["message"] = (
        f"💾 已写入 config/fast_credentials.yaml（账号: [{fresh['effective']['username']}]，掩码: {fresh['effective']['masked_password']}）。"
        f"正在真实访问目标地址 [{login_url}] 校验该账密是否有效..."
        if fresh["is_ready_for_auto_login"]
        else "已保存至 config/fast_credentials.yaml，但账号或密码为空，请补充完整后再测试登录。"
    )
    return fresh


def handle_api_fast_login_verify(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    [POST /api/fast-login-verify]
    调用 utils.login_handler.verify_fast_login_live 真实通过浏览器/HTTP 访问目标 URL (默认 http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx)：
    - 若目标 URL 访问超时或无法打开：捕获 TimeoutError / URLError / Chrome ERR_* 并返回 overall_status="URL_UNREACHABLE"；
    - 若目标 URL 服务端返回账密错误或过期：捕获服务端错误提示并返回 overall_status="AUTH_FAILED"；
    - 仅当真实访问该 URL 并通过服务端登录鉴权进入主界面时，返回 overall_status="PASS"。
    """
    from utils.login_handler import verify_fast_login_live

    cred_status = handle_api_fast_credentials_get()
    eff = cred_status["effective"]
    cfg = cred_status["config_data"]

    has_explicit_user = "test_username" in payload and payload.get("test_username") is not None
    has_explicit_pass = "test_password" in payload and payload.get("test_password") is not None

    test_user = str(payload.get("test_username")).strip() if has_explicit_user else eff["username"]
    test_pass = str(payload.get("test_password")) if has_explicit_pass else cfg["fast_auth"]["password"]
    test_unit = str(payload.get("test_unit") or eff.get("unit") or "CTY").strip() or "CTY"
    test_lang = str(payload.get("test_language") or eff.get("language") or "en").strip() or "en"
    test_url = str(payload.get("test_login_url") or eff.get("login_url") or "").strip() or eff["login_url"]
    timeout_sec = float(payload.get("timeout_sec") or 4.0)

    live_res = verify_fast_login_live(
        test_username=test_user,
        test_password=test_pass,
        test_unit=test_unit,
        test_language=test_lang,
        test_login_url=test_url,
        timeout_sec=max(1.5, min(timeout_sec, 15.0)),
        headless=True,
        config_dir=str(BASE_DIR / "config"),
    )
    live_res["effective_credentials"] = eff
    return live_res


def handle_api_smoke_test() -> Dict[str, Any]:
    """
    [POST /api/smoke-test]
    在线触发 1104 全量 7 大模块冒烟验收测试（tests/_smoke_test.py），返回详细测试指标。
    """
    from tests._smoke_test import run_smoke_test
    result = run_smoke_test()
    return {"ok": True, "result": result, **result}


def handle_api_skills() -> Dict[str, Any]:
    """
    [GET /api/skills]
    扫描项目 .agents/skills/ 目录下的全部 Skill 定义文件 (SKILL.md)，
    解析并返回每个 Skill 的名称、描述、版本、路径及状态。
    """
    skills_dir = BASE_DIR / ".agents" / "skills"
    items: List[Dict[str, Any]] = []
    if skills_dir.exists():
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            folder_name = skill_md.parent.name
            raw_text = skill_md.read_text(encoding="utf-8", errors="replace")
            desc = ""
            version = "1.0.0"
            for line in raw_text.splitlines():
                s = line.strip()
                if s.startswith("description:") and not desc:
                    desc = s.split(":", 1)[1].strip().strip('"\'|')
                elif s.startswith("version:"):
                    version = s.split(":", 1)[1].strip().strip('"\'')
            items.append({
                "id": folder_name,
                "name": folder_name,
                "version": version,
                "description": desc or f"Project Skill: {folder_name}",
                "path": f".agents/skills/{folder_name}/SKILL.md",
            })
    return {"ok": True, "count": len(items), "skills": items}


def handle_api_doc(name: str) -> Dict[str, Any]:
    """
    [GET /api/docs/<name> & GET /api/doc/<name>]
    仅读取项目根目录下的 10 份 Markdown 说明文档（最新 Reviews 报告、1104 评估报告、
    需求 6/7 验收报告、README、登陆链接、两份 1103 CFO 会议纪要、使用说明、开发说明、财务业务需求），
    返回文档内容与行数统计供前端 Markdown 预览渲染。
    """
    target: Optional[Path] = None
    if name == "review_latest":
        reviews = sorted(BASE_DIR.glob("Reviews_*.md"), reverse=True)
        if reviews:
            target = reviews[0]
    else:
        doc_map = {
            "eval_1104": BASE_DIR / "功能对比、评估报告1104.md",
            "accept_1104": BASE_DIR / "需求6-7实现验收报告1104.md",
            "readme": BASE_DIR / "README.md",
            "cfo_1103": BASE_DIR / "1103_CFO会议纪要_正式版.md",
            "cfo_update_1103": BASE_DIR / "1103与CFO会议纪要及需求更新.md",
            "manual_ops": BASE_DIR / "SAP2FAST 使用说明（财务与运维操作手册）.md",
            "arch_dev": BASE_DIR / "SAP2FAST 开发说明（技术架构与重构设计文档）.md",
            "biz_req": BASE_DIR / "财务业务需求.md",
            "login_guide": BASE_DIR / "登陆链接.md",
        }
        target = doc_map.get(name)

    if not target or not target.exists():
        raise FileNotFoundError(f"文档不存在: {name}")

    text = target.read_text(encoding="utf-8")
    rel_name = str(target.relative_to(BASE_DIR)) if target.is_relative_to(BASE_DIR) else target.name
    return {
        "ok": True,
        "name": name,
        "filename": rel_name,
        "line_count": len(text.splitlines()),
        "size_bytes": len(text.encode("utf-8")),
        "content": text,
    }


# ------------------------------------------------------------------------------
# 5. 基于 Python 标准库的零延迟多线程 HTTP 服务端（兼容 FastAPI 路由规范）
# ------------------------------------------------------------------------------
class ReusableThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """
    Linux/POSIX 下开启 allow_reuse_address 复用 TIME_WAIT 端口；
    Windows (nt) 下保持 allow_reuse_address = False，确保当 3000 端口被其他进程占用时
    准确抛出 WinError 10048 并触发自动进程检测与端口顺延。
    """
    allow_reuse_address = (os.name != "nt")
    daemon_threads = True


class SAP2FastWorkbenchHandler(http.server.BaseHTTPRequestHandler):
    """
    SAP2FAST 1104 可视化工作台 HTTP 请求处理器：
    - 支持静态资源服务 (`/`, `/static/*`, `/api/download`)
    - 支持全部 JSON API 接口 (`/api/overview`, `/api/status`, `/api/sample-convert`, `/api/convert`,
      `/api/audit`, `/api/mappings`, `/api/mappings/action`, `/api/db-maintenance/preview`,
      `/api/fast-credentials`, `/api/fast-login-verify`, `/api/smoke-test`, `/api/skills`, `/api/docs/*`)
    """

    def log_message(self, format: str, *args: Any) -> None:
        # 保持控制台输出清爽，仅在异常时打印详细堆栈
        pass

    def _send_json(self, data: Any, status_code: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, file_path: Path, download_name: Optional[str] = None) -> None:
        if not file_path.exists() or not file_path.is_file():
            self._send_json({"detail": f"文件不存在: {file_path.name}"}, status_code=404)
            return
        content = file_path.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if file_path.suffix == ".css":
            mime_type = "text/css; charset=utf-8"
        elif file_path.suffix in (".html", ".htm"):
            mime_type = "text/html; charset=utf-8"
        elif file_path.suffix == ".js":
            mime_type = "application/javascript; charset=utf-8"
        elif not mime_type:
            mime_type = "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(content)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}

    def _send_html(self, html_str: str, status_code: int = 200) -> None:
        raw = html_str.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    @staticmethod
    def _render_fast_test_login_html(error_msg: str = "") -> str:
        err_span = (
            f'<span id="LoginExtender_lblError" class="login-error" style="color:red;">{error_msg}</span>'
            if error_msg else ""
        )
        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>FAST Financial - Login.aspx</title></head>
<body>
  <form method="post" action="/fast-test-server/FAW_TIMBERLAND/Main/Login.aspx">
    <input type="hidden" name="__VIEWSTATE" value="dDwtMTI3OTM0Njc4O3Q8O2w8aTwxPjs+O2w8dDw7bDxpPDE+Oz47bDx0PHA8cDxsPFZpc2libGU7PjtsPG88dD47Pj47Pjs7Pjs+Pjs+Pjs+" />
    <input type="hidden" name="__VIEWSTATEGENERATOR" value="C2EE9ABB" />
    <input type="hidden" name="__EVENTVALIDATION" value="/wEdAAVe8K3c9F0a" />
    <input type="text" id="LoginExtender_txtUserName" name="LoginExtender$txtUserName" />
    <input type="password" id="LoginExtender_txtPassword" name="LoginExtender$txtPassword" />
    <input type="text" id="txtUnit" name="txtUnit" value="CTY" />
    <select id="LoginExtender_ddlLanguage" name="LoginExtender$ddlLanguage">
      <option value="V">Tiếng Việt</option>
      <option value="E">English</option>
    </select>
    <button type="submit" id="LoginExtender_Ok" name="LoginExtender$Ok" value="Login">Login</button>
    {err_span}
  </form>
</body>
</html>"""

    def do_HEAD(self) -> None:
        """支持健康检查探针发送的 HEAD 请求。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                self._send_file(STATIC_DIR / "index.html")
            elif path == "/fast-test-server/FAW_TIMBERLAND/Main/Login.aspx":
                self._send_html(self._render_fast_test_login_html(""))
            elif path.startswith("/static/"):
                rel = path[len("/static/"):]
                target = (STATIC_DIR / rel).resolve()
                if not str(target).startswith(str(STATIC_DIR)):
                    self._send_json({"detail": "非法静态文件路径"}, status_code=403)
                    return
                self._send_file(target)
            elif path in ("/api/overview", "/api/status"):
                self._send_json(handle_api_status())
            elif path == "/api/fast-credentials":
                self._send_json(handle_api_fast_credentials_get())
            elif path == "/api/skills":
                self._send_json(handle_api_skills())
            elif path in ("/api/mappings", "/api/mapping/search"):
                q = qs.get("search", qs.get("q", [""]))[0]
                self._send_json(handle_api_mapping_search(q))
            elif path.startswith("/api/docs/"):
                doc_name = path[len("/api/docs/"):].strip("/")
                self._send_json(handle_api_doc(doc_name))
            elif path.startswith("/api/doc/"):
                doc_name = path[len("/api/doc/"):].strip("/")
                self._send_json(handle_api_doc(doc_name))
            elif path == "/api/download":
                rel_path = qs.get("path", [""])[0]
                target = (BASE_DIR / rel_path).resolve()
                if not str(target).startswith(str(BASE_DIR)) or not target.exists():
                    self._send_json({"detail": "文件不存在或路径越权"}, status_code=404)
                    return
                self._send_file(target, download_name=target.name)
            else:
                self._send_json({"detail": f"未找到路由: {path}"}, status_code=404)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"ok": False, "detail": str(exc)}, status_code=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/fast-test-server/FAW_TIMBERLAND/Main/Login.aspx":
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw_form = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else ""
                form_qs = parse_qs(raw_form, keep_blank_values=True)
                u = (form_qs.get("LoginExtender$txtUserName") or form_qs.get("LoginExtender_txtUserName") or [""])[0].strip()
                p = (form_qs.get("LoginExtender$txtPassword") or form_qs.get("LoginExtender_txtPassword") or [""])[0]
                if u in ("FIN_ADMIN_VN", "ADMIN") and p == "Fast@2026#Timber":
                    self._send_html(f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>FAST Financial - Main/Default.aspx</title></head>
<body>
  <div id="header">Welcome {u} | <a href="Logout.aspx" id="btnLogout">Logout</a></div>
  <nav id="menu"><span>General Ledger</span> | <span>Journal Entry</span> | <span>Post to GL</span> | <span>Trial Balance</span></nav>
</body>
</html>""")
                elif "expired" in p.lower() or "2023" in p or "2024" in p:
                    self._send_html(
                        self._render_fast_test_login_html(
                            f'Tài khoản hoặc mật khẩu đã hết hạn! / Password for user "{u or "UNKNOWN"}" has expired on FAST Server (错误代码: FAST_AUTH_403_EXPIRED)'
                        )
                    )
                else:
                    self._send_html(
                        self._render_fast_test_login_html(
                            f'Sai tên đăng nhập hoặc mật khẩu! / Invalid username or password for user "{u or "EMPTY"}" (错误代码: FAST_AUTH_401_INVALID_CREDENTIALS)'
                        )
                    )
                return

            payload = self._read_json_body()
            if path == "/api/generate-sample":
                self._send_json(handle_api_generate_sample())
            elif path in ("/api/sample-convert", "/api/convert"):
                self._send_json(handle_api_convert(payload))
            elif path == "/api/audit":
                self._send_json(handle_api_audit(payload))
            elif path in ("/api/mappings/action", "/api/mapping"):
                self._send_json(handle_api_mapping(payload))
            elif path in ("/api/db-maintenance/preview", "/api/db-dry-run"):
                self._send_json(handle_api_db_dry_run(payload))
            elif path == "/api/fast-credentials":
                self._send_json(handle_api_fast_credentials_save(payload))
            elif path == "/api/fast-login-verify":
                self._send_json(handle_api_fast_login_verify(payload))
            elif path == "/api/smoke-test":
                self._send_json(handle_api_smoke_test())
            else:
                self._send_json({"detail": f"未找到 POST 路由: {path}"}, status_code=404)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"ok": False, "detail": str(exc)}, status_code=500)


# ------------------------------------------------------------------------------
# 6. Windows / Linux 端口占用检测、占用进程识别与自动端口顺延机制（彻底解决 WinError 10048）
# ------------------------------------------------------------------------------
def _is_port_occupied(port: int) -> bool:
    """检测本地 127.0.0.1:<port> 是否已有服务正在监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _find_windows_occupying_pids(port: int) -> List[int]:
    """
    在 Windows 环境下调用 `netstat -ano` 查找占用指定端口的进程 PID 列表，
    帮助用户在出现 `[WinError 10048]` 时一目了然看到占用者或自动清理。
    """
    pids: List[int] = []
    if os.name != "nt":
        return pids
    try:
        out = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore")
        for line in out.splitlines():
            if f":{port} " in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pid = int(parts[-1])
                    if pid > 0 and pid not in pids:
                        pids.append(pid)
    except Exception:
        pass
    return pids


def _kill_occupying_pids_on_windows(pids: List[int]) -> None:
    """在 Windows 下当用户传入 `--kill-port` 时，自动终止占用端口的旧进程。"""
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"[端口释放] 已自动终止占用端口的旧进程 PID={pid}")
        except Exception as e:
            print(f"[端口释放警告] 无法终止进程 PID={pid}: {e}")


def start_workbench_server(host: str = "0.0.0.0", preferred_port: int = 3000, kill_port: bool = False) -> None:
    """
    启动 SAP2FAST 1104 Web 工作台服务：
    1. 毫秒级直接尝试绑定 `preferred_port`（默认 3000）；
    2. 若触发 `[WinError 10048]` 或 `EADDRINUSE`（端口已被占用）：
       - 自动识别并打印 Windows 占用进程 PID；
       - 若指定了 `--kill-port`，自动清理占用进程后重新绑定 `preferred_port`；
       - 否则自动顺延尝试 `preferred_port + 1` ~ `preferred_port + 25`（如 3001、3002），
         确保本地执行 `python app.py` 永远不会因端口冲突而崩溃退出！
    3. 绑定成功后，启动后台线程预热 `sap2fast_full_fix` 转换引擎与默认测试样表。
    """
    target_port = preferred_port
    httpd = None

    for idx, attempt_port in enumerate([preferred_port] + list(range(preferred_port + 1, preferred_port + 25))):
        try:
            httpd = ReusableThreadingHTTPServer((host, attempt_port), SAP2FastWorkbenchHandler)
            target_port = attempt_port
            if idx > 0:
                print(
                    f"[自动避让 WinError 10048] 原端口 {preferred_port} 已被占用，"
                    f"已自动切换至可用端口: {target_port} (也可使用 `python app.py --port 3000 --kill-port` 强制释放原端口)"
                )
            break
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10048 or exc.errno in (98, 48, 10048):
                if idx == 0:
                    pids = _find_windows_occupying_pids(attempt_port)
                    pid_msg = f" (占用进程 PID: {pids})" if pids else ""
                    print(f"[端口检测] 端口 {attempt_port} 当前已被占用{pid_msg}。")
                    if kill_port and pids:
                        _kill_occupying_pids_on_windows(pids)
                        time.sleep(0.4)
                        try:
                            httpd = ReusableThreadingHTTPServer((host, attempt_port), SAP2FastWorkbenchHandler)
                            target_port = attempt_port
                            break
                        except OSError:
                            pass
                continue
            raise

    if httpd is None:
        raise RuntimeError(f"无法在 {preferred_port}~{preferred_port + 25} 范围内找到可用端口，请检查本地网络配置。")

    # 后台守护线程：预加载 pandas/openpyxl/sap2fast_full_fix 并在首次启动时生成默认 43 列模板文件
    def _background_warmup():
        try:
            s2f, _, _ = _get_core_modules()
            if not glob.glob(str(BASE_DIR / "output" / "*.xlsx")):
                sample_p = s2f.generate_raw_sap_sample_file()
                s2f.reprocess_local_files(
                    input_pattern=sample_p,
                    output_dir=str(BASE_DIR / "output"),
                )
        except Exception as e:
            print(f"[后台预热提示] 初始样表预热忽略: {e}")

    threading.Thread(target=_background_warmup, daemon=True).start()

    print("=" * 76)
    print(f"SAP2FAST (1104 全量闭环版) 可视化工作台已启动！")
    print(f"  - 本地访问地址: http://127.0.0.1:{target_port}")
    print(f"  - 局域网/容器 : http://{host}:{target_port}")
    print("=" * 76)
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[服务关闭] 收到中断信号，正在安全关闭 SAP2FAST 工作台...")
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SAP2FAST 1104 可视化 Web 运维与平账审计工作台")
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="监听地址（默认 0.0.0.0）",
    )
    _env_port = os.environ.get("PORT", "3000")
    _default_port = 3000 if _env_port in ("8080", "8000", "") else int(_env_port)
    parser.add_argument(
        "--port",
        type=int,
        default=_default_port,
        help="监听端口（默认 3000；若在本地被占用将自动顺延至 3001+，避免 WinError 10048）",
    )
    parser.add_argument(
        "--kill-port",
        action="store_true",
        help="Windows 下若目标端口被旧进程占用，自动终止占用该端口的旧进程",
    )
    args, _ = parser.parse_known_args()
    start_workbench_server(host=args.host, preferred_port=args.port, kill_port=args.kill_port)
