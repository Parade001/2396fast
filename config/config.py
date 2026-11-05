# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: config/config.py
模块定位: SAP2FAST 映射配置 (account.yaml)、登录凭据 (fast_credentials.yaml) 与转换流水线参数配置类
================================================================================
【本文件核心作用】
1. `Config` / `AccountConfig`：加载 `config/account.yaml` 中的普通科目 (`account`)、功能范围科目 (`fkber`)、
   供应商 (`vendor`)、客户 (`customer`) 及 Chrome 自动化配置 (`chrome`)，同时支持合并读取
   `config/fast_credentials.yaml` 与 `.env` 中的 FAST 登录账号密码与语言配置；
2. `StatusConfig`：管理 SAP 增量抽取期间与录入日期 (`cpudt`) 游标状态；
3. `ProcessConfig`：定义多借多贷复合凭证拆分规则参数、特别科目配对表、VND 原币置零开关等。
================================================================================
"""
import os
import yaml
from typing import Dict, Any, List, Callable, Optional, Tuple
from dataclasses import dataclass, field
import pandas as pd


class Config:
    """加载并提供 config/account.yaml 与 config/fast_credentials.yaml 配置的封装类。"""
    def __init__(self, config_path: str = None):
        if config_path is None:
            self.config_path = os.path.join(os.path.dirname(__file__), 'account.yaml')
        else:
            self.config_path = config_path
        self.cred_path = os.path.join(os.path.dirname(self.config_path), 'fast_credentials.yaml')
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        return data

    def get_chrome_config(self) -> Dict[str, Any]:
        """
        获取 Chrome CDP 与越南 FAST 登录凭据配置。
        优先级：环境变量 (.env) > config/fast_credentials.yaml > config/account.yaml (chrome 节点)
        """
        chrome_cfg = (self.config.get("chrome") or {}).copy()
        cred_cfg: Dict[str, Any] = {}
        if os.path.exists(self.cred_path):
            try:
                with open(self.cred_path, 'r', encoding='utf-8') as f:
                    cred_cfg = yaml.safe_load(f) or {}
            except Exception:
                cred_cfg = {}

        fast_auth = cred_cfg.get("fast_auth") or {}
        chrome_cdp = cred_cfg.get("chrome_cdp") or {}

        default_chrome = os.getenv(
            "CHROME_PATH",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            if os.name == "nt"
            else "/ms-playwright/chrome-headless-shell-linux64/chrome-headless-shell"
        )
        return {
            "chrome_path": os.getenv("CHROME_PATH") or chrome_cdp.get("chrome_path") or chrome_cfg.get("chrome_path") or default_chrome,
            "remote_debugging_port": int(
                os.getenv("CHROME_PORT")
                or chrome_cdp.get("remote_debugging_port")
                or chrome_cfg.get("remote_debugging_port")
                or 9222
            ),
            "max_retries": int(
                chrome_cdp.get("max_retries")
                or chrome_cfg.get("max_retries")
                or 3
            ),
            "close_browser_on_exit": bool(
                chrome_cdp.get("close_browser_on_exit", chrome_cfg.get("close_browser_on_exit", True))
            ),
            "fast_url": (
                os.getenv("FAST_URL")
                or fast_auth.get("login_url")
                or chrome_cfg.get("fast_url")
                or "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"
            ),
            "fast_user": (
                os.getenv("FAST_USER")
                or fast_auth.get("username")
                or chrome_cfg.get("fast_user")
                or ""
            ),
            "fast_password": (
                os.getenv("FAST_PASSWORD")
                or fast_auth.get("password")
                or chrome_cfg.get("fast_password")
                or ""
            ),
            "fast_unit": (
                os.getenv("FAST_UNIT")
                or fast_auth.get("unit")
                or chrome_cfg.get("fast_unit")
                or "CTY"
            ),
            "fast_language": (
                os.getenv("FAST_LANGUAGE")
                or fast_auth.get("language")
                or chrome_cfg.get("fast_language")
                or "en"
            ),
        }


# 兼容 `from config.config import AccountConfig` 调用方式
AccountConfig = Config


@dataclass
class StatusConfig:
    """数据状态类。"""
    earliest_cpudt: str = ''
    latest_cpudt: str = ''
    current_cupdt: str = ''
    current_period: dict = field(default_factory=dict)

    def next_cpudt(self) -> bool:
        """cpudt格式是yyyymmdd的字符串日期格式，返回下一个日期的cpudt。"""
        if len(self.current_cupdt) == 0:
            return False
        import arrow
        self.current_cupdt = arrow.get(self.current_cupdt, 'YYYYMMDD').shift(days=1).format('YYYYMMDD')
        return self.current_cupdt <= self.latest_cpudt

    def get_kwargs(self) -> dict:
        self.current_period.update({"cpudt": self.current_cupdt})
        return self.current_period


@dataclass
class ProcessConfig:
    """处理配置。"""

    # 多借多贷分组的最小方向数量（> N 才视为多借多贷），默认 1
    # 即 H>1 且 S>1 时触发（同一 voucher 至少 2 行 H + 2 行 S）
    multi_min_per_side: int = 1
    # 分组号起始值
    group_id_start: int = 1
    # 是否对 VND 强制 original_amount = 0
    vnd_zero_original: bool = True
    # USD 校验容差（浮点比较）
    usd_tolerance: float = 1e-6
    # 特别科目组: [(acc_h, acc_s), ...] 允许规则 3 拆分的科目代码配对
    # None 表示任意同科目 H/S 都允许 (默认行为)
    special_account_pairs: Optional[List[Tuple[str, str]]] = None
    # 多借多贷规则 1-3 整体的最大递归轮数 (初始 1 轮 + 递归 N 轮 = N+1 轮)
    max_recursion: int = 10
    # 规则 2 配对允许的最多子集大小 (1借N贷 / N借1贷 中的 N 上限)
    rule2_max_subset: int = 3


AccountConfig = Config()