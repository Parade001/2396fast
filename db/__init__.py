# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: db/__init__.py
模块定位: 数据库连接池 (SAP HANA / FAST MSSQL) 与 SQL 模板统一导出包
================================================================================
【本文件核心作用】
1. 导出 `HanaSQL` 与 `MSSQLSQL` 参数化 SQL 模板类（无需安装数据库驱动即可在离线/Dry-Run 模式下安全导入）；
2. 导出 `HanaConnectionPool` 与 `MSSQLConnectionPool` 数据库连接池管理类（当离线环境未安装 `sqlalchemy` 时优雅降级，不阻断离线模板转换与 Dry-Run 预览）。
================================================================================
"""

from db.sql_templates import (
    HanaSQL,
    MSSQLSQL,
)

try:
    from db.hana_db import (
        HanaConfig,
        HanaConnectionPool,
        get_hana_pool,
        init_hana_pool,
        close_hana_pool,
    )
except ImportError:
    HanaConfig = None
    HanaConnectionPool = None
    get_hana_pool = None
    init_hana_pool = None
    close_hana_pool = None

try:
    from db.mssql_db import (
        MSSQLConfig,
        MSSQLConnectionPool,
        get_mssql_pool,
        init_mssql_pool,
        close_mssql_pool,
    )
except ImportError:
    MSSQLConfig = None
    MSSQLConnectionPool = None
    get_mssql_pool = None
    init_mssql_pool = None
    close_mssql_pool = None

__all__ = [
    # HANA
    "HanaConfig",
    "HanaConnectionPool",
    "get_hana_pool",
    "init_hana_pool",
    "close_hana_pool",
    # MSSQL
    "MSSQLConfig",
    "MSSQLConnectionPool",
    "get_mssql_pool",
    "init_mssql_pool",
    "close_mssql_pool",
    # SQL Templates
    "HanaSQL",
    "MSSQLSQL",
]
