# -*- coding: utf-8 -*-
"""
数据库模块

提供HANA和MSSQL的连接池管理及SQL模板
所有连接参数从 env_loader 模块读取（基于 .env 文件）
"""

from db.hana_db import (
    HanaConfig,
    HanaConnectionPool,
    get_hana_pool,
    init_hana_pool,
    close_hana_pool
)

from db.mssql_db import (
    MSSQLConfig,
    MSSQLConnectionPool,
    get_mssql_pool,
    init_mssql_pool,
    close_mssql_pool
)

from db.sql_templates import (
    HanaSQL,
    MSSQLSQL   
)
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
