#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: update_account.py
模块定位: 越南 FAST 数据库 (SQL Server) 历史科目代码批量替换独立工具脚本
================================================================================
【本文件核心作用】
1. 当财务调整科目映射后，需要将越南 FAST 数据库中已入账的旧科目 (`old_account`) 批量替换为
   新科目 (`new_account`) 时，支持按年份（如 2024、2025）及币种范围（仅 USD 外币业务 / 全部币种）
   同步更新 FAST 的核心账表：
   - `r00$YYYYMM`（汇总表本方科目 `tk` 与对方科目 `tk_du`）
   - `d11$YYYYMM`（与 `m11$YYYYMM` 联表按币种更新明细表科目 `tk`）
   - `i11$YYYYMM`（更新检索表 `d$` 字段中的科目串）
   - `ct00`（总账底表本方科目 `tk` 与对方科目 `tk_du`）

【1104 版本关键修改与修复说明】
1. 老版本中第 72~73 行的 `tx.execute(...)` 与 `tx.commit()` 被注释掉，且第 7/14 行将 `i11$` 误写成了 `i111$`；
2. 1104 版已在 `sap2fast_full_fix.py` 的 `update_fast_account()` 及本文件中修复了 `i11$` 表名拼写，
   取消了事务注释使其真正生效，并新增 `--dry-run` 预览与审计日志支持：
   `python sap2fast_full_fix.py --mode update-account --old-account 131111 --new-account 131112 --dry-run`
================================================================================
"""

from db import get_mssql_pool

# 仅针对 USD 外币业务更新科目的 SQL 模板列表（已修正 i11${yyyymm} 表名）
SQL_USD = [
    "update r00${yyyymm} set tk='{new_account}' where tk='{old_account}' and ma_nt = 'USD'",
    "update r00${yyyymm} set tk_du='{new_account}' where tk_du='{old_account}' and ma_nt = 'USD'",
    "update d11${yyyymm} set tk='{new_account}' from m11${yyyymm} a,d11${yyyymm} b where a.stt_rec=b.stt_rec and a.ma_nt='USD' and b.tk='{old_account}'",
    "update i11${yyyymm} set d$=replace(d$,'{old_account}','{new_account}') where d$ like '%{old_account}%' and m$='#10$USD'",
    "update ct00 set tk='{new_account}' where tk='{old_account}' and ma_nt = 'USD'",
    "update ct00 set tk_du='{new_account}' where tk_du='{old_account}' and ma_nt = 'USD'",
]

# 针对所有币种更新科目的 SQL 模板列表（已修正 i11${yyyymm} 表名）
SQL = [
    "update r00${yyyymm} set tk='{new_account}' where tk='{old_account}'",
    "update r00${yyyymm} set tk_du='{new_account}' where tk_du='{old_account}'",
    "update d11${yyyymm} set tk='{new_account}' from m11${yyyymm} a,d11${yyyymm} b where a.stt_rec=b.stt_rec and b.tk='{old_account}'",
    "update i11${yyyymm} set d$=replace(d$,'{old_account}','{new_account}') where d$ like '%{old_account}%'",
    "update ct00 set tk='{new_account}' where tk='{old_account}'",
    "update ct00 set tk_du='{new_account}' where tk_du='{old_account}'",
]

# 每年 12 个月份后缀
mm = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12']


def update_account() -> None:
    """
    交互式批量更新越南 FAST 数据库中的科目代码（含事务提交保护）。
    """
    print('======================================')
    print('FAST 历史科目批量更新流程：')
    print('1. 输入年份（如 2024 或 2025）；')
    print('2. 选择更新方式：1-仅针对 USD 业务更新科目代码，2-更新所有币种科目；')
    print('3. 按 E 退出')
    print('======================================')
    pool = get_mssql_pool()

    while True:
        yyyy = input('请输入年份（四位数字，如2024，输入E退出）：').strip()
        if str(yyyy).upper() == "E":
            break
        if not (yyyy.isdigit() and 2023 < int(yyyy) < 2030):
            print("请输入正确的年份，年份需在 2024~2029 之间!")
            continue
        else:
            print(f"已确定年份：{yyyy}年\n")

        select = input('请选择更新方式（选择数字即可:\n1-只更新USD币种的科目；\n2-更新所有币种科目）：').strip()
        if select not in ['1', '2']:
            print("请输入正确的更新方式 (1 或 2)!")
            continue
        else:
            print(f"确定更新年份：{yyyy}年，更新方式：{select}")

        account = input('请输入旧科目-新科目（例如：131111-131112）：').strip()
        if '-' not in account:
            print("请输入正确的科目格式（旧科目-新科目）!")
            continue

        old_account, new_account = [x.strip() for x in account.split('-', 1)]
        print(f"确定更新年份：{yyyy}年，\n更新方式：{select}，\n旧科目：{old_account}，\n新科目：{new_account}")
        confirm = input('确认在数据库中执行更新并提交事务？（Y/N）：').strip()
        if confirm.upper() != 'Y':
            print("更新已取消!")
            continue

        print('正在执行数据库科目更新事务...')
        with pool.begin_transaction() as tx:
            for m in mm:
                yyyymm = f"{yyyy}{m}"
                for sql_tpl in (SQL_USD if str(select) == '1' else SQL):
                    sql_stmt = sql_tpl.format(yyyymm=yyyymm, old_account=old_account, new_account=new_account)
                    print(sql_stmt)
                    tx.execute(sql_stmt)
                print(f"更新期间 {yyyymm} 完成!")
            tx.commit()
            print(f"已成功提交 {yyyy} 年科目 ({old_account} -> {new_account}) 更新事务！")


if __name__ == '__main__':
    update_account()
