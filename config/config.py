import os
import yaml
from typing import Dict, Any, List, Callable, Optional, Tuple
from dataclasses import dataclass, field
import pandas as pd


class Config:
    def __init__(self, config_path: str = None):
        if config_path is None:
            self.config_path = os.path.join(os.path.dirname(__file__), 'account.yaml')
        self.config = self._load_config()
 

    def _load_config(self) -> Dict[str, Any]:
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

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
        self.current_period.update({"cpudt":self.current_cupdt})
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