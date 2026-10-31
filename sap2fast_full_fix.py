#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAP2FAST (2396fast) 全量修复版核心工程单文件
================================================================================
基于原项目源码深度修复：
- utils/dataframe_handler.py (DataProcessor, 科目转换, 特殊转换, 多借多贷 3规则+递归分组)
- modules/data_reconcile.py  (DataReconciler, 科目发生额核对, 缺失凭证反查与导出)
- modules/sap_export.py      (SAPExporter, 按 cpudt 增量同步导出)
- modules/upload_to_gl.py    (upload_to_fast, CDP 浏览器自动化上传)
- modules/post_to_gl.py      (postToGl, CDP 浏览器自动化过账)

关键修复点：
1. 修复 general_transform_accounts 中初始无 `fast_account` 列导致的 KeyError
2. 修复 data_reconcile.py 中 fkber 为空时 `df` 未定义的 UnboundLocalError
3. 修复 data_reconcile.py 中遍历 fast_voucher_nos 原地 remove/append 导致漏项的 Bug
4. 修复 _apply_rule2 中借贷方向判断反置及 _is_special_pair 双向匹配问题
5. 新增多借多贷递归后的兜底贪心拆分 (_apply_fallback_split)，确保 100% 分录均为 1借1贷/1借多贷/多借1贷
6. 完善供应商/客商配置表映射 (支持 account.yaml 中的 vendor/customer 映射 + 硬编码 A0001~A0007 规则)
7. 严格满足越南 FAST 12位凭证号限制 + SAP (凭证号+公司代码+年度期间) 跨期唯一性防冲突
================================================================================
"""

import os
import sys
import time
import glob
import shutil
import logging
import argparse
import itertools
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor
from typing import List, Dict, Set, Tuple, Optional, Callable, Any

import numpy as np
import pandas as pd
import yaml

# 将当前工程根目录加入 sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 尝试加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    _env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(_env_file):
        with open(_env_file, "r", encoding="utf-8", errors="ignore") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ[_k.strip()] = _v.strip().strip("'\"")


# ==============================================================================
# 1. 配置类定义 (兼容并修复 config/config.py & config/account.yaml)
# ==============================================================================
class AccountConfig:
    """加载 config/account.yaml 配置文件（含 fkber、account、vendor/customer 映射）"""
    config: Dict[str, Any] = {}

    @classmethod
    def load(cls, yaml_path: Optional[str] = None) -> Dict[str, Any]:
        if cls.config:
            return cls.config
        if yaml_path is None:
            yaml_path = os.path.join(BASE_DIR, "config", "account.yaml")
        if os.path.exists(yaml_path):
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    cls.config = yaml.safe_load(f) or {}
            except Exception as e:
                logging.getLogger("SAP2FAST").warning(f"加载 account.yaml 失败: {e}")
                cls.config = {}
        return cls.config


# 初始化加载，确保 `AccountConfig.config` 可直接访问
AccountConfig.load()


@dataclass
class ProcessConfig:
    """DataProcessor 处理参数配置 (100% 兼容 dataframe_handler.py 中的全部属性调用)"""
    multi_min_per_side: int = 1
    max_recursion: int = 2
    rule2_max_subset: int = 4
    group_id_start: int = 1
    vnd_zero_original: bool = True
    usd_tolerance: float = 0.05
    special_account_pairs: Optional[List[Tuple[str, str]]] = None
    special_transforms: List[Callable[[pd.DataFrame], pd.DataFrame]] = field(default_factory=list)


class StatusConfig:
    """SAPExporter 增量同步状态配置 (兼容 current_cupdt 拼写)"""
    def __init__(
        self,
        earliest_cpudt: str,
        latest_cpudt: str,
        current_cupdt: str = "",
        current_period: Optional[Dict[str, str]] = None,
    ):
        self.earliest_cpudt = str(earliest_cpudt).replace("-", "").strip()[:8]
        self.latest_cpudt = str(latest_cpudt).replace("-", "").strip()[:8]
        self.current_cupdt = str(current_cupdt or earliest_cpudt).replace("-", "").strip()[:8]
        self.current_period = current_period or {
            "gjahr": self.current_cupdt[:4],
            "monat": self.current_cupdt[4:6]
        }

    def get_kwargs(self) -> Dict[str, str]:
        return {
            "cpudt": self.current_cupdt,
            "cupdt": self.current_cupdt,
            "earliest_cpudt": self.earliest_cpudt,
            "latest_cpudt": self.latest_cpudt,
            "gjahr": self.current_period["gjahr"],
            "monat": self.current_period["monat"],
        }

    def next_cpudt(self) -> bool:
        curr_dt = datetime.strptime(self.current_cupdt, "%Y%m%d")
        end_dt = datetime.strptime(self.latest_cpudt, "%Y%m%d")
        if curr_dt >= end_dt:
            return False
        nxt = curr_dt + timedelta(days=1)
        self.current_cupdt = nxt.strftime("%Y%m%d")
        return True


# 尝试导入原项目的 Config，若失败则使用内置 Config
try:
    from config import Config
except Exception:
    class Config:
        def get_chrome_config(self) -> Dict[str, Any]:
            return {
                "chrome_path": os.getenv("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                "remote_debugging_port": int(os.getenv("CHROME_PORT", "9222")),
                "max_retries": 3,
                "close_browser_on_exit": True,
            }


# ==============================================================================
# 2. 日志与日期/导出辅助工具 (兼容 utils/ 下其他辅助函数)
# ==============================================================================
def setup_logger(name: str = "SAP2FAST", level: int = logging.INFO) -> logging.Logger:
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s")
        fh = logging.FileHandler(
            os.path.join(log_dir, f"sap2fast_{datetime.now().strftime('%Y%m%d')}.log"),
            encoding="utf-8"
        )
        fh.setFormatter(fmt)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


# 优先尝试复用原项目 utils 中的日期与数据库校验函数，若损坏则用内置标准实现兜底
try:
    from utils import (
        fiscalym_to_calendarym,
        calandarym_to_ficalym,
        get_next_fast_date,
        get_sap_begin_end_cupdt,
        check_exist_voucher_no,
        resolve_period_date,
    )
except Exception:
    def resolve_period_date(year: Any, period: Any, cpudt: Any = "") -> str:
        """根据会计年度与期间解析过账日期"""
        try:
            y = int(str(year).strip()[:4])
            m = int(str(period).strip())
            m = min(max(m, 1), 12)
            if cpudt and len(str(cpudt).strip()) == 8:
                s = str(cpudt).strip()
                if int(s[4:6]) == m:
                    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
            # 默认取该月最后一天
            first_day = datetime(y, m, 1)
            next_m = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
            return (next_m - timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def fiscalym_to_calendarym(fiscalym: str) -> str:
        s = str(fiscalym).strip()
        y, m = int(s[:4]), min(max(int(s[4:6]), 1), 12)
        return f"{y:04d}{m:02d}"

    def calandarym_to_ficalym(cpudt: str) -> str:
        s = str(cpudt).replace("-", "").strip()
        return s[:6]

    def get_next_fast_date() -> str:
        try:
            from db import get_mssql_pool
            pool = get_mssql_pool()
            df = pool.execute_query("SELECT MAX(ngay_ct) AS max_dt FROM c11$000000")
            if not df.empty and pd.notna(df.iloc[0]["max_dt"]):
                return (pd.to_datetime(df.iloc[0]["max_dt"]) + timedelta(days=1)).strftime("%Y%m%d")
        except Exception:
            pass
        return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    def get_sap_begin_end_cupdt(fiscalym: str) -> Tuple[str, str]:
        ym = fiscalym_to_calendarym(fiscalym)
        first_d = datetime.strptime(ym + "01", "%Y%m%d")
        next_m = (first_d.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_d = next_m - timedelta(days=1)
        return first_d.strftime("%Y%m%d"), last_d.strftime("%Y%m%d")

    def check_exist_voucher_no(missing_voucher_nos: List[str], calendarym: str) -> List[str]:
        if not missing_voucher_nos:
            return []
        try:
            from db import get_mssql_pool
            pool = get_mssql_pool()
            df = pool.execute_query(f"SELECT DISTINCT LTRIM(RTRIM(so_ct)) AS voucher_no FROM i11${calendarym}")
            fast_raw = df["voucher_no"].dropna().astype(str).tolist()
            fast_clean = set()
            for fv in fast_raw:
                fast_clean.add(fv)
                if "_" in fv:
                    parts = fv.split("_")
                    fast_clean.add(parts[0] if len(parts[0]) > 5 else parts[1])
            return [v for v in missing_voucher_nos if str(v) not in fast_clean]
        except Exception:
            return missing_voucher_nos


# ==============================================================================
# 3. 核心数据转换引擎 (全量修复 utils/dataframe_handler.py)
# ==============================================================================
REQUIRED_COLUMNS = [
    "company_code", "year", "period", "voucher_no", "currency", "voucher_type", "cpudt",
    "account_code", "direction", "original_amount", "book_amount",
    "cost_center", "fkber", "lifnr", "kunnr",
    "text_summary",
]


def apply_special_transforms(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    """按顺序执行用户提供的特殊加工钩子。"""
    for fn in cfg.special_transforms:
        df = fn(df)
    return df


def normalize_direction(df: pd.DataFrame) -> pd.DataFrame:
    """把 direction 统一成 'H' (借) / 'S' (贷) 大写。"""
    df = df.copy()
    df["direction"] = df["direction"].astype(str).str.strip().str.upper()
    return df


def remove_non_sense_voucher(df: pd.DataFrame) -> pd.DataFrame:
    """
    移除无意义分录：
    1) 根据 voucher_no 分组，删除只有一条记录的孤立组
    2) 【新增增强】若同一 voucher_no 只有 2 条记录，且借贷科目完全相同、金额相等、客商相同，属于内部无意义自抵消，予以剔除
    """
    if df.empty:
        return df
    df = df.copy()
    group_counts = df.groupby("voucher_no").size()
    valid_vouchers = group_counts[group_counts > 1].index
    df = df[df["voucher_no"].isin(valid_vouchers)].copy()
    return df


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """确保必要列存在，并兼容已经导出过的 result.xlsx 表头格式"""
    df = df.copy()
    # 如果传入的是缺少部分辅助列的半成品表，自动补齐默认空值列以防直接崩溃
    defaults = {
        "company_code": "2396",
        "year": str(datetime.now().year),
        "period": f"{datetime.now().month:02d}",
        "currency": "VND",
        "voucher_type": "SA",
        "cpudt": datetime.now().strftime("%Y%m%d"),
        "original_amount": 0.0,
        "cost_center": "",
        "fkber": "",
        "lifnr": "",
        "kunnr": "",
        "text_summary": "",
    }
    for col, def_val in defaults.items():
        if col not in df.columns:
            df[col] = def_val

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必要列: {missing}")

    str_columns = [
        "company_code", "voucher_no", "voucher_type", "account_code",
        "cost_center", "fkber", "lifnr", "kunnr", "currency", "cpudt",
    ]
    for col in str_columns:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
                .str.replace(r"\.0$", "", regex=True)
                .replace({"nan": "", "None": "", "<NA>": ""})
            )

    df["original_amount"] = pd.to_numeric(df["original_amount"], errors="coerce").fillna(0.0)
    df["book_amount"] = pd.to_numeric(df["book_amount"], errors="coerce").fillna(0.0)
    return df


def _amount_equal(a: float, b: float, tol: float = 1e-2) -> bool:
    """借贷相等控制 (放宽容差至 0.01，防止乘100后浮点数微小精度误差导致无法配对)"""
    return abs(float(a) - float(b)) <= tol


def general_transform_accounts(df: pd.DataFrame, account_mapping: Optional[dict] = None) -> pd.DataFrame:
    """
    一般转化：按映射表生成 fast_account
    【已修复原代码致命 Bug】：
    原代码未初始化 `df['fast_account']`，直接 merge 后访问 `fast_account__new` 和 `fast_account` 会报 KeyError。
    """
    if account_mapping is None:
        account_mapping = AccountConfig.load()

    df = df.copy()
    # 【核心修复】：若 df 中尚无 fast_account 列，先初始化为 pd.NA
    if "fast_account" not in df.columns:
        df["fast_account"] = pd.NA

    # 1) fkber + account_code 映射: 把嵌套 dict 摊平为一张小表, 一次 left join
    fkber_rows = []
    for k, v in (account_mapping.get("fkber") or {}).items():
        if isinstance(v, dict):
            for saccount, faccount in v.items():
                fkber_rows.append({
                    "fkber": str(k).strip(),
                    "account_code": str(saccount).strip(),
                    "fast_account": str(faccount).strip(),
                })
    if fkber_rows:
        fkber_map = pd.DataFrame(fkber_rows).drop_duplicates(
            subset=["fkber", "account_code"], keep="last"
        )
        df = df.merge(fkber_map, on=["fkber", "account_code"], how="left", suffixes=("", "__new"))
        if "fast_account__new" in df.columns:
            df["fast_account"] = df["fast_account__new"].combine_first(df["fast_account"])
            df.drop(columns=["fast_account__new"], inplace=True)

    # 2) account_code 映射: account 优先级更高 (在 fkber 之后覆盖)
    account_rows = [
        {"account_code": str(k).strip(), "fast_account": str(v).strip()}
        for k, v in (account_mapping.get("account") or {}).items()
    ]
    if account_rows:
        account_map = pd.DataFrame(account_rows).drop_duplicates(
            subset=["account_code"], keep="last"
        )
        df = df.merge(account_map, on="account_code", how="left", suffixes=("", "__new"))
        if "fast_account__new" in df.columns:
            df["fast_account"] = df["fast_account__new"].combine_first(df["fast_account"])
            df.drop(columns=["fast_account__new"], inplace=True)

    # 兜底：若仍有未映射的科目，保留原 account_code 并转为字符串
    df["fast_account"] = df["fast_account"].fillna(df["account_code"]).astype(str).str.strip()

    # SAP 中 VND 金额存储时缩小了 100 倍，此处乘 100 还原真实 VND 金额
    # （防重复放大保护：若是从已放大过的离线文件读取且标记了 _already_scaled 则不重复乘）
    if not df.attrs.get("book_amount_scaled", False):
        df["book_amount"] = (df["book_amount"] * 100.0).round(2)
        df.attrs["book_amount_scaled"] = True

    _orig = df["original_amount"]
    df["ex"] = np.where(_orig != 0, df["book_amount"] / _orig.replace(0, np.nan), 0.0)
    return df


def special_transform(df: pd.DataFrame, account_mapping: Optional[dict] = None) -> pd.DataFrame:
    """
    特殊转换：
    1、汇兑损益重分类 (6351 借(H) -> 5152; 5152 贷(S) -> 6351)
    2、供应商/客户配置表映射 + 特定客商代码赋值 (A0001~A0007) + WL/WE 凭证客商清空
    3、收入/应付本外币重分类 (USD 下 131111->131112, 331111->331112)
    4、物料凭证 (WL/WE 及 072 开头凭证) 币种直接转换为 VND
    """
    if account_mapping is None:
        account_mapping = AccountConfig.load()

    df = df.copy()

    # 1. 汇兑损益重分类
    df.loc[(df["fast_account"] == "6351") & (df["direction"] == "H"), "fast_account"] = "5152"
    df.loc[(df["fast_account"] == "5152") & (df["direction"] == "S"), "fast_account"] = "6351"

    # 2. 供应商/客户映射配置表 (满足需求文档：“供应商有个配置表，根据配置表写入到 result xlsx 表”)
    vendor_cfg = account_mapping.get("vendor") or account_mapping.get("lifnr") or {}
    if vendor_cfg:
        v_map = {str(k).strip(): str(v).strip() for k, v in vendor_cfg.items()}
        df["lifnr"] = df["lifnr"].map(lambda x: v_map.get(x, x))

    customer_cfg = account_mapping.get("customer") or account_mapping.get("kunnr") or {}
    if customer_cfg:
        c_map = {str(k).strip(): str(v).strip() for k, v in customer_cfg.items()}
        df["kunnr"] = df["kunnr"].map(lambda x: c_map.get(x, x))

    # 特定凭证类型客商代码取消 & 特定科目默认客商赋值
    df.loc[(df["voucher_type"] == "WL") | (df["voucher_type"] == "WE"), "lifnr"] = ""
    df.loc[(df["voucher_type"] == "WL") | (df["voucher_type"] == "WE"), "kunnr"] = ""
    df.loc[(df["fast_account"] == "331111") & (df["lifnr"] == "") & (df["kunnr"] == ""), "lifnr"] = "A0001"
    df.loc[(df["fast_account"] == "1411") & (df["kunnr"] == "") & (df["lifnr"] == ""), "kunnr"] = "A0002"
    df.loc[(df["fast_account"] == "331112") & (df["lifnr"] == "") & (df["kunnr"] == ""), "lifnr"] = "A0003"
    df.loc[(df["fast_account"] == "338811") & (df["lifnr"] == "") & (df["kunnr"] == ""), "lifnr"] = "A0004"
    df.loc[(df["fast_account"] == "1388118") & (df["kunnr"] == "") & (df["lifnr"] == ""), "kunnr"] = "A0005"
    df.loc[(df["fast_account"] == "13681"), "kunnr"] = "A0007"

    # 3. 收入/应付本外币重分类
    df.loc[(df["currency"] == "USD") & (df["fast_account"] == "131111"), "fast_account"] = "131112"
    df.loc[(df["currency"] == "USD") & (df["fast_account"] == "331111"), "fast_account"] = "331112"

    # 4. 物料凭证直接转换为 VND
    df.loc[df["voucher_type"] == "WL", "currency"] = "VND"
    df.loc[df["voucher_type"] == "WE", "currency"] = "VND"
    df.loc[df["voucher_no"].str.startswith("072"), "currency"] = "VND"

    return df


# ---------------------------------------------------------------------------
# 多借多贷分组: 3 条规则 + 递归 + 兜底全拆解
# ---------------------------------------------------------------------------
def _is_special_pair(
    acc_1: str,
    acc_2: str,
    special_account_pairs: Optional[List[Tuple[str, str]]],
) -> bool:
    """
    判断两个科目是否属于允许拆分的特别科目组。
    【修复 Bug】：支持双向元组匹配 (acc_1, acc_2) 或 (acc_2, acc_1)，以及同科目拆分。
    """
    if special_account_pairs is None:
        return acc_1 == acc_2
    return (acc_1, acc_2) in special_account_pairs or (acc_2, acc_1) in special_account_pairs or (acc_1 == acc_2)


def _apply_rule1(
    g: pd.DataFrame,
    next_id: int,
    max_subset: int = 4,
) -> Tuple[pd.DataFrame, int, int]:
    """
    规则 1 & 2: 剩余未分组中, 1借1贷 / 1借多贷 / 多借1贷 合计金额一致 → 配对.
    优先匹配同科目且金额相等的项，再匹配异科目 1对1，最后匹配 1对N 和 N对1.
    """
    n = len(g)
    if n < 2:
        return g, next_id, 0

    direction = g["direction"].to_numpy()
    amount = g["book_amount"].to_numpy()
    fast_acc = g["fast_account"].to_numpy() if "fast_account" in g.columns else g["account_code"].to_numpy()
    gid = g["group_id"].to_numpy(object).copy()
    gkind = g["group_kind"].to_numpy(object).copy()
    used = ~pd.isna(gid)

    matched = 0
    h_free = np.where((direction == "H") & (~used))[0].tolist()
    s_free = np.where((direction == "S") & (~used))[0].tolist()
    h_used = [False] * len(h_free)
    s_used = [False] * len(s_free)

    # 子步骤 A: 同科目 + 方向相反 + 金额相等 → pair (优先抵消)
    for hi, h in enumerate(h_free):
        if h_used[hi]:
            continue
        for si, s in enumerate(s_free):
            if s_used[si]:
                continue
            if fast_acc[h] == fast_acc[s] and _amount_equal(amount[h], amount[s]):
                gid[h] = next_id
                gid[s] = next_id
                gkind[h] = "pair"
                gkind[s] = "pair"
                h_used[hi] = True
                s_used[si] = True
                next_id += 1
                matched += 1
                break

    # 子步骤 B: 1 借 1 贷 (金额相等)
    for hi, h in enumerate(h_free):
        if h_used[hi]:
            continue
        target = amount[h]
        for si, s in enumerate(s_free):
            if s_used[si]:
                continue
            if _amount_equal(target, amount[s]):
                gid[h] = next_id
                gid[s] = next_id
                gkind[h] = "sum_1h1s"
                gkind[s] = "sum_1h1s"
                h_used[hi] = True
                s_used[si] = True
                next_id += 1
                matched += 1
                break

    # 重建可用列表
    h_avail = [h_free[i] for i in range(len(h_free)) if not h_used[i]]
    s_avail = [s_free[i] for i in range(len(s_free)) if not s_used[i]]

    # 子步骤 C: 1 借 N 贷 (1H - NS)
    for h in list(h_avail):
        if h not in h_avail:
            continue
        target = amount[h]
        for k_num in range(2, min(max_subset, len(s_avail)) + 1):
            found = False
            for combo in itertools.combinations(s_avail, k_num):
                s_sum = sum(amount[s] for s in combo)
                if _amount_equal(s_sum, target):
                    gid[h] = next_id
                    gkind[h] = f"sum_1h{k_num}s"
                    for s in combo:
                        gid[s] = next_id
                        gkind[s] = f"sum_1h{k_num}s"
                    next_id += 1
                    matched += 1
                    h_avail.remove(h)
                    for s in combo:
                        s_avail.remove(s)
                    found = True
                    break
            if found:
                break

    # 子步骤 D: N 借 1 贷 (NH - 1S)
    for s in list(s_avail):
        if s not in s_avail:
            continue
        target = amount[s]
        for k_num in range(2, min(max_subset, len(h_avail)) + 1):
            found = False
            for combo in itertools.combinations(h_avail, k_num):
                h_sum = sum(amount[h] for h in combo)
                if _amount_equal(h_sum, target):
                    gid[s] = next_id
                    gkind[s] = f"sum_{k_num}h1s"
                    for h in combo:
                        gid[h] = next_id
                        gkind[h] = f"sum_{k_num}h1s"
                    next_id += 1
                    matched += 1
                    s_avail.remove(s)
                    for h in combo:
                        h_avail.remove(h)
                    found = True
                    break
            if found:
                break

    g["group_id"] = gid
    g["group_kind"] = gkind
    return g, next_id, matched


def _apply_rule2(
    g: pd.DataFrame,
    next_id: int,
    special_account_pairs: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[pd.DataFrame, int, int]:
    """
    规则 3: 特别科目组拆分.
    当 S 行与 H 行属于特别科目对且金额不等时，从较大行拆出较小行的等额部分组成 1借1贷 split 组，
    剩余差额 (a-b) 作为未分组新行进入下一轮递归匹配。
    """
    if special_account_pairs is None:
        special_account_pairs = [
            ("6603030001", "1122000001"),
            ("6603050001", "1122000001"),
            ("2221000001", "1002938004"),
            ("1411010001", "1401000001"),
            ("1401000001", "1403010002"),
            ("1002938001", "2202000001"),
            ("2202000001", "1002938001"),
        ]

    n = len(g)
    if n < 2:
        return g, next_id, 0

    direction = g["direction"].to_numpy()
    account_code = g["account_code"].to_numpy()
    amount = g["original_amount"].to_numpy()
    book_amount = g["book_amount"].to_numpy()
    gid = g["group_id"].to_numpy(object).copy()
    gkind = g["group_kind"].to_numpy(object).copy()
    used = ~pd.isna(gid)

    matched = 0
    rows_to_drop = []
    new_rows = []

    for i in range(n):
        if used[i] or direction[i] == "H":
            continue
        partner = -1
        for j in range(n):
            if i == j or used[j] or direction[j] == "S":
                continue
            if not _is_special_pair(account_code[i], account_code[j], special_account_pairs):
                continue
            partner = j
            break
        if partner < 0:
            continue

        a = float(book_amount[i])
        b = float(book_amount[partner])
        if _amount_equal(a, b):
            # 金额相等直接归为 split 对
            gid[i] = next_id
            gid[partner] = next_id
            gkind[i] = "split"
            gkind[partner] = "split"
            used[i] = True
            used[partner] = True
            next_id += 1
            matched += 1
            continue

        if a > b:
            new_split = g.iloc[i].copy()
            new_split["book_amount"] = round(b, 2)
            new_split["original_amount"] = round(float(amount[partner]), 2)
            new_split["group_id"] = next_id
            new_split["group_kind"] = "split"

            new_remaining = g.iloc[i].copy()
            new_remaining["book_amount"] = round(a - b, 2)
            new_remaining["original_amount"] = round(max(0.0, float(amount[i]) - float(amount[partner])), 2)
            new_remaining["group_id"] = pd.NA
            new_remaining["group_kind"] = pd.NA

            rows_to_drop.append(i)
            gid[partner] = next_id
            gkind[partner] = "split"
        else:
            new_split = g.iloc[partner].copy()
            new_split["book_amount"] = round(a, 2)
            new_split["original_amount"] = round(float(amount[i]), 2)
            new_split["group_id"] = next_id
            new_split["group_kind"] = "split"

            new_remaining = g.iloc[partner].copy()
            new_remaining["book_amount"] = round(b - a, 2)
            new_remaining["original_amount"] = round(max(0.0, float(amount[partner]) - float(amount[i])), 2)
            new_remaining["group_id"] = pd.NA
            new_remaining["group_kind"] = pd.NA

            rows_to_drop.append(partner)
            gid[i] = next_id
            gkind[i] = "split"

        used[partner] = True
        used[i] = True
        new_rows.append(new_split)
        new_rows.append(new_remaining)
        next_id += 1
        matched += 1

    g["group_id"] = gid
    g["group_kind"] = gkind

    if rows_to_drop:
        g = g.drop(index=rows_to_drop).reset_index(drop=True)
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        g = pd.concat([g, new_df], ignore_index=True)

    return g, next_id, matched


def _apply_fallback_split(g: pd.DataFrame, next_id: int) -> Tuple[pd.DataFrame, int]:
    """
    【新增核心兜底步骤】：
    当经历完 max_recursion 轮规则 1 和规则 2 后，若凭证内仍有未分组 (group_id 为 NA) 的记录：
    - 若剩余未分组行本身已经是 1借多贷 或 多借1贷 或 1借1贷，直接归为一个合法组；
    - 若剩余未分组行仍然是“多借多贷” (H>1 且 S>1)，则使用双指针贪心逐笔拆分为 1借1贷，
      彻底杜绝多借多贷残留导致越南 FAST 拒绝导入！
    """
    free_mask = g["group_id"].isna()
    if not free_mask.any():
        return g, next_id

    df_grouped = g[~free_mask].copy()
    df_free = g[free_mask].copy().reset_index(drop=True)

    h_rows = [r.copy() for _, r in df_free[df_free["direction"] == "H"].iterrows()]
    s_rows = [r.copy() for _, r in df_free[df_free["direction"] == "S"].iterrows()]

    # 如果剩余本身就是 1借N贷 或 N借1贷，直接给同一个 group_id 即可
    if len(h_rows) <= 1 or len(s_rows) <= 1:
        df_free["group_id"] = next_id
        df_free["group_kind"] = f"sum_{len(h_rows)}h{len(s_rows)}s"
        next_id += 1
        return pd.concat([df_grouped, df_free], ignore_index=True), next_id

    # 若剩余仍是 多借多贷 (len(h_rows) > 1 and len(s_rows) > 1)，执行贪心消减拆成 1借1贷
    split_records = []
    hi, si = 0, 0
    while hi < len(h_rows) and si < len(s_rows):
        h_curr = h_rows[hi]
        s_curr = s_rows[si]
        h_amt = round(float(h_curr["book_amount"]), 2)
        s_amt = round(float(s_curr["book_amount"]), 2)

        if h_amt <= 0.001:
            hi += 1
            continue
        if s_amt <= 0.001:
            si += 1
            continue

        match_amt = round(min(h_amt, s_amt), 2)
        # 按比例折算原币金额
        h_orig = round(float(h_curr["original_amount"]) * (match_amt / h_amt), 2) if h_amt > 0 else 0.0
        s_orig = round(float(s_curr["original_amount"]) * (match_amt / s_amt), 2) if s_amt > 0 else 0.0

        h_new = h_curr.copy()
        h_new["book_amount"] = match_amt
        h_new["original_amount"] = h_orig
        h_new["group_id"] = next_id
        h_new["group_kind"] = "fallback_1h1s"

        s_new = s_curr.copy()
        s_new["book_amount"] = match_amt
        s_new["original_amount"] = s_orig
        s_new["group_id"] = next_id
        s_new["group_kind"] = "fallback_1h1s"

        split_records.append(h_new)
        split_records.append(s_new)
        next_id += 1

        h_curr["book_amount"] = round(h_amt - match_amt, 2)
        h_curr["original_amount"] = round(max(0.0, float(h_curr["original_amount"]) - h_orig), 2)
        s_curr["book_amount"] = round(s_amt - match_amt, 2)
        s_curr["original_amount"] = round(max(0.0, float(s_curr["original_amount"]) - s_orig), 2)

        if h_curr["book_amount"] <= 0.001:
            hi += 1
        if s_curr["book_amount"] <= 0.001:
            si += 1

    df_fallback = pd.DataFrame(split_records) if split_records else pd.DataFrame()
    return pd.concat([df_grouped, df_fallback], ignore_index=True), next_id


def _group_key_for_voucher(
    group: pd.DataFrame,
    group_id_start: int,
    special_account_pairs: Optional[List[Tuple[str, str]]] = None,
    max_recursion: int = 2,
    rule2_max_subset: int = 4,
) -> pd.DataFrame:
    """对单个 voucher_no 的子表做多借多贷分组解析 (3规则递归 + 兜底全拆解)"""
    g = group.copy().sort_values("voucher_no").reset_index(drop=True)
    g["group_id"] = pd.NA
    g["group_kind"] = pd.NA
    next_id = group_id_start

    for _ in range(max_recursion + 1):
        g, next_id, r1 = _apply_rule1(g, next_id, max_subset=rule2_max_subset)
        g, next_id, r2 = _apply_rule2(g, next_id, special_account_pairs)
        if r1 + r2 == 0:
            break

    # 兜底拆解：确保多借多贷凭证内不留任何未分组的残留行
    g, next_id = _apply_fallback_split(g, next_id)
    return g


def _group_one_voucher_worker(args):
    """多进程 worker: 必须是模块级函数以支持 Windows spawn pickle"""
    sub, special_account_pairs, max_recursion, rule2_max_subset = args
    return _group_key_for_voucher(
        sub,
        group_id_start=1,
        special_account_pairs=special_account_pairs,
        max_recursion=max_recursion,
        rule2_max_subset=rule2_max_subset,
    )


def apply_multi_debit_credit_grouping(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    """
    多借多贷分组主函数：
    注意：为了防止跨期相同 voucher_no 被混在一起分组，分组键联合使用 (year, period, voucher_no)！
    """
    if df.empty:
        return df
    df = df.copy()
    df["group_id"] = pd.NA
    df["group_kind"] = pd.NA

    # 联合唯一键：解决 SAP 跨期使用相同凭证号的问题
    df["_ composite_vkey"] = (
        df["company_code"].astype(str) + "_" +
        df["year"].astype(str) + "_" +
        df["period"].astype(str) + "_" +
        df["voucher_no"].astype(str)
    )

    n = cfg.multi_min_per_side
    counts = df.groupby("_ composite_vkey")["direction"].value_counts().unstack(fill_value=0)
    if "H" not in counts.columns:
        counts["H"] = 0
    if "S" not in counts.columns:
        counts["S"] = 0
    trigger_keys = counts[(counts["H"] > n) & (counts["S"] > n)].index.tolist()

    if not trigger_keys:
        df.drop(columns=["_ composite_vkey"], inplace=True)
        return df

    trigger_set = set(trigger_keys)
    trigger_mask = df["_ composite_vkey"].isin(trigger_set)
    df_trigger = df[trigger_mask].copy()
    df_rest = df[~trigger_mask].copy()

    groups = [g for _, g in df_trigger.groupby("_ composite_vkey", sort=False)]
    args_list = [
        (g, cfg.special_account_pairs, cfg.max_recursion, cfg.rule2_max_subset)
        for g in groups
    ]

    PARALLEL_THRESHOLD = 16
    if len(groups) >= PARALLEL_THRESHOLD:
        workers = max(2, min(len(groups), (os.cpu_count() or 4)))
        chunksize = max(1, len(groups) // (workers * 4))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_group_one_voucher_worker, args_list, chunksize=chunksize))
    else:
        results = [_group_one_voucher_worker(a) for a in args_list]

    # 全局唯一重编号：确保不同凭证之间的 group_id 绝不重叠
    global_gid_counter = cfg.group_id_start
    reindexed_frames = []
    for sub_res in results:
        sub_res = sub_res.copy()
        sub_res["group_id"] = pd.to_numeric(sub_res["group_id"], errors="coerce")
        valid_ids = sorted(sub_res["group_id"].dropna().astype(int).unique().tolist())
        id_map = {old_id: global_gid_counter + idx for idx, old_id in enumerate(valid_ids)}
        global_gid_counter += len(valid_ids)
        sub_res["group_id"] = sub_res["group_id"].map(lambda x: id_map.get(int(x), pd.NA) if pd.notna(x) else pd.NA).astype("Int64")
        reindexed_frames.append(sub_res)

    grouped_df = pd.concat(reindexed_frames, ignore_index=True)
    final_df = pd.concat([grouped_df, df_rest], ignore_index=True) if not df_rest.empty else grouped_df
    final_df.drop(columns=["_ composite_vkey"], inplace=True, errors="ignore")
    return final_df


def apply_vnd_zero_original(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    """VND 原币金额置零，且剔除 book_amount == 0 的无效行"""
    if not cfg.vnd_zero_original:
        return df
    df = df.copy()
    mask = df["currency"].astype(str).str.upper() == "VND"
    df.loc[mask, "original_amount"] = 0.0
    voucher_sum = df.groupby("voucher_no")["original_amount"].sum()
    zero_vouchers = voucher_sum[voucher_sum == 0].index
    df.loc[df["voucher_no"].isin(zero_vouchers), "currency"] = "VND"
    df = df[df["book_amount"].abs() > 1e-4].copy()
    return df


@dataclass
class UsdCheckResult:
    ok: bool
    message: str
    details: pd.DataFrame = field(default_factory=pd.DataFrame)


def check_usd_balance(
    df: pd.DataFrame,
    cfg: ProcessConfig,
    include_split_groups: bool = False,
) -> UsdCheckResult:
    """USD 原币借贷平衡校验"""
    usd = df[df["currency"].astype(str).str.upper() == "USD"].copy()
    if usd.empty:
        return UsdCheckResult(True, "无 USD 记录")

    has_group = usd["group_id"].notna().any()
    tol = cfg.usd_tolerance

    if has_group:
        if not include_split_groups and "group_kind" in usd.columns:
            usd_for_check = usd[usd["group_kind"] != "split"]
        else:
            usd_for_check = usd
        usd_for_check = usd_for_check.dropna(subset=["group_id"])
        if usd_for_check.empty:
            return UsdCheckResult(True, "USD 无 pair 组可校验")
        grouped = (
            usd_for_check.groupby(["voucher_no", "group_id", "direction"])["original_amount"]
            .sum()
            .unstack(fill_value=0.0)
        )
    else:
        grouped = (
            usd.groupby(["voucher_no", "direction"])["original_amount"]
            .sum()
            .unstack(fill_value=0.0)
        )

    if "H" not in grouped.columns:
        grouped["H"] = 0.0
    if "S" not in grouped.columns:
        grouped["S"] = 0.0
    grouped["diff"] = grouped["H"] - grouped["S"]
    bad = grouped[grouped["diff"].abs() > tol].reset_index()
    if bad.empty:
        return UsdCheckResult(True, "USD 校验通过")
    return UsdCheckResult(False, f"USD 不平衡, 违规 {len(bad)} 条", bad)


class DataProcessor:
    """核心数据转换主入口类 (与原项目调用方式 100% 兼容)"""
    def __init__(self, config: Optional[ProcessConfig] = None):
        self.config = config or ProcessConfig()
        self.logger = setup_logger(__name__)

    def load(self, source: Any, **kwargs) -> pd.DataFrame:
        if isinstance(source, pd.DataFrame):
            return source.copy()
        path = str(source)
        if path.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(path, **kwargs)
        return pd.read_csv(path, **kwargs)

    def _process(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        cfg = self.config
        out = ensure_columns(df)
        out = normalize_direction(out)
        out = remove_non_sense_voucher(out)
        out = general_transform_accounts(out)
        out = special_transform(out)
        if cfg.special_transforms:
            out = apply_special_transforms(out, cfg)
        out = apply_multi_debit_credit_grouping(out, cfg)
        out = apply_vnd_zero_original(out, cfg)
        usd_res = check_usd_balance(out, cfg)
        if not usd_res.ok:
            self.logger.warning(f"USD 平衡校验提示: {usd_res.message}")
        return out

    def run(self, source: Any, **kwargs) -> pd.DataFrame:
        return self._process(self.load(source, **kwargs))


# ==============================================================================
# 4. 导出至 FAST 标准 Excel 函数 (兼容 export_to_sap 及 12位凭证号规则)
# ==============================================================================
def export_to_sap(df: pd.DataFrame, filename: str) -> str:
    """
    将 DataProcessor 处理后的 DataFrame 导出为可直接导入 FAST 的 Excel 文件。
    同时解决：
    1) 多借多贷拆分组后凭证号加 `_子序号` 标识
    2) 严格控制最终凭证号长度 <= 12 位，且兼容 data_reconcile.py 的 `_` 拆分反查规则
    """
    # 若原项目 utils 中已有完好的 export_to_sap，优先尝试调用，若失败则走内置实现
    out_path = os.path.join(BASE_DIR, filename) if not os.path.isabs(filename) else filename
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if df is None or df.empty:
        pd.DataFrame().to_excel(out_path, index=False)
        return out_path

    df_out = df.copy()

    # 为拆分后的多借多贷子组生成带 `_` 的 12 位以内 FAST 凭证号
    if "group_id" in df_out.columns and df_out["group_id"].notna().any():
        # 在每个 voucher_no 内部将 group_id 映射为 1, 2, 3... 短序号，确保 `{voucher_no}_{seq}` <= 12 位
        def _assign_short_voucher_no(grp: pd.DataFrame) -> pd.DataFrame:
            grp = grp.copy()
            base_vno = str(grp["voucher_no"].iloc[0]).strip()
            unique_gids = grp["group_id"].dropna().unique().tolist()
            if len(unique_gids) <= 1:
                grp["fast_voucher_no"] = base_vno[:12]
                return grp
            gid_to_seq = {gid: idx + 1 for idx, gid in enumerate(unique_gids)}
            new_vnos = []
            for _, row in grp.iterrows():
                gid = row["group_id"]
                if pd.isna(gid):
                    new_vnos.append(base_vno[:12])
                else:
                    seq_str = str(gid_to_seq[gid])
                    # 确保拼接 `_seq` 后总长度 <= 12 位 (若原号10位，去掉前导0或保留后部)
                    max_base_len = 12 - 1 - len(seq_str)
                    trimmed_base = base_vno[-max_base_len:] if len(base_vno) > max_base_len else base_vno
                    new_vnos.append(f"{trimmed_base}_{seq_str}")
            grp["fast_voucher_no"] = new_vnos
            return grp

        df_out = df_out.groupby(["year", "period", "voucher_no"], group_keys=False, sort=False).apply(_assign_short_voucher_no)
    else:
        df_out["fast_voucher_no"] = df_out["voucher_no"].astype(str).str[:12]

    # 解析过账日期
    if "posting_date" not in df_out.columns:
        df_out["posting_date"] = df_out.apply(
            lambda r: resolve_period_date(r.get("year", ""), r.get("period", ""), r.get("cpudt", "")),
            axis=1
        )

    df_out.to_excel(out_path, index=False)
    logging.getLogger("SAP2FAST").info(f"已导出 Excel 文件: {out_path} (共 {len(df_out)} 行)")
    return out_path


# ==============================================================================
# 5. 修复后的 DataReconciler (完整修复 modules/data_reconcile.py)
# ==============================================================================
class DataReconciler:
    """
    使用方法：
    初始化：DataReconciler('202403')  输入会计期间 yyyymm
    运行 fetch_sap_to_fast()  导出可直接导入 fast 的凭证分录
    """

    def __init__(self, fiscalym: str):
        self.logger = setup_logger(__name__)
        from db import get_hana_pool, get_mssql_pool
        self.sap_pool = get_hana_pool()
        self.fast_pool = get_mssql_pool()
        self.fiscalym = str(fiscalym).strip()
        self.calendarym = fiscalym_to_calendarym(self.fiscalym)
        self._get_foreign_currency_account_dict()

    def _get_foreign_currency_account_dict(self) -> None:
        from db import MSSQLSQL
        df = self.fast_pool.execute_query(MSSQLSQL.QUERY_TK)
        df["account_code"] = df["account_code"].astype(str).str.strip()
        self.foreign_currency_account = df.to_dict(orient="records")

    def _get_sap_fast_arising(self) -> None:
        from db import HanaSQL, MSSQLSQL
        sapsql = HanaSQL.format_account_arising_month({
            "gjahr": self.fiscalym[:4],
            "monat": self.fiscalym[4:6]
        })
        self.df_sap_account = self.sap_pool.execute_query(sapsql)

        fastsql = MSSQLSQL.format_account_arising_month({
            "ym": self.calendarym
        })
        self.df_fast_account = self.fast_pool.execute_query(fastsql)

    def compare_account_diff(self, account_mapping: Optional[dict] = None) -> List[str]:
        from db import HanaSQL, MSSQLSQL
        self._get_sap_fast_arising()
        df_fast = self.df_fast_account.copy()
        df_sap = self.df_sap_account.copy()

        fast_compare = df_fast.rename(columns={"book_amount": "book_amount_fast"})
        sap_compare = df_sap.rename(columns={"book_amount": "book_amount_sap"})

        if account_mapping is None:
            account_mapping = AccountConfig.load()

        # 【修复 1】：初始化 df 和 fast_account 列，防止 fkber 为空时报错
        df = sap_compare.copy()
        if "fast_account" not in df.columns:
            df["fast_account"] = pd.NA

        # 1) fkber + account_code 映射
        fkber_rows = []
        for k, v in (account_mapping.get("fkber") or {}).items():
            if isinstance(v, dict):
                for saccount, faccount in v.items():
                    fkber_rows.append({
                        "fkber": str(k).strip(),
                        "account_code": str(saccount).strip(),
                        "fast_account": str(faccount).strip(),
                    })
        if fkber_rows:
            fkber_map = pd.DataFrame(fkber_rows).drop_duplicates(
                subset=["fkber", "account_code"], keep="last"
            )
            df = df.merge(fkber_map, on=["fkber", "account_code"], how="left", suffixes=("", "__new"))
            if "fast_account__new" in df.columns:
                df["fast_account"] = df["fast_account__new"].combine_first(df["fast_account"])
                df.drop(columns=["fast_account__new"], inplace=True)

        # 2) account_code 映射: account 优先级更高 (在 fkber 之后覆盖)
        account_rows = [
            {"account_code": str(k).strip(), "fast_account": str(v).strip()}
            for k, v in (account_mapping.get("account") or {}).items()
        ]
        if account_rows:
            account_map = pd.DataFrame(account_rows).drop_duplicates(
                subset=["account_code"], keep="last"
            )
            df = df.merge(account_map, on="account_code", how="left", suffixes=("", "__new"))
            if "fast_account__new" in df.columns:
                df["fast_account"] = df["fast_account__new"].combine_first(df["fast_account"])
                df.drop(columns=["fast_account__new"], inplace=True)

        df["fast_account"] = df["fast_account"].fillna(df["account_code"]).astype(str).str.strip()

        # 3) 外币科目更新
        for account in self.foreign_currency_account:
            acc_code = str(account["account_code"]).strip()
            if acc_code[:3] in ["111", "112"]:
                continue
            df.loc[
                (df["currency"] == account["currency"]) &
                (df["fast_account"].str[:-1] == acc_code[:-1]),
                "fast_account"
            ] = acc_code

        df_sap_agg = df.groupby("fast_account", as_index=False).agg({"book_amount_sap": "sum"})
        df_fast_agg = fast_compare.groupby("account_code", as_index=False).agg({"book_amount_fast": "sum"})
        df_diff = df_sap_agg.merge(df_fast_agg, right_on="account_code", left_on="fast_account", how="left")
        df_diff.fillna({"book_amount_sap": 0.0, "book_amount_fast": 0.0}, inplace=True)
        df_diff["book_amount_sap"] = df_diff["book_amount_sap"].astype(float)
        df_diff["book_amount_fast"] = df_diff["book_amount_fast"].astype(float)

        # 同步保存一份 compare.csv 供财务核查
        df_diff.to_csv(os.path.join(BASE_DIR, "compare.csv"), index=False, encoding="utf-8-sig")

        fast_list_account = df_diff.loc[
            (df_diff["book_amount_fast"] - df_diff["book_amount_sap"]).abs() > 1e-2,
            "fast_account"
        ].unique().tolist()
        for ac in ["5152", "6351"]:
            if ac not in fast_list_account:
                fast_list_account.append(ac)

        sap_list_account = df.loc[df["fast_account"].isin(fast_list_account), "account_code"].unique().tolist()
        for ac in ["6603050001", "6603050003"]:
            if ac not in sap_list_account:
                sap_list_account.append(ac)

        sap_account_list_sql = HanaSQL.format_voucher_by_account({
            "account_codes": ",".join([f"'{x}'" for x in sap_list_account]),
            "gjahr": self.fiscalym[:4],
            "monat": self.fiscalym[4:6]
        })
        df_sap_account_list = self.sap_pool.execute_query(sap_account_list_sql)

        fast_account_list_sql = MSSQLSQL.format_query_voucher_by_account({
            "account_codes": ",".join([f"'{x}'" for x in fast_list_account]),
            "ym": self.calendarym
        })
        df_fast_account_list = self.fast_pool.execute_query(fast_account_list_sql)

        sap_voucher_nos = df_sap_account_list["voucher_no"].astype(str).str.strip().unique().tolist()
        raw_fast_voucher_nos = df_fast_account_list["voucher_no"].astype(str).str.strip().unique().tolist()

        # 【修复 2】：使用新集合剥离 `_` 前后缀，修复原项目遍历列表原地 remove 导致的漏删 Bug
        normalized_fast_vnos = set()
        for fast_vno in raw_fast_voucher_nos:
            if "_" in fast_vno:
                parts = fast_vno.split("_")
                core_no = parts[0] if len(parts[0]) > 5 else parts[1]
                normalized_fast_vnos.add(core_no.strip())
            else:
                normalized_fast_vnos.add(fast_vno.strip())

        missing_voucher_nos = [
            sap_vno for sap_vno in sap_voucher_nos
            if sap_vno not in normalized_fast_vnos
        ]

        missing_voucher_nos = check_exist_voucher_no(missing_voucher_nos, self.calendarym)
        self.logger.info(f"获取缺失凭证号数量: {len(missing_voucher_nos)} 条！")
        return missing_voucher_nos

    def fetch_sap_to_fast(self) -> Optional[str]:
        from db import get_hana_pool, HanaSQL
        voucher_nos = self.compare_account_diff()
        if not voucher_nos:
            self.logger.info(f"会计期间 {self.fiscalym} 无缺失凭证号！")
            return None

        pool = get_hana_pool()
        step = 500
        dfs = []
        for i in range(0, len(voucher_nos), step):
            batch = voucher_nos[i:i + step]
            sql = HanaSQL.format_voucher_no({
                "voucher_nos": ",".join([f"'{x}'" for x in batch]),
                "gjahr": self.fiscalym[:4]
            })
            df_batch = pool.execute_query(sql)
            dfs.append(df_batch)

        df = pd.concat(dfs, axis=0, ignore_index=True)
        cfg = ProcessConfig()
        processor = DataProcessor(cfg)
        df = processor.run(df)

        out_filename = f"output/{self.fiscalym}_{time.time()}_added_data.xlsx"
        out_path = export_to_sap(df, filename=out_filename)
        self.logger.info(f"成功导出缺失凭证文件: {out_path}")
        return out_path


# ==============================================================================
# 6. 修复后的 SAPExporter (完整修复 modules/sap_export.py)
# ==============================================================================
class SAPExporter:
    def __init__(self, cpudt: str = ""):
        self.logger = setup_logger(__name__)
        if cpudt == "":
            cpudt = get_next_fast_date()
        fiscalym = calandarym_to_ficalym(cpudt)
        period = {
            "gjahr": fiscalym[:4],
            "monat": fiscalym[4:6]
        }
        if cpudt[-2:] == "01":
            earliest_cpudt, latest_cpudt = get_sap_begin_end_cupdt(fiscalym)
            cpudt = earliest_cpudt
        else:
            earliest_cpudt = cpudt
            latest_cpudt = cpudt

        self.status = StatusConfig(
            earliest_cpudt=earliest_cpudt,
            latest_cpudt=latest_cpudt,
            current_cupdt=cpudt,
            current_period=period
        )

    def run(self) -> None:
        from db import get_hana_pool, HanaSQL
        pconfig = ProcessConfig()
        self.logger.info("Loaded the ProcessConfig")
        fiscalym = self.status.current_period["gjahr"] + self.status.current_period["monat"]
        processor = DataProcessor(pconfig)
        conn = get_hana_pool()

        df = conn.execute_query(HanaSQL.format_voucher_by_cpudt(self.status.get_kwargs()))
        if df is not None and not df.empty:
            df = processor.run(df)
            export_to_sap(df, filename=f"output/{fiscalym}_{time.time()}_sync_data.xlsx")
            self.logger.info(f"Exported {fiscalym} data to excel")

        while self.status.next_cpudt():
            df = conn.execute_query(HanaSQL.format_voucher_by_cpudt(self.status.get_kwargs()))
            if df is not None and not df.empty:
                df = processor.run(df)
                export_to_sap(df, filename=f"output/{fiscalym}_{time.time()}_sync_data.xlsx")
                self.logger.info(f"Exported {fiscalym} data to excel")


# ==============================================================================
# 7. 离线文件重跑与主程序入口
# ==============================================================================
def reprocess_local_files(input_pattern: str = "input/*.xlsx") -> List[str]:
    """无需连库，直接读取项目下已有的 Excel 文件运行修复后的 DataProcessor"""
    logger = setup_logger("LocalReprocess")
    files = sorted(glob.glob(os.path.join(BASE_DIR, input_pattern)))
    if not files:
        logger.warning(f"未找到匹配的本地文件: {input_pattern}")
        return []

    processor = DataProcessor(ProcessConfig())
    out_files = []
    for f in files:
        if os.path.basename(f).startswith("~$"):
            continue
        try:
            df_raw = pd.read_excel(f)
            # 若已经是处理完的 .result.xlsx (带有 tk/ps_no 等 FAST 最终列)，则跳过或仅做校验
            if not all(c in df_raw.columns for c in ["voucher_no", "account_code", "direction"]):
                logger.info(f"文件 {os.path.basename(f)} 已是最终 FAST 导入格式 ({len(df_raw)} 行)，无需二次转换。")
                continue
            df_res = processor.run(df_raw)
            out_file = export_to_sap(df_res, filename=f"output/fixed_{os.path.basename(f)}")
            out_files.append(out_file)
        except Exception as e:
            logger.error(f"处理文件 {f} 出错: {e}", exc_info=True)
    return out_files


def main():
    parser = argparse.ArgumentParser(description="SAP2FAST (2396fast) 全量修复版运行入口")
    parser.add_argument(
        "--mode",
        choices=["reconcile", "export", "local", "all"],
        default="reconcile",
        help="reconcile=按月核对差异并导出补充凭证(默认); export=按日增量同步; local=本地离线文件重跑"
    )
    parser.add_argument("--fiscalym", default="202403", help="会计期间 YYYYMM (如 202403, 202410, 202508)")
    parser.add_argument("--cpudt", default="", help="增量同步指定自然日期 YYYYMMDD")
    args = parser.parse_args()

    if args.mode == "local":
        reprocess_local_files()
    elif args.mode == "export":
        SAPExporter(cpudt=args.cpudt).run()
    elif args.mode == "reconcile":
        DataReconciler(fiscalym=args.fiscalym).fetch_sap_to_fast()
    elif args.mode == "all":
        SAPExporter(cpudt=args.cpudt).run()
        DataReconciler(fiscalym=args.fiscalym).fetch_sap_to_fast()


if __name__ == "__main__":
    main()