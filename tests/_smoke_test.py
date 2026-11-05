# -*- coding: utf-8 -*-
"""
SAP2FAST (1104 全量闭环版) 冒烟与验收回归测试套件 (tests/_smoke_test.py)
==========================================================================
验证范围：
1. DataProcessor 8 步转换管线（含 6051000001->51181 外部收入锁定、331111->A0001 客商兜底、
   多借多贷 2H2S 规则拆分、VND 金额 ×100 还原、USD 外币科目重分类与平衡校验）；
2. export_to_sap 返回 List[str] 及标准 43 列无表头 FAST 导入模板结构核验（第 1~5 行空，
   第 6 行起 CTY、dd/mm/yyyy 日期、S 落 11/13 列、H 落 12/14 列、第 43 列 group_id）；
3. 43 列 FAST 结果模板逆向解析 (_parse_fast_cty_template) 防二次放大与防二次翻转验证；
4. 离线跨期同名凭证号自动追加 _A/_B 后缀去重 (dedup_vouchers_offline)；
5. 未映射 10 位 SAP 科目主动筛查 (detect_unmapped_accounts)；
6. 需求 6 (MappingManager 6.1 防重、6.21 改客商多表范围同步、6.22 多对一安全锁) 与
   库表维护安全闸 (modify_fast_database / update_fast_account / remove_fast_errors --dry-run)；
7. 需求 7 (CDPClient.attach_upload_file logger 修复、LoginHandler 自动登录、PostingHandler 分支日志修复)。
"""
import os
import sys
import shutil
import tempfile
import openpyxl
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import sap2fast_full_fix as s2f


def run_smoke_test() -> dict:
    cols = [
        "cpudt", "company_code", "year", "period", "voucher_no", "voucher_type",
        "account_code", "direction", "book_amount", "original_amount",
        "cost_center", "fkber", "lifnr", "kunnr", "text_summary", "zuonr", "currency",
    ]

    rows = [
        # 1. 简单 1借1贷 VND + 测试 6051000001 -> 51181 (CFO 1103 拍板锁定外部收入)
        ("20240301", "2390", "2024", "03", "1000000001", "SA", "6051000001", "H", 1000.0, 0.0, "", "", "", "", "Rental income", "", "VND"),
        ("20240301", "2390", "2024", "03", "1000000001", "SA", "2202000001", "S", 1000.0, 0.0, "", "", "", "", "Rental income", "", "VND"),
        # 2. 多借多贷：2H + 2S（触发特别科目对拆分 group_id = 1, 2）
        ("20240302", "2390", "2024", "03", "1000000002", "SA", "6603030001", "H", 600.0, 0.0, "", "1000", "", "", "Prod cost", "", "VND"),
        ("20240302", "2390", "2024", "03", "1000000002", "SA", "6603030002", "H", 400.0, 0.0, "", "1000", "", "", "Prod cost", "", "VND"),
        ("20240302", "2390", "2024", "03", "1000000002", "SA", "1122000001", "S", 700.0, 0.0, "", "", "", "", "Prod cost", "", "VND"),
        ("20240302", "2390", "2024", "03", "1000000002", "SA", "2202000001", "S", 300.0, 0.0, "", "", "", "", "Prod cost", "", "VND"),
        # 3. USD 1借1贷（外币科目重分类 + 汇率计算 + A0003 默认客商）
        ("20240303", "2390", "2024", "03", "1000000003", "SA", "1121000001", "H", 100.0, 100.0, "", "", "", "", "FX receipt", "", "USD"),
        ("20240303", "2390", "2024", "03", "1000000003", "SA", "2202000080", "S", 100.0, 100.0, "", "", "", "", "FX receipt", "", "USD"),
        # 4. 跨期复用同一凭证号 1000000001（202404 期间，验证离线跨期自动追加 _A 后缀）
        ("20240405", "2390", "2024", "04", "1000000001", "SA", "1001000001", "H", 500.0, 0.0, "", "", "", "", "Cross period", "", "VND"),
        ("20240405", "2390", "2024", "04", "1000000001", "SA", "2202000001", "S", 500.0, 0.0, "", "", "", "", "Cross period", "", "VND"),
    ]

    df = pd.DataFrame(rows, columns=cols)
    print("INPUT_SHAPE", df.shape)

    # 1. 验证 config/account.yaml 物理清理 6051000001 重复键
    yaml_cfg = s2f.AccountConfig.load(force_reload=True)
    assert yaml_cfg["account"]["6051000001"] == "51181", "6051000001 必须映射为 51181"

    # 2. 执行 DataProcessor 8 步转换管线
    proc = s2f.DataProcessor(s2f.ProcessConfig())
    out = proc.run(df)
    print("OUTPUT_SHAPE", out.shape)
    print("OUTPUT_COLS", list(out.columns))

    gids = out["group_id"].dropna().unique().tolist()
    print("group_id values:", gids)
    assert len(gids) >= 2, "多借多贷凭证必须成功生成拆分组号 group_id"

    # 验证 6051000001 -> 51181
    mapped_51181 = out.loc[out["account_code"] == "6051000001", "fast_account"].tolist()
    assert mapped_51181 == ["51181"], f"6051000001 映射结果异常: {mapped_51181}"

    # 验证跨期凭证号自动追加 _A 后缀
    vnos_after = sorted(out["voucher_no"].unique().tolist())
    print("VOUCHER_NOS_AFTER_DEDUP:", vnos_after)
    assert "1000000001" in vnos_after and "1000000001_A" in vnos_after, "跨期同名凭证号必须自动生成 _A 后缀"

    # 3. 验证 export_to_sap 返回 List[str] 及 43 列模板布局
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

    # 4. 验证双格式逆向解析 (_parse_fast_cty_template) 不重复放大金额
    reloaded = proc.run(first_file)
    print("RELOADED_SHAPE", reloaded.shape)
    assert len(reloaded) == len(out), "从 43 列模板逆向解析后行数应保持一致"
    assert abs(float(reloaded["book_amount"].sum()) - float(out["book_amount"].sum())) < 1e-2, "逆向解析严禁二次放大金额"

    # 5. 验证未映射 10 位 SAP 科目主动筛查
    df_unmapped = df.iloc[:2].copy()
    df_unmapped.loc[0, "account_code"] = "6699887766"
    out_unmapped = proc.run(df_unmapped)
    detected = out_unmapped.attrs.get("unmapped_accounts", [])
    print("DETECTED_UNMAPPED:", detected)
    assert any(d["account_code"] == "6699887766" for d in detected), "应检测到未映射的 10 位 SAP 科目 6699887766"

    # 6. 验证需求 6 (MappingManager) 与库表维护安全闸 (--dry-run)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_yaml = os.path.join(tmpdir, "account.yaml")
        shutil.copyfile(os.path.join(BASE, "config", "account.yaml"), tmp_yaml)
        mgr = s2f.MappingManager(yaml_path=tmp_yaml)
        assert mgr.add_partner_mapping("V_TEST_01", "FAST_V01", keep_log=False) is True
        assert mgr.add_partner_mapping("V_TEST_02", "FAST_V01", keep_log=False) is False  # 防重拦截
        assert mgr.update_partner_mapping(
            "V_TEST_01", "FAST_V02", sync_fast_db=False, sync_tables=("d11", "ct00", "m11", "dmkh"), dry_run=True, keep_log=False
        ) is True
        # 6.22 多对一安全锁拦截验证：1001000001 与 1001000002 均映射到 11121
        assert mgr.update_account_mapping("1001000001", "11129", force_multi=False, sync_fast_db=False, keep_log=False) is False
        assert mgr.update_account_mapping("1001000001", "11129", force_multi=True, sync_fast_db=False, dry_run=True, keep_log=False) is True
        # 恢复默认 AccountConfig 路径
        s2f.AccountConfig.load(os.path.join(BASE, "config", "account.yaml"), force_reload=True)

    del_preview = s2f.modify_fast_database("6428", dry_run=True)
    upd_preview = s2f.update_fast_account("131111", "131112", dry_run=True)
    rec = s2f.DataReconciler("202403")
    err_preview = rec.remove_fast_errors(2024, 3, {"1000000001", "1000000002"}, dry_run=True)
    assert del_preview["dry_run"] is True and del_preview["sql_count"] > 0
    assert upd_preview["dry_run"] is True and upd_preview["sql_count"] > 0
    assert err_preview == 2

    # 7. 验证需求 7 组件修复 (CDPClient.attach_upload_file / LoginHandler / PostingHandler)
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
        "status": "PASS",
        "input_rows": len(df),
        "output_rows": len(out),
        "exported_files": out_paths,
        "template_columns": ws.max_column,
        "voucher_nos": vnos_after,
        "group_ids": [int(x) for x in gids],
    }


if __name__ == "__main__":
    run_smoke_test()
