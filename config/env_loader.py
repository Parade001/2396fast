# -*- coding: utf-8 -*-
"""
环境变量加载器

统一管理 .env 文件的加载和环境变量读取
"""

import os
from pathlib import Path
from typing import Optional, Any, Union
from dataclasses import dataclass

try:
    from dotenv import load_dotenv, find_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False
    print("警告: 未安装 python-dotenv, 请运行: pip install python-dotenv")


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
# print(PROJECT_ROOT)

def load_env(env_file: Optional[str] = None) -> bool:
    """
    加载 .env 文件
    
    Args:
        env_file: .env 文件路径，为None则自动查找项目根目录下的 .env
    
    Returns:
        是否加载成功
    """
    if not DOTENV_AVAILABLE:
        return False
    
    if env_file is None:
        # 自动查找项目根目录下的 .env
        env_file = PROJECT_ROOT / ".env"
    
    env_path = Path(env_file)
    
    if not env_path.exists():
        print(f"警告: .env 文件不存在: {env_path}")
        print(f"请参考 .env.example 创建 .env 文件")
        return False
    
    load_dotenv(env_path, override=False)
    return True


def get_env(
    key: str,
    default: Any = None,
    required: bool = False,
    env_type: type = str
) -> Any:
    """
    读取环境变量
    
    Args:
        key: 环境变量名
        default: 默认值
        required: 是否必填
        env_type: 类型转换函数
    
    Returns:
        环境变量值
    """
    value = os.getenv(key, default)
    
    if required and value is None:
        raise ValueError(f"环境变量 {key} 未设置")
    
    if value is None:
        return None
    
    # 类型转换
    if env_type == bool:
        return str(value).lower() in ('true', '1', 'yes', 'on')
    elif env_type == int:
        return int(value)
    elif env_type == float:
        return float(value)
    else:
        return str(value)


@dataclass
class EnvConfig:
    """
    环境变量配置
    
    自动从环境变量读取所有配置项
    """
    
    # ==================== HANA ====================
    @property
    def hana_host(self) -> str:
        return get_env("HANA_HOST", required=True)
    
    @property
    def hana_port(self) -> int:
        return get_env("HANA_PORT", default=30015, env_type=int)
    
    @property
    def hana_user(self) -> str:
        return get_env("HANA_USER", required=True)
    
    @property
    def hana_password(self) -> str:
        return get_env("HANA_PASSWORD", required=True)
    
    @property
    def hana_database(self) -> str:
        return get_env("HANA_DATABASE", default="SYSTEMDB")
    
    @property
    def hana_pool_size(self) -> int:
        return get_env("HANA_POOL_SIZE", default=5, env_type=int)
    
    @property
    def hana_max_overflow(self) -> int:
        return get_env("HANA_MAX_OVERFLOW", default=10, env_type=int)
    
    @property
    def hana_pool_recycle(self) -> int:
        return get_env("HANA_POOL_RECYCLE", default=3600, env_type=int)
    
    # ==================== MSSQL ====================
    @property
    def mssql_host(self) -> str:
        return get_env("MSSQL_HOST", required=True)
    
    @property
    def mssql_port(self) -> int:
        return get_env("MSSQL_PORT", default=1433, env_type=int)
    
    @property
    def mssql_user(self) -> str:
        return get_env("MSSQL_USER", required=True)
    
    @property
    def mssql_password(self) -> str:
        return get_env("MSSQL_PASSWORD", required=True)
    
    @property
    def mssql_database(self) -> str:
        return get_env("MSSQL_DATABASE", required=True)
    
    @property
    def mssql_pool_size(self) -> int:
        return get_env("MSSQL_POOL_SIZE", default=5, env_type=int)
    
    @property
    def mssql_max_overflow(self) -> int:
        return get_env("MSSQL_MAX_OVERFLOW", default=10, env_type=int)
    
    @property
    def mssql_pool_recycle(self) -> int:
        return get_env("MSSQL_POOL_RECYCLE", default=3600, env_type=int)
          
    @property
    def output_path(self) -> str:
        return get_env("OUTPUT_PATH", default="output/processed_data.xlsx")
    
    @property
    def output_sheet_name(self) -> str:
        return get_env("OUTPUT_SHEET_NAME", default="数据")
    
    # ==================== 便捷方法 ====================
    def to_hana_config(self) -> dict:
        """获取HANA连接配置"""
        return {
            "host": self.hana_host,
            "port": self.hana_port,
            "user": self.hana_user,
            "password": self.hana_password,
            "database": self.hana_database,
        }
    
    def to_hana_pool_kwargs(self) -> dict:
        """获取HANA连接池参数"""
        return {
            "pool_size": self.hana_pool_size,
            "max_overflow": self.hana_max_overflow,
            "pool_recycle": self.hana_pool_recycle,
        }
    
    def to_mssql_config(self) -> dict:
        """获取MSSQL连接配置"""
        return {
            "host": self.mssql_host,
            "port": self.mssql_port,
            "user": self.mssql_user,
            "password": self.mssql_password,
            "database": self.mssql_database,
        }
    
    def to_mssql_pool_kwargs(self) -> dict:
        """获取MSSQL连接池参数"""
        return {
            "pool_size": self.mssql_pool_size,
            "max_overflow": self.mssql_max_overflow,
            "pool_recycle": self.mssql_pool_recycle,
        }


# 全局单例
_config_instance: Optional[EnvConfig] = None


def get_env_config() -> EnvConfig:
    """
    获取环境配置单例
    
    首次调用时自动加载 .env 文件
    """
    global _config_instance
    
    if _config_instance is None:
        load_env()
        _config_instance = EnvConfig()
    
    return _config_instance


def reset_config():
    """重置配置缓存（用于测试或重新加载）"""
    global _config_instance
    _config_instance = None


# ==================== 使用示例 ====================

if __name__ == "__main__":
    # 加载 .env
    print("加载 .env 文件...")
    if load_env():
        print("✓ .env 文件加载成功\n")
    
    # 读取配置
    config = get_env_config()
    
    print("=" * 50)
    print("HANA 配置:")
    print(f"  Host:     {config.hana_host}")
    print(f"  Port:     {config.hana_port}")
    print(f"  User:     {config.hana_user}")
    print(f"  Database: {config.hana_database}")
    print(f"  Pool Size: {config.hana_pool_size}")
    
    print("\nMSSQL 配置:")
    print(f"  Host:     {config.mssql_host}")
    print(f"  Port:     {config.mssql_port}")
    print(f"  User:     {config.mssql_user}")
    print(f"  Database: {config.mssql_database}")
    print(f"  Pool Size: {config.mssql_pool_size}")
    
    print("\n业务配置:")
    print(f"  HANA Table: {config.hana_table}")
    print(f"  MSSQL Table: {config.mssql_table}")
    print(f"  Date Range: {config.start_date} ~ {config.end_date}")
    print(f"  Output: {config.output_path}")
