# -*- coding: utf-8 -*-
"""
生成一份「原始 SAP 导出」示例数据，用于离线验证 sap2fast_full_fix.py 的
input -> output 转换主流程（无需连接数据库）。

输出：samples/sample_raw_sap.xlsx（与 HanaSQL.QUERY_VOUCHER_BY_VOUCHER_NO 查询
结果的列结构完全一致），另存一份 samples/sample_raw_sap.csv 便于查看。

运行：
    python samples/generate_sample_data.py

然后用它跑离线转换：
    python sap2fast_full_fix.py --mode local --input samples/sample_raw_sap.xlsx --output output
"""
import os
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与 HanaSQL.QUERY_VOUCHER_BY_VOUCHER_NO / QUERY_VOUCHER_BY_DATE 返回列顺序一致
COLS = [
    "cpudt", "company_code", "year", "period", "voucher_no", "voucher_type",
    "account_code", "direction", "book_amount", "original_amount",
    "cost_center", "fkber", "lifnr", "kunnr", "text_summary", "zuonr", "currency",
]

ROWS = [
    # 简单 1借1贷（VND）：银行存款 -> 应付账款
    ("20240301", "2390", "2024", "03", "1000000001", "SA", "1001000001", "H", 1000.00, 0.00, "", "1000", "", "", "Thanh toan tien hang", "", "VND"),
    ("20240301", "2390", "2024", "03", "1000000001", "SA", "2202000001", "S", 1000.00, 0.00, "", "1000", "", "", "Thanh toan tien hang", "", "VND"),
    # 多借多贷（VND）：2 借 2 贷，触发多借多贷拆分分组
    ("20240302", "2390", "2024", "03", "1000000002", "SA", "6603030001", "H", 600.00, 0.00, "", "1000", "", "", "Chi phi san xuat", "", "VND"),
    ("20240302", "2390", "2024", "03", "1000000002", "SA", "6603030002", "H", 400.00, 0.00, "", "1000", "", "", "Chi phi san xuat", "", "VND"),
    ("20240302", "2390", "2024", "03", "1000000002", "SA", "1122000001", "S", 700.00, 0.00, "", "1000", "", "", "Chi phi san xuat", "", "VND"),
    ("20240302", "2390", "2024", "03", "1000000002", "SA", "2202000001", "S", 300.00, 0.00, "", "1000", "", "", "Chi phi san xuat", "", "VND"),
    # 外币（USD）1借1贷：银行存款 -> 应付账款
    ("20240303", "2390", "2024", "03", "1000000003", "SA", "1121000001", "H", 100.00, 100.00, "", "1000", "", "", "Thu ngoai te", "", "USD"),
    ("20240303", "2390", "2024", "03", "1000000003", "SA", "2202000080", "S", 100.00, 100.00, "", "1000", "", "", "Thu ngoai te", "", "USD"),
]

df = pd.DataFrame(ROWS, columns=COLS)

out_xlsx = os.path.join(BASE, "samples", "sample_raw_sap.xlsx")
out_csv = os.path.join(BASE, "samples", "sample_raw_sap.csv")
os.makedirs(os.path.dirname(out_xlsx), exist_ok=True)
df.to_excel(out_xlsx, index=False)
df.to_csv(out_csv, index=False, encoding="utf-8-sig")
print(f"已生成示例原始 SAP 数据：")
print(f"  {out_xlsx} ({len(df)} 行)")
print(f"  {out_csv}")
