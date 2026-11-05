# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/general.py
模块定位: 跨国财年期间换算、过账日期压缩与通用判等工具函数模块
================================================================================
【本文件核心作用】
1. `fiscalym_to_calendarym` / `calandarym_to_ficalym`：实现 SAP 财年期间（4月起算）与越南日历月份（+3个月）双向换算；
2. `resolve_period_date`：处理跨月补记凭证的过账日期（当录入日 `cpudt` 跨月时，自动压缩至所属期间自然月最后一天）；
3. `get_next_fast_date` / `get_sap_begin_end_cupdt`：查询 FAST 最新日期与 SAP 期间起止录入日期；
4. `safe_eq`：兼容 `NaN` / `None` 的安全判等函数。
================================================================================
"""

from datetime import timedelta
import calendar
import pandas as pd

from db import MSSQLConnectionPool,MSSQLSQL,HanaConnectionPool,HanaSQL

# fast 最新的凭证更新日期
def get_next_fast_date() -> str:
    """获取最新的更新日期"""
    with MSSQLConnectionPool() as pool:
        df = pool.execute_query(MSSQLSQL.QUERY_LATEST_DATE)
        return (df.iloc[0]['latest_date']+ timedelta(days=1)).strftime('%Y%m%d')

# sap 特定账期内最早和最晚日期
def get_sap_begin_end_cupdt(period: dict)  -> tuple[str, str]:
    """获取sap的开始和结束凭证更新日期"""
    with HanaConnectionPool() as pool:
        df = pool.execute_query(HanaSQL.format_begin_end_cpudt(period))
        return df.iloc[0]['begin_cupdt'].strftime('%Y%m%d'), df.iloc[0]['end_cupdt'].strftime('%Y%m%d')

def fiscalym_to_calendarym(fiscalym: str) -> str:
    """将财务期间转换为日历期间"""
    year = int(fiscalym[:4])
    period = int(fiscalym[4:6])
    if period > 9:
        year += 1
        period -= 9
    else:
        period += 3
    return f"{year}{period:02d}"

def calandarym_to_ficalym(calandarym: str) -> str:
    """将日历期间转换为财务期间"""
    year = int(calandarym[:4])
    period = int(calandarym[4:6])
    if period < 3:
        year -= 1
        period += 9
    else:
        period -= 3
    return f"{year}{period:02d}"
    
def resolve_period_date(cpudt: str, year: int, period: int) -> str:
    """
    根据 cpudt 与 (year, period+3) 的早晚关系，返回目标日期字符串 (yyyymmdd)。

    - cpudt 早于 (year, period+3)  → 返回该月月初：f"{year}{month:02d}01"
    - cpudt 等于 (year, period+3)  → 直接返回 cpudt 原值
    - cpudt 晚于 (year, period+3)  → 返回该月月末：f"{year}{month:02d}{last_day:02d}"

    :param cpudt: yyyymmdd 格式的日期字符串（8 位）
    :param year:  目标年份
    :param period: 财务期间 (1~9)，函数内部 +3 转为月份 (4~12)
    :return: yyyymmdd 格式的日期字符串
    """
    if not (isinstance(cpudt, str) and len(cpudt) == 8 and cpudt.isdigit()):
        raise ValueError(f"cpudt 必须是 8 位数字字符串，实际为: {cpudt!r}")

    month = period + 3
    if  month > 12:
        year +=1
        month -=12

    cpudt_year = int(cpudt[:4])
    cpudt_month = int(cpudt[4:6])
    cpudt_day =  cpudt[-2:]

    if (cpudt_year, cpudt_month) < (year, month):
        return f"01/{month:02d}/{year}"
    if (cpudt_year, cpudt_month) == (year, month):
        return f"{cpudt_day}/{month:02d}/{year}"

    last_day = calendar.monthrange(year, month)[1]
    return f"{last_day:02d}/{month:02d}/{year}"

# pd.na对比函数
def safe_eq(val, target) -> bool:
    """
    安全的相等比较，处理 pandas 的 NA/NaN/None 缺失值。

    pandas 的 NA/NaN 在 if 条件里会抛 "boolean value of NA is ambiguous"，
    而 df.itertuples() 会把缺失值转成 pandas.NA，这里统一转为 False。

    Args:
        val: 待比较的值
        target: 比较目标

    Returns:
        val == target，缺失值返回 False
    """
    try:
        if pd.isna(val):
            return False
    except (TypeError, ValueError):
        pass
    return val == target
