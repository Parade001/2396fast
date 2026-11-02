# -*- coding: utf-8 -*-
"""临时冒烟测试：用内存构造的原始 SAP 数据跑通 sap2fast_full_fix.py 的转换管线。
不读取任何 .xlsx 文件，仅用于定位运行时 bug。"""
import os, sys
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import sap2fast_full_fix as s2f

cols = [
    "cpudt", "company_code", "year", "period", "voucher_no", "voucher_type",
    "account_code", "direction", "book_amount", "original_amount",
    "cost_center", "fkber", "lifnr", "kunnr", "text_summary", "zuonr", "currency",
]

rows = [
    # 简单 1借1贷 VND
    ("20240301", "2390", "2024", "03", "1000000001", "SA", "1001000001", "H", 1000.0, 0.0, "", "", "", "", "text", "", "VND"),
    ("20240301", "2390", "2024", "03", "1000000001", "SA", "2202000001", "S", 1000.0, 0.0, "", "", "", "", "text", "", "VND"),
    # 多借多贷：2H + 2S（触发分组）
    ("20240302", "2390", "2024", "03", "1000000002", "SA", "6603030001", "H", 600.0, 0.0, "", "1000", "", "", "text", "", "VND"),
    ("20240302", "2390", "2024", "03", "1000000002", "SA", "6603030002", "H", 400.0, 0.0, "", "1000", "", "", "text", "", "VND"),
    ("20240302", "2390", "2024", "03", "1000000002", "SA", "1122000001", "S", 700.0, 0.0, "", "", "", "", "text", "", "VND"),
    ("20240302", "2390", "2024", "03", "1000000002", "SA", "2202000001", "S", 300.0, 0.0, "", "", "", "", "text", "", "VND"),
    # USD 1借1贷
    ("20240303", "2390", "2024", "03", "1000000003", "SA", "1121000001", "H", 100.0, 100.0, "", "", "", "", "text", "", "USD"),
    ("20240303", "2390", "2024", "03", "1000000003", "SA", "2202000080", "S", 100.0, 100.0, "", "", "", "", "text", "", "USD"),
]

df = pd.DataFrame(rows, columns=cols)
print("INPUT_SHAPE", df.shape)

proc = s2f.DataProcessor(s2f.ProcessConfig())
out = proc.run(df)
print("OUTPUT_SHAPE", out.shape)
print("OUTPUT_COLS", list(out.columns))
print("group_id values:", out["group_id"].dropna().unique().tolist() if "group_id" in out else "N/A")
print("fast_voucher_no exists after export? checking via export...")

out_path = s2f.export_to_sap(out, filename="output/_smoke_result.xlsx")
print("EXPORT_OK", out_path)

# 验证 fast_voucher_no 长度与内容
res = pd.read_excel(out_path)
print("RESULT_COLS", list(res.columns))
print("RESULT_SHAPE", res.shape)
print("fast_voucher_no:", res["fast_voucher_no"].tolist())
print("posting_date:", res["posting_date"].tolist())
print("ALL_OK")
