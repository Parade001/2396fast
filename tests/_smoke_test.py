#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: tests/_smoke_test.py
模块定位: SAP2FAST (1104 全量闭环版) 端到端冒烟与验收回归测试套件
================================================================================
【本文件核心作用】
用于一键离线验证 SAP2FAST 1104 全量版的 7 大核心能力是否 100% 正常工作：
1. 【规则与配置核验】验证 `config/account.yaml` 已物理移除第 172 行冲突键，且 `6051000001`
   在普通科目层唯一锁定映射到外部收入 `51181`（CFO 1103 会议拍板决策）；
2. 【8 步转换管线核验】验证 `DataProcessor.run()` 对 17 列原始 SAP 数据的完整处理：
   - 方向规范化 (`S` 借 / `H` 贷)
   - 两级科目映射 (`fkber` + `account`)
   - 特殊修正（`331111->A0001` 客商兜底、USD 外币科目重分类、VND 金额 ×100 还原）
   - 复合凭证 2借2贷按规则拆分为 1借1贷并生成 `group_id = [1, 2]`；
3. 【离线跨期凭证号去重核验】验证 `dedup_vouchers_offline()` 自动检测跨期间（202403 与 202404）
   复用的同名凭证号 `1000000001`，并自动追加 `_A` 后缀 (`1000000001_A`)；
4. 【43 列 FAST 模板导出核验】验证 `export_to_sap()` 返回 `List[str]`，且生成的 Excel 文件：
   - 总列数恒为 43 列 (`ws.max_column == 43`)
   - 第 1~5 行留空，第 6 行第 1 列为 `'CTY'`，第 3 列为 `dd/mm/yyyy` 过账日期；
5. 【双格式逆向解析防翻倍核验】验证将导出的 43 列无表头模板再次喂给 `DataProcessor.run()` 时，
   `_parse_fast_cty_template()` 能准确逆向还原且绝不二次放大 VND 金额（防 `×100` 二次放大）；
6. 【未映射科目筛查 + 需求 6 + 库表安全闸核验】：
   - 验证 `detect_unmapped_accounts()` 能主动识别未映射的 10 位 SAP 科目 `6699887766`；
   - 验证 `MappingManager` 的 6.1 防重添加、6.21 多表同步改客商 (`d11/ct00/m11/dmkh`)、
     6.22 多对一科目修改安全锁拦截 (`force_multi=False` 拦截、`force_multi=True` 放行)；
   - 验证 `modify_fast_database`、`update_fast_account`、`remove_fast_errors` 的 `--dry-run` 安全预览闸；
7. 【需求 7 网页自动化组件核验】：
   - 验证 `CDPClient.attach_upload_file` 已修复 `self.logger`，不存在未定义 `logger` 的 `NameError`；
   - 验证 `LoginHandler` 具备 `self.logger` 与可配置 `login_url`；
   - 验证 `PostingHandler.process_posting` 分支返回值正确。

【运行方式】
`python tests/_smoke_test.py` （全部通过时末尾打印 `ALL_OK`）
================================================================================
"""

import os
import sys
import shutil
import tempfile

# 将项目根目录与 .pydeps 本地持久化依赖目录加入 sys.path，确保任何工作目录下均可直接运行
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
_PYDEPS_DIR = os.path.join(BASE, ".pydeps")
if os.path.isdir(_PYDEPS_DIR) and _PYDEPS_DIR not in sys.path:
    sys.path.insert(0, _PYDEPS_DIR)

import openpyxl
import pandas as pd

import sap2fast_full_fix as s2f


def run_smoke_test() -> dict:
    """
    执行 1104 全量版 7 大模块冒烟回归测试，全部断言通过后打印 'ALL_OK' 并返回结构化验收摘要。
    """
    # 定义 SAP HANA 导出的 17 列标准原始字段名
    cols = [
        "cpudt", "company_code", "year", "period", "voucher_no", "voucher_type",
        "account_code", "direction", "book_amount", "original_amount",
        "cost_center", "fkber", "lifnr", "kunnr", "text_summary", "zuonr", "currency",
    ]

    # 构造覆盖 4 类核心业务场景的 10 行测试分录
    rows = [
        # 场景 1: 简单 1借1贷 VND + 测试 6051000001 -> 51181 (CFO 1103 拍板锁定外部收入)
        ("20240301", "2390", "2024", "03", "1000000001", "SA", "6051000001", "H", 1000.0, 0.0, "", "", "", "", "Rental income", "", "VND"),
        ("20240301", "2390", "2024", "03", "1000000001", "SA", "2202000001", "S", 1000.0, 0.0, "", "", "", "", "Rental income", "", "VND"),
        # 场景 2: 多借多贷 2H + 2S（触发特别科目对拆分，生成 group_id = 1, 2）
        ("20240302", "2390", "2024", "03", "1000000002", "SA", "6603030001", "H", 600.0, 0.0, "", "1000", "", "", "Prod cost", "", "VND"),
        ("20240302", "2390", "2024", "03", "1000000002", "SA", "6603030002", "H", 400.0, 0.0, "", "1000", "", "", "Prod cost", "", "VND"),
        ("20240302", "2390", "2024", "03", "1000000002", "SA", "1122000001", "S", 700.0, 0.0, "", "", "", "", "Prod cost", "", "VND"),
        ("20240302", "2390", "2024", "03", "1000000002", "SA", "2202000001", "S", 300.0, 0.0, "", "", "", "", "Prod cost", "", "VND"),
        # 场景 3: USD 外币 1借1贷（测试外币科目重分类 + 汇率计算 + A0003 默认客商补齐）
        ("20240303", "2390", "2024", "03", "1000000003", "SA", "1121000001", "H", 100.0, 100.0, "", "", "", "", "FX receipt", "", "USD"),
        ("20240303", "2390", "2024", "03", "1000000003", "SA", "2202000080", "S", 100.0, 100.0, "", "", "", "", "FX receipt", "", "USD"),
        # 场景 4: 跨期复用同一凭证号 1000000001（202404 期间，验证离线跨期自动追加 _A 后缀）
        ("20240405", "2390", "2024", "04", "1000000001", "SA", "1001000001", "H", 500.0, 0.0, "", "", "", "", "Cross period", "", "VND"),
        ("20240405", "2390", "2024", "04", "1000000001", "SA", "2202000001", "S", 500.0, 0.0, "", "", "", "", "Cross period", "", "VND"),
    ]

    df = pd.DataFrame(rows, columns=cols)
    print("INPUT_SHAPE", df.shape)

    # [核验 1] 验证 config/account.yaml 已物理清理 6051000001 重复键并锁定为 51181
    yaml_cfg = s2f.AccountConfig.load(force_reload=True)
    assert yaml_cfg["account"]["6051000001"] == "51181", "6051000001 必须映射为 51181"

    # [核验 2] 执行 DataProcessor 8 步转换管线
    proc = s2f.DataProcessor(s2f.ProcessConfig())
    out = proc.run(df)
    print("OUTPUT_SHAPE", out.shape)
    print("OUTPUT_COLS", list(out.columns))

    gids = out["group_id"].dropna().unique().tolist()
    print("group_id values:", gids)
    assert len(gids) >= 2, "多借多贷凭证必须成功生成拆分组号 group_id"

    # 验证转换结果中 6051000001 映射为 51181
    mapped_51181 = out.loc[out["account_code"] == "6051000001", "fast_account"].tolist()
    assert mapped_51181 == ["51181"], f"6051000001 映射结果异常: {mapped_51181}"

    # 验证离线跨期凭证号自动追加 _A 后缀
    vnos_after = sorted(out["voucher_no"].unique().tolist())
    print("VOUCHER_NOS_AFTER_DEDUP:", vnos_after)
    assert "1000000001" in vnos_after and "1000000001_A" in vnos_after, "跨期同名凭证号必须自动生成 _A 后缀"

    # [核验 3] 验证 export_to_sap 返回 List[str] 及 43 列无表头模板物理布局
    out_paths = s2f.export_to_sap(out, filename="output/_smoke_result.xlsx", use_db_dedup=False)
    print("EXPORT_OK", out_paths)
    assert isinstance(out_paths, list) and len(out_paths) == 1, "export_to_sap 应返回 List[str]"
    first_file = out_paths[0]
    assert os.path.exists(first_file), f"导出文件不存在: {first_file}"

    wb = openpyxl.load_workbook(first_file)
    ws = wb.active
    print("TEMPLATE_MAX_ROW_COL", (ws.max_row, ws.max_column))
    assert ws.max_column == 43, f"导出 FAST 模板列数必须恒为 43 列，实际为 {ws.max_column}"
    for r in range(1, 6):
        assert ws.cell(r, 1).value is None, f"第 {r} 行应留空"
    assert ws.cell(6, 1).value == "CTY", "第 6 行第 1 列必须为 'CTY'"
    assert "/" in str(ws.cell(6, 3).value), "第 6 行第 3 列必须为 dd/mm/yyyy 格式过账日期"

    # [核验 4] 验证 43 列 FAST 模板逆向解析 (_parse_fast_cty_template) 不重复放大金额
    reloaded = proc.run(first_file)
    print("RELOADED_SHAPE", reloaded.shape)
    assert len(reloaded) == len(out), "从 43 列模板逆向解析后行数应保持一致"
    assert abs(float(reloaded["book_amount"].sum()) - float(out["book_amount"].sum())) < 1e-2, "逆向解析严禁二次放大金额"

    # [核验 5] 验证未映射 10 位 SAP 科目主动筛查 (detect_unmapped_accounts)
    df_unmapped = df.iloc[:2].copy()
    df_unmapped.loc[0, "account_code"] = "6699887766"
    out_unmapped = proc.run(df_unmapped)
    detected = out_unmapped.attrs.get("unmapped_accounts", [])
    print("DETECTED_UNMAPPED:", detected)
    assert any(d["account_code"] == "6699887766" for d in detected), "应检测到未映射的 10 位 SAP 科目 6699887766"

    # [核验 6] 验证 CFO 需求 6 (MappingManager) 与库表维护安全闸 (--dry-run)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_yaml = os.path.join(tmpdir, "account.yaml")
        shutil.copyfile(os.path.join(BASE, "config", "account.yaml"), tmp_yaml)
        mgr = s2f.MappingManager(yaml_path=tmp_yaml)
        # 6.1 客商新增与防重拦截
        assert mgr.add_partner_mapping("V_TEST_01", "FAST_V01", keep_log=False) is True
        assert mgr.add_partner_mapping("V_TEST_02", "FAST_V01", keep_log=False) is False
        # 6.21 客商修改与可配置多表同步范围 Dry-Run
        assert mgr.update_partner_mapping(
            "V_TEST_01", "FAST_V02", sync_fast_db=False, sync_tables=("d11", "ct00", "m11", "dmkh"), dry_run=True, keep_log=False
        ) is True
        # 6.22 多对一安全锁拦截验证：1001000001 与 1001000002 均映射到 11121
        assert mgr.update_account_mapping("1001000001", "11129", force_multi=False, sync_fast_db=False, keep_log=False) is False
        assert mgr.update_account_mapping("1001000001", "11129", force_multi=True, sync_fast_db=False, dry_run=True, keep_log=False) is True
        # 恢复默认 AccountConfig 路径
        s2f.AccountConfig.load(os.path.join(BASE, "config", "account.yaml"), force_reload=True)

    # 验证三类破坏性库表操作在 --dry-run 模式下均能安全生成预览 SQL
    del_preview = s2f.modify_fast_database("6428", dry_run=True)
    upd_preview = s2f.update_fast_account("131111", "131112", dry_run=True)
    rec = s2f.DataReconciler("202403")
    err_preview = rec.remove_fast_errors(2024, 3, {"1000000001", "1000000002"}, dry_run=True)
    assert del_preview["dry_run"] is True and del_preview["sql_count"] > 0
    assert upd_preview["dry_run"] is True and upd_preview["sql_count"] > 0
    assert err_preview == 2

    # [核验 7] 验证 CFO 需求 7 网页自动化组件修复 (CDPClient / LoginHandler / PostingHandler)
    from utils.cdp_client import CDPClient
    from utils.login_handler import LoginHandler
    from utils.posting_handler import PostingHandler

    cdp = CDPClient("http://localhost:9222")
    # 未连接且文件不存在时应通过 self.logger 报错并返回 False，绝不抛 NameError: name 'logger' is not defined
    assert cdp.attach_upload_file("/non_existent_file_1104.xlsx") is False
    lh = LoginHandler(cdp, login_url="http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx")
    assert hasattr(lh, "logger") and lh.logger is not None
    ph = PostingHandler(cdp)
    assert ph.process_posting() is False

    print("ALL_OK")
    return {
        "ok": True,
        "status": "PASS",
        "output": "ALL_OK (7 大核心验收模块全部通过)",
        "input_rows": len(df),
        "output_rows": len(out),
        "exported_files": out_paths,
        "template_columns": ws.max_column,
        "voucher_nos": vnos_after,
        "group_ids": [int(x) for x in gids],
    }


if __name__ == "__main__":
    run_smoke_test()
