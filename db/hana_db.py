# -*- coding: utf-8 -*-
"""
HANA数据库连接池模块

连接参数从 env_loader 模块读取（基于 .env 文件）
"""

import sys
from pathlib import Path
from typing import Optional, Any
from contextlib import contextmanager
import threading
from dataclasses import dataclass
import pandas as pd
from sqlalchemy import create_engine, Engine
from sqlalchemy.pool import QueuePool

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入env_loader
from config import get_env_config, EnvConfig


@dataclass
class HanaConfig:
    """HANA数据库配置"""
    host: str=''
    user: str=''
    password: str=''
    port: int = 30015
    database: str = "SYSTEMDB"

    def to_uri(self) -> str:
        """生成HANA连接URI"""
        return f"hana+hdbcli://{self.user}:{self.password}@{self.host}:{self.port}"

    @classmethod
    def from_env(cls, env: EnvConfig = None) -> "HanaConfig":
        """
        从 env_loader 创建配置

        Args:
            env: EnvConfig实例，为None则自动获取

        Returns:
            HanaConfig实例
        """
        if env is None:
            env = get_env_config()
        return cls(
            host=env.hana_host,
            port=env.hana_port,
            user=env.hana_user,
            password=env.hana_password,
            database=env.hana_database
        )


class HanaConnectionPool:
    """
    HANA连接池管理器

    使用SQLAlchemy的QueuePool实现连接池复用
    """

    def __init__(
        self,
        config: HanaConfig,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_recycle: int = 3600,
        pool_pre_ping: bool = True
    ):
        """
        初始化连接池

        Args:
            config: HANA配置
            pool_size: 基础连接数
            max_overflow: 最大溢出连接数
            pool_recycle: 连接回收时间(秒)
            pool_pre_ping: 使用前检测连接有效性
        """
        self.config = config
        self._engine: Optional[Engine] = None
        self._lock = threading.Lock()

        # 连接池参数
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pool_recycle = pool_recycle
        self._pool_pre_ping = pool_pre_ping

    @classmethod
    def from_env(
        cls,
        env: EnvConfig = None,
        **pool_kwargs
    ) -> "HanaConnectionPool":
        """
        从 env_loader 创建连接池

        Args:
            env: EnvConfig实例，为None则自动获取
            **pool_kwargs: 连接池其他参数

        Returns:
            HanaConnectionPool实例
        """
        if env is None:
            env = get_env_config()

        # 如果未指定 pool_size 等参数，从 env 读取
        pool_kwargs.setdefault("pool_size", env.hana_pool_size)
        pool_kwargs.setdefault("max_overflow", env.hana_max_overflow)
        pool_kwargs.setdefault("pool_recycle", env.hana_pool_recycle)

        config = HanaConfig.from_env(env)
        return cls(config, **pool_kwargs)

    @property
    def engine(self) -> Engine:
        """获取或创建数据库引擎"""
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    self._engine = create_engine(
                        self.config.to_uri(),
                        poolclass=QueuePool,
                        pool_size=self._pool_size,
                        max_overflow=self._max_overflow,
                        pool_recycle=self._pool_recycle,
                        pool_pre_ping=self._pool_pre_ping,
                        echo=False  # 生产环境设为False
                    )
        return self._engine

    @contextmanager
    def get_connection(self):
        """
        获取数据库连接的上下文管理器

        Usage:
            with pool.get_connection() as conn:
                result = conn.execute(text("SELECT 1"))
        """
        conn = self.engine.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def begin_transaction(self):
        """
        获取事务连接的上下文管理器

        Usage:
            with pool.begin_transaction() as tx:
                tx.execute(text("INSERT INTO ..."))
                tx.commit()
        """
        with self.engine.begin() as tx:
            yield tx

    def execute_query(self, sql: str, params: dict = None) -> pd.DataFrame:
        """
        执行查询并返回DataFrame

        Args:
            sql: SQL语句
            params: 查询参数

        Returns:
            查询结果DataFrame
        """
        with self.get_connection() as conn:
            if params:
                df = pd.read_sql(sql, conn, params=params)
            else:
                df = pd.read_sql(sql, conn)
            return df

    def execute_scalar(self, sql: str, params: dict = None) -> Any:
        """
        执行查询并返回单个值

        Args:
            sql: SQL语句
            params: 查询参数

        Returns:
            查询结果的第一行第一列
        """
        with self.get_connection() as conn:
            from sqlalchemy import text
            result = conn.execute(text(sql), params or {})
            row = result.fetchone()
            return row[0] if row else None

    def execute_none(self, sql: str, params: dict = None):
        """
        执行不返回结果的SQL（INSERT/UPDATE/DELETE）

        Args:
            sql: SQL语句
            params: 查询参数
        """
        with self.begin_transaction() as tx:
            from sqlalchemy import text
            tx.execute(text(sql), params or {})

    def dispose(self):
        """释放所有连接池连接"""
        if self._engine:
            self._engine.dispose()
            self._engine = None


# 全局连接池实例
_pool_instance: Optional[HanaConnectionPool] = None
_pool_lock = threading.Lock()


def get_hana_pool(config: HanaConfig = None) -> HanaConnectionPool:
    """
    获取HANA连接池单例

    Args:
        config: HANA配置，如果为None则从 env_loader 读取

    Returns:
        HanaConnectionPool实例
    """
    global _pool_instance

    if _pool_instance is None:
        with _pool_lock:
            if _pool_instance is None:
                if config is None:
                    # 从 env_loader 读取配置
                    config = HanaConfig.from_env()
                _pool_instance = HanaConnectionPool(config)
                # 同步连接池参数
                env = get_env_config()
                _pool_instance._pool_size = env.hana_pool_size
                _pool_instance._max_overflow = env.hana_max_overflow
                _pool_instance._pool_recycle = env.hana_pool_recycle

    return _pool_instance


def init_hana_pool(
    host: str = None,
    port: int = None,
    user: str = None,
    password: str = None,
    database: str = None,
    use_env: bool = True,
    **pool_kwargs
) -> HanaConnectionPool:
    """
    初始化HANA连接池

    Args:
        host: 服务器地址，None时从env读取
        port: 端口，None时从env读取
        user: 用户名，None时从env读取
        password: 密码，None时从env读取
        database: 数据库名，None时从env读取
        use_env: 是否使用env_loader中的连接池参数
        **pool_kwargs: 连接池其他参数（pool_size, max_overflow, pool_recycle等）

    Returns:
        HanaConnectionPool实例

    Usage:
        # 方式1: 完全从 .env 读取
        init_hana_pool()

        # 方式2: 显式传入参数
        init_hana_pool(host="...", port=30015, user="...", password="...", database="...")

        # 方式3: 部分参数从env读取
        init_hana_pool(host="custom_host", use_env=False)
    """
    global _pool_instance

    env = get_env_config()

    # 合并参数：显式传入 > env
    final_host = host if host is not None else env.hana_host
    final_port = port if port is not None else env.hana_port
    final_user = user if user is not None else env.hana_user
    final_password = password if password is not None else env.hana_password
    final_database = database if database is not None else env.hana_database

    config = HanaConfig(
        host=final_host,
        port=final_port,
        user=final_user,
        password=final_password,
        database=final_database
    )

    # 连接池参数
    if use_env:
        pool_kwargs.setdefault("pool_size", env.hana_pool_size)
        pool_kwargs.setdefault("max_overflow", env.hana_max_overflow)
        pool_kwargs.setdefault("pool_recycle", env.hana_pool_recycle)

    with _pool_lock:
        if _pool_instance:
            _pool_instance.dispose()
        _pool_instance = HanaConnectionPool(config, **pool_kwargs)

    return _pool_instance


def close_hana_pool():
    """关闭HANA连接池"""
    global _pool_instance
    with _pool_lock:
        if _pool_instance:
            _pool_instance.dispose()
            _pool_instance = None
