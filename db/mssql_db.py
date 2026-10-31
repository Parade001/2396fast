# -*- coding: utf-8 -*-
"""
MSSQL数据库连接池模块

连接参数从 env_loader 模块读取（基于 .env 文件）
"""

import sys
from pathlib import Path
from typing import Optional, Any, List, Tuple
from contextlib import contextmanager
import threading
import time
from dataclasses import dataclass
import pandas as pd

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入env_loader
from config import get_env_config, EnvConfig

try:
    import pymssql
    from pymssql import Connection, Cursor
except ImportError:
    pymssql = None
    Connection = None
    Cursor = None


@dataclass
class MSSQLConfig:
    """MSSQL数据库配置"""
    host: str
    user: str
    password: str
    database: str
    port: int = 1433

    def to_params(self) -> dict:
        """生成连接参数"""
        return {
            "server": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database
        }

    @classmethod
    def from_env(cls, env: EnvConfig = None) -> "MSSQLConfig":
        """
        从 env_loader 创建配置

        Args:
            env: EnvConfig实例，为None则自动获取

        Returns:
            MSSQLConfig实例
        """
        if env is None:
            env = get_env_config()
        return cls(
            host=env.mssql_host,
            port=env.mssql_port,
            user=env.mssql_user,
            password=env.mssql_password,
            database=env.mssql_database
        )


class MSSQLConnectionPool:
    """
    MSSQL连接池管理器

    手动实现连接池，支持连接复用和自动重连
    """

    def __init__(
        self,
        config: MSSQLConfig,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_recycle: int = 3600
    ):
        """
        初始化连接池

        Args:
            config: MSSQL配置
            pool_size: 基础连接数
            max_overflow: 最大溢出连接数
            pool_recycle: 连接回收时间(秒)
        """
        if pymssql is None:
            raise ImportError("请安装 pymssql: pip install pymssql")

        self.config = config
        self._pool: List[dict] = []
        self._in_use: set = set()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._max_pool_size = pool_size + max_overflow
        self._pool_recycle = pool_recycle

        # 初始化连接池
        self._init_pool()

    @classmethod
    def from_env(
        cls,
        env: EnvConfig = None,
        **pool_kwargs
    ) -> "MSSQLConnectionPool":
        """
        从 env_loader 创建连接池

        Args:
            env: EnvConfig实例，为None则自动获取
            **pool_kwargs: 连接池其他参数

        Returns:
            MSSQLConnectionPool实例
        """
        if env is None:
            env = get_env_config()

        # 如果未指定 pool_size 等参数，从 env 读取
        pool_kwargs.setdefault("pool_size", env.mssql_pool_size)
        pool_kwargs.setdefault("max_overflow", env.mssql_max_overflow)
        pool_kwargs.setdefault("pool_recycle", env.mssql_pool_recycle)

        config = MSSQLConfig.from_env(env)
        return cls(config, **pool_kwargs)

    def _init_pool(self):
        """初始化基础连接"""
        for _ in range(self._pool_size):
            try:
                conn = pymssql.connect(**self.config.to_params())
                self._pool.append({
                    "conn": conn,
                    "created_at": time.time()
                })
            except Exception as e:
                print(f"初始化连接失败: {e}")

    def _is_connection_alive(self, conn: Connection) -> bool:
        """检查连接是否有效"""
        try:
            conn.ping()
            return True
        except:
            return False

    def _create_connection(self) -> Optional[Connection]:
        """创建新连接"""
        try:
            conn = pymssql.connect(**self.config.to_params())
            return conn
        except Exception as e:
            print(f"创建新连接失败: {e}")
            return None

    def _get_connection(self, timeout: int = 30) -> Optional[Connection]:
        """
        获取一个可用连接

        Args:
            timeout: 超时时间(秒)

        Returns:
            数据库连接对象
        """
        start_time = time.time()

        while True:
            with self._lock:
                # 优先从池中获取可用连接
                for item in list(self._pool):
                    conn = item["conn"]
                    conn_id = id(conn)

                    if conn_id not in self._in_use:
                        # 检查连接是否有效
                        if self._is_connection_alive(conn):
                            # 检查是否需要回收
                            if time.time() - item["created_at"] > self._pool_recycle:
                                try:
                                    conn.close()
                                    conn = self._create_connection()
                                    if conn:
                                        item["conn"] = conn
                                        item["created_at"] = time.time()
                                except:
                                    continue

                            self._in_use.add(conn_id)
                            return conn
                        else:
                            # 连接失效，移除
                            try:
                                conn.close()
                            except:
                                pass
                            try:
                                self._pool.remove(item)
                            except:
                                pass

                # 池中没有可用连接，尝试创建新的
                if len(self._pool) + len(self._in_use) < self._max_pool_size:
                    conn = self._create_connection()
                    if conn:
                        conn_id = id(conn)
                        self._pool.append({
                            "conn": conn,
                            "created_at": time.time()
                        })
                        self._in_use.add(conn_id)
                        return conn

            # 检查超时
            if time.time() - start_time > timeout:
                raise TimeoutError("获取数据库连接超时")

            time.sleep(0.1)

    @contextmanager
    def get_connection(self, timeout: int = 30):
        """
        获取数据库连接的上下文管理器

        Usage:
            with pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
        """
        conn = self._get_connection(timeout)
        conn_id = id(conn)

        try:
            yield conn
        finally:
            with self._lock:
                self._in_use.discard(conn_id)

    @contextmanager
    def cursor(self, timeout: int = 30):
        """
        获取游标的上下文管理器

        Usage:
            with pool.cursor() as cursor:
                cursor.execute("SELECT * FROM table")
                result = cursor.fetchall()
        """
        with self.get_connection(timeout) as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()

    @contextmanager
    def begin_transaction(self):
        """
        获取事务连接的上下文管理器

        Usage:
            with pool.begin_transaction() as tx:
                tx.execute("INSERT INTO ...")
                tx.commit()
        """
        conn = self._get_connection()
        conn_id = id(conn)
        tx = conn.cursor()

        try:
            yield tx
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            tx.close()
            with self._lock:
                self._in_use.discard(conn_id)

    def execute_query(
        self,
        sql: str,
        params: tuple = None,
        as_dict: bool = False
    ) -> pd.DataFrame:
        """
        执行查询并返回DataFrame

        Args:
            sql: SQL语句
            params: 查询参数
            as_dict: 是否将结果转为字典

        Returns:
            查询结果DataFrame
        """
        with self.cursor() as cursor:
            cursor.execute(sql, params)

            if as_dict:
                columns = [d[0] for d in cursor.description]
                rows = cursor.fetchall()
                return pd.DataFrame(rows, columns=columns)
            else:
                df = pd.DataFrame(cursor.fetchall())
                if not df.empty:
                    df.columns = [d[0] for d in cursor.description]
                return df

    def execute_scalar(self, sql: str, params: tuple = None) -> Any:
        """
        执行查询并返回单个值

        Args:
            sql: SQL语句
            params: 查询参数

        Returns:
            查询结果的第一行第一列
        """
        with self.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return row[0] if row else None

    def execute_none(self, sql: str, params: tuple = None):
        """
        执行不返回结果的SQL（INSERT/UPDATE/DELETE）

        Args:
            sql: SQL语句
            params: 查询参数
        """
        with self.begin_transaction() as cursor:
            cursor.execute(sql, params)

    def execute_many(self, sql: str, params_list: List[tuple]):
        """
        批量执行SQL

        Args:
            sql: SQL语句
            params_list: 参数列表
        """
        with self.begin_transaction() as cursor:
            cursor.executemany(sql, params_list)

    def batch_check_exists(
        self,
        sql_template: str,
        value_list: List[Any],
        param_name: str = "value",
        batch_size: int = 1000
    ) -> dict:
        """
        批量检查值是否存在

        Args:
            sql_template: SQL模板，需要包含 {param_name} 占位符
            value_list: 要检查的值列表
            param_name: 参数名
            batch_size: 每批查询的数量

        Returns:
            字典，key为值，value为是否存在
        """
        result = {}

        # 批量处理
        for i in range(0, len(value_list), batch_size):
            batch = value_list[i:i + batch_size]
            placeholders = ",".join(["%s"] * len(batch))
            sql = sql_template.format(param_name=param_name, placeholders=placeholders)

            with self.cursor() as cursor:
                cursor.execute(sql, tuple(batch))
                existing = set(row[0] for row in cursor.fetchall())

                for v in batch:
                    result[v] = v in existing

        return result

    def dispose(self):
        """释放所有连接"""
        with self._lock:
            for item in self._pool:
                try:
                    item["conn"].close()
                except:
                    pass
            self._pool.clear()
            self._in_use.clear()


# 全局连接池实例
_pool_instance: Optional[MSSQLConnectionPool] = None
_pool_lock = threading.Lock()


def get_mssql_pool(config: MSSQLConfig = None) -> MSSQLConnectionPool:
    """
    获取MSSQL连接池单例

    Args:
        config: MSSQL配置，如果为None则从 env_loader 读取

    Returns:
        MSSQLConnectionPool实例
    """
    global _pool_instance

    if _pool_instance is None:
        with _pool_lock:
            if _pool_instance is None:
                if config is None:
                    # 从 env_loader 读取配置
                    config = MSSQLConfig.from_env()
                _pool_instance = MSSQLConnectionPool(config)
                # 同步连接池参数
                env = get_env_config()
                _pool_instance._pool_size = env.mssql_pool_size
                _pool_instance._max_overflow = env.mssql_max_overflow
                _pool_instance._pool_recycle = env.mssql_pool_recycle

    return _pool_instance


def init_mssql_pool(
    host: str = None,
    port: int = None,
    user: str = None,
    password: str = None,
    database: str = None,
    use_env: bool = True,
    **pool_kwargs
) -> MSSQLConnectionPool:
    """
    初始化MSSQL连接池

    Args:
        host: 服务器地址，None时从env读取
        port: 端口，None时从env读取
        user: 用户名，None时从env读取
        password: 密码，None时从env读取
        database: 数据库名，None时从env读取
        use_env: 是否使用env_loader中的连接池参数
        **pool_kwargs: 连接池其他参数（pool_size, max_overflow, pool_recycle等）

    Returns:
        MSSQLConnectionPool实例

    Usage:
        # 方式1: 完全从 .env 读取
        init_mssql_pool()

        # 方式2: 显式传入参数
        init_mssql_pool(host="...", port=1433, user="...", password="...", database="...")

        # 方式3: 部分参数从env读取
        init_mssql_pool(host="custom_host", use_env=False)
    """
    global _pool_instance

    env = get_env_config()

    # 合并参数：显式传入 > env
    final_host = host if host is not None else env.mssql_host
    final_port = port if port is not None else env.mssql_port
    final_user = user if user is not None else env.mssql_user
    final_password = password if password is not None else env.mssql_password
    final_database = database if database is not None else env.mssql_database

    config = MSSQLConfig(
        host=final_host,
        port=final_port,
        user=final_user,
        password=final_password,
        database=final_database
    )

    # 连接池参数
    if use_env:
        pool_kwargs.setdefault("pool_size", env.mssql_pool_size)
        pool_kwargs.setdefault("max_overflow", env.mssql_max_overflow)
        pool_kwargs.setdefault("pool_recycle", env.mssql_pool_recycle)

    with _pool_lock:
        if _pool_instance:
            _pool_instance.dispose()
        _pool_instance = MSSQLConnectionPool(config, **pool_kwargs)

    return _pool_instance


def close_mssql_pool():
    """关闭MSSQL连接池"""
    global _pool_instance
    with _pool_lock:
        if _pool_instance:
            _pool_instance.dispose()
            _pool_instance = None
