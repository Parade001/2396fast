#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: modify_fast_database.py
模块定位: 越南 FAST 数据库 (SQL Server) 按科目级联清理历史凭证独立工具脚本
================================================================================
【本文件核心作用】
1. 当某个越南 FAST 科目（如 `6428`）在历史年度（2024、2025 年 01~12 月）存在错账需要整科清理重导时，
   根据 `ct00` 总账表反查关联的 `stt_rec`（FAST 内部凭证唯一流水号），并依次级联删除 6 类核心分表：
   - `c11$000000`（跨期凭证主索引表）
   - `r00$YYYYMM`（期间过账汇总表）
   - `d11$YYYYMM`（期间通用凭证分录明细表）
   - `o11$000000`（凭证辅助关联表）
   - `m11$YYYYMM`（期间通用凭证抬头主表）
   - `i11$YYYYMM`（期间凭证全文检索索引表）
   最后删除 `ct00` 总账底表中的对应科目记录。

【1104 版本安全升级说明】
- 本文件已完整内聚并升级至 `sap2fast_full_fix.py` 的 `modify_fast_database()` 函数中；
- 推荐优先使用带 `--dry-run` SQL 安全预览、二次确认及 `logs/db_maintenance_audit.log` 审计日志的命令：
  `python sap2fast_full_fix.py --mode delete-account --target-account 6428 --dry-run`
================================================================================
"""

from db import get_mssql_pool

# 需要按 stt_rec 级联清理的 FAST 分表模板列表（注意保留表名中的 $ 符号）
update_table = ['c11$000000', 'r00${ym}', 'd11${ym}', 'o11$000000', 'm11${ym}', 'i11${ym}']

# 每年 12 个财务月份
M = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12']


def modify_fast_database(account_no: str) -> None:
    """
    按 FAST 科目号级联删除 2024~2025 年全部月度分表及 ct00 总账表中的关联凭证。
    :param account_no: 待清理的越南 FAST 科目代码
    """
    year = ['2024', '2025']
    pool = get_mssql_pool()
    for y in year:
        for mm in M:
            ym = f'{y}{mm}'
            for table in update_table:
                # 仅替换 {ym} 占位符，保留 FAST 分表名中的 $ 符号（如 r00$202401）
                table_name = table.replace('${ym}', f'${ym}').replace('{ym}', ym)
                sql = (
                    "delete " + table_name +
                    " where stt_rec in (select distinct stt_rec from ct00 where tk='" + account_no + "'); "
                )
                pool.execute_none(sql)
    sql = "delete ct00 where tk='" + account_no + "';"
    pool.execute_none(sql)


if __name__ == '__main__':
    account_no = input('请输入要级联清理的 FAST 科目号：').strip()
    confirm = input(f'高危操作确认：即将从 FAST 数据库级联删除科目 {account_no} 关联的全部凭证，确认继续？(Y/N)：').strip()
    if confirm.upper() == 'Y':
        modify_fast_database(account_no)
        print('修改完成')
    else:
        print('已取消操作（建议使用 `python sap2fast_full_fix.py --mode delete-account --target-account ' + account_no + ' --dry-run` 先预览 SQL）')
