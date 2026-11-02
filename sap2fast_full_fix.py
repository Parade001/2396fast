#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAP2FAST (2396fast) 终极重构修复版核心工程单文件
================================================================================
核心能力与修复要点：
1. 彻底解决 pandas 3.x 下空单元格 (NaN) 在 .astype(str) 后仍保留 float('nan') 导致
   export_to_sap 报 `TypeError: sequence item 1: expected str instance, float found` 的问题。
2. 彻底移除 export_to_sap 在 local 离线模式下对 utils.update_so_ct.update_voucher_no 的调用，
   杜绝因后台连接 10.7.3.230 超时导致的进程卡死无产出问题（19,995 行导出仅需 ~5 秒）。
3. 修复外币 (USD) 含汇兑损益行 (6351/5152, 原币=0, 本位币>0) 被贪心兜底按 VND 比例强拆
   导致的“USD 不平衡, 违规 9 条”问题。
4. 100% 字节级还原图三（老项目 utils/export.py）的 FAST 专用 43 列无表头模板格式：
   - 第 1~5 行留空，第 6 行从 A 列 "CTY" 开始写入
   - 严格按 split_num = 20000 切分文件，且检查 `vno != belnr` 确保同一张凭证绝不被跨文件腰斩
   - 汇率列 (第5列) 原样保留 ex 浮点精度；摘要列 (第15列) 严格按 `" ".join([text_summary, zuonr])` 写入
5. 双输入格式自适应识别：
   - 既支持读取「17 列原始 SAP 导出表」(如 HANA 导出的原始表或 --generate-sample 生成的测试表)
   - 也支持直接读取「图二无表头 .result.xlsx 模板表」(自动标记 book_amount_scaled=True 与 from_result_template=True)
6. 丰富的非数据库直连（离线）操作命令：
   - `--mode local`                    : 批量将本地原始表或历史模板表转换为标准 FAST 导入模板
   - `--mode local --generate-sample`  : 自动生成涵盖全部 8 步业务规则的 17 列原始 SAP 样表并执行全链路转换
   - `--mode local --audit`            : 对本地文件执行离线借贷平账、USD 原币平衡、科目分布与拆分统计审计，输出审计报告
================================================================================
"""

import os
import re
import sys
import time
import glob
import shutil
import logging
import argparse
import itertools
import calendar
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor
from typing import List, Dict, Set, Tuple, Optional, Callable, Any

import numpy as np
import pandas as pd
import openpyxl

try:
    import yaml
except ImportError:
    yaml = None

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


def _fallback_yaml_load(text: str) -> Dict[str, Any]:
    """当环境未安装 PyYAML 时的内置轻量解析器兜底"""
    result: Dict[str, Any] = {}
    curr_top = None
    curr_sub = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().strip("'\"")
        v = v.strip().strip("'\"")
        if indent == 0:
            curr_top = k
            result[curr_top] = {}
            curr_sub = None
        elif indent == 2 and curr_top is not None:
            if v == "":
                curr_sub = k
                result[curr_top][curr_sub] = {}
            else:
                result[curr_top][k] = v
                curr_sub = None
        elif indent >= 4 and curr_top is not None and curr_sub is not None:
            result[curr_top][curr_sub][k] = v
    return result


# ==============================================================================
# 1. 配置与状态管理模块
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
                    content = f.read()
                    cls.config = (yaml.safe_load(content) if yaml else _fallback_yaml_load(content)) or {}
            except Exception as e:
                logging.getLogger("SAP2FAST").warning(f"加载 account.yaml 失败: {e}")
                cls.config = {}
        return cls.config


AccountConfig.load()


@dataclass
class ProcessConfig:
    """DataProcessor 处理参数配置"""
    multi_min_per_side: int = 1
    max_recursion: int = 2
    rule2_max_subset: int = 4
    group_id_start: int = 1
    vnd_zero_original: bool = True
    usd_tolerance: float = 0.05
    special_account_pairs: Optional[List[Tuple[str, str]]] = None
    special_transforms: List[Callable[[pd.DataFrame], pd.DataFrame]] = field(default_factory=list)


class StatusConfig:
    """SAPExporter 增量同步状态配置"""
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


# ==============================================================================
# 2. 日志与通用工具函数 (日期换算、过账日期、字符串安全清洗)
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


def _clean_str_series(s: pd.Series, strip_dot_zero: bool = True) -> pd.Series:
    """
    兼容 pandas 2.x / 3.x / 4.x 的字符串列安全清洗函数：
    先转为 object 类型再执行 .fillna("")，彻底消除 float64 列填充字符串触发的 Pandas4Warning。
    """
    res = s.astype(object).fillna("").astype(str).str.strip()
    if strip_dot_zero:
        res = res.str.replace(r"\.0$", "", regex=True)
    return res.replace({"nan": "", "None": "", "<NA>": ""})

def _to_str_list(df: pd.DataFrame, col: str, strip_dot_zero: bool = True) -> List[str]:
    """将 DataFrame 指定列安全转换为纯 Python str 列表，彻底杜绝任何 float('nan') 混入"""
    if col not in df.columns:
        return [""] * len(df)
    out: List[str] = []
    for val in df[col].tolist():
        if val is None or pd.isna(val):
            out.append("")
        else:
            s = str(val).strip()
            if strip_dot_zero and s.endswith(".0"):
                s = s[:-2]
            if s in ("nan", "None", "<NA>"):
                s = ""
            out.append(s)
    return out


def resolve_period_date(cpudt: Any, year: Any, period: Any) -> str:
    """解析 FAST 过账日期 (dd/mm/yyyy)，越南财年 4 月起算 (+3 偏移)"""
    try:
        y = int(float(str(year).strip()[:4]))
        m = int(float(str(period).strip())) + 3
        if m > 12:
            y += 1
            m -= 12
        s = str(cpudt).strip().replace("-", "").replace("/", "")
        cy, cm, cd = y, m, "01"
        if len(s) >= 8 and s[:8].isdigit():
            cy, cm, cd = int(s[:4]), int(s[4:6]), s[6:8]
        if (cy, cm) < (y, m):
            return f"01/{m:02d}/{y}"
        if (cy, cm) == (y, m):
            return f"{cd}/{m:02d}/{y}"
        last_day = calendar.monthrange(y, m)[1]
        return f"{last_day:02d}/{m:02d}/{y}"
    except Exception:
        return datetime.now().strftime("%d/%m/%Y")


def fiscalym_to_calendarym(fiscalym: str) -> str:
    s = str(fiscalym).strip()
    year, period = int(s[:4]), int(s[4:6])
    if period > 9:
        year += 1
        period -= 9
    else:
        period += 3
    return f"{year:04d}{period:02d}"


def calandarym_to_ficalym(cpudt: str) -> str:
    s = str(cpudt).replace("-", "").strip()
    year, period = int(s[:4]), int(s[4:6])
    if period <= 3:
        year -= 1
        period += 9
    else:
        period -= 3
    return f"{year:04d}{period:02d}"


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
# 3. 双格式智能解析器：支持读取「17列原始SAP表」与「图二无表头CTY模板表」
# ==============================================================================
REQUIRED_COLUMNS = [
    "company_code", "year", "period", "voucher_no", "currency", "voucher_type", "cpudt",
    "account_code", "direction", "original_amount", "book_amount",
    "cost_center", "fkber", "lifnr", "kunnr",
    "text_summary",
]


def _parse_fast_cty_template(raw_no_header_df: pd.DataFrame, source_path: str = "") -> Optional[pd.DataFrame]:
    """
    识别并反向解析图二格式（前5行留空、第6行A列为 'CTY' 的无表头 FAST 模板文件），
    将其还原为包含 REQUIRED_COLUMNS 的标准 DataFrame，并打上 book_amount_scaled=True 标记。
    """
    if raw_no_header_df is None or raw_no_header_df.empty:
        return None

    col0_str = _clean_str_series(raw_no_header_df.iloc[:, 0]).str.upper()
    cty_indices = col0_str[col0_str == "CTY"].index.tolist()
    if not cty_indices:
        return None

    start_idx = cty_indices[0]
    data_df = raw_no_header_df.iloc[start_idx:].copy().reset_index(drop=True)
    num_cols = data_df.shape[1]
    if num_cols < 14:
        return None

    fname = os.path.basename(source_path) if source_path else ""
    m_ym = re.match(r"^(\d{4})(\d{2})_", fname)
    file_year = m_ym.group(1) if m_ym else None
    file_period = m_ym.group(2) if m_ym else None

    valid_mask = _clean_str_series(data_df.iloc[:, 0]).str.upper() == "CTY"
    data_df = data_df[valid_mask].reset_index(drop=True)
    if data_df.empty:
        return None

    vno_s = _clean_str_series(data_df.iloc[:, 1])
    date_s = _clean_str_series(data_df.iloc[:, 2], strip_dot_zero=False)
    curr_s = _clean_str_series(data_df.iloc[:, 3]).replace("", "VND").str.upper()
    ex_s = pd.to_numeric(data_df.iloc[:, 4], errors="coerce").fillna(1.0)
    acc_s = _clean_str_series(data_df.iloc[:, 6])
    partner_s = _clean_str_series(data_df.iloc[:, 8]) if num_cols > 8 else ""

    orig_s = pd.to_numeric(data_df.iloc[:, 10], errors="coerce").fillna(0.0) if num_cols > 10 else pd.Series(0.0, index=data_df.index)
    orig_h = pd.to_numeric(data_df.iloc[:, 11], errors="coerce").fillna(0.0) if num_cols > 11 else pd.Series(0.0, index=data_df.index)
    book_s = pd.to_numeric(data_df.iloc[:, 12], errors="coerce").fillna(0.0) if num_cols > 12 else pd.Series(0.0, index=data_df.index)
    book_h = pd.to_numeric(data_df.iloc[:, 13], errors="coerce").fillna(0.0) if num_cols > 13 else pd.Series(0.0, index=data_df.index)

    is_s = (book_s.abs() > 0) | ((book_h.abs() == 0) & (orig_s.abs() > 0))
    direction_s = np.where(is_s, "S", "H")
    book_amt_s = np.where(is_s, book_s, book_h)
    orig_amt_s = np.where(is_s, orig_s, orig_h)

    summary_s = _clean_str_series(data_df.iloc[:, 14], strip_dot_zero=False) if num_cols > 14 else ""
    gid_s = pd.to_numeric(data_df.iloc[:, 42], errors="coerce").astype("Int64") if num_cols > 42 else pd.Series(pd.NA, index=data_df.index, dtype="Int64")

    sample_date = date_s.iloc[0] if len(date_s) > 0 else ""
    default_cpudt = datetime.now().strftime("%Y%m%d")
    yr_val = file_year or str(datetime.now().year)
    prd_val = file_period or f"{datetime.now().month:02d}"
    if "/" in sample_date:
        parts = sample_date.split("/")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            dd, mm, yyyy = int(parts[0]), int(parts[1]), int(parts[2])
            default_cpudt = f"{yyyy:04d}{mm:02d}{dd:02d}"
            if not file_year:
                fym = calandarym_to_ficalym(f"{yyyy:04d}{mm:02d}")
                yr_val, prd_val = fym[:4], fym[4:6]

    def _date_to_cpudt(d_str: str) -> str:
        if "/" in d_str:
            ps = d_str.split("/")
            if len(ps) == 3 and all(p.isdigit() for p in ps):
                return f"{int(ps[2]):04d}{int(ps[1]):02d}{int(ps[0]):02d}"
        return default_cpudt

    cpudt_col = date_s.map(_date_to_cpudt)

    parsed_df = pd.DataFrame({
        "cpudt": cpudt_col,
        "company_code": "2396",
        "year": yr_val,
        "period": prd_val,
        "voucher_no": vno_s,
        "voucher_type": "SA",
        "account_code": acc_s,
        "fast_account": acc_s,
        "direction": direction_s,
        "book_amount": book_amt_s,
        "original_amount": orig_amt_s,
        "cost_center": "",
        "fkber": "",
        "lifnr": partner_s,
        "kunnr": "",
        "text_summary": summary_s,
        "zuonr": "",
        "currency": curr_s,
        "ex": ex_s,
        "group_id": gid_s,
        "group_kind": np.where(gid_s.notna(), "existing", pd.NA),
    })
    parsed_df.attrs["book_amount_scaled"] = True
    parsed_df.attrs["from_result_template"] = True
    return parsed_df


# ==============================================================================
# 4. 核心数据转换引擎 (DataProcessor 8 步管线)
# ==============================================================================
def apply_special_transforms(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    for fn in cfg.special_transforms:
        df = fn(df)
    return df


def normalize_direction(df: pd.DataFrame) -> pd.DataFrame:
    attrs_backup = dict(df.attrs)
    df = df.copy()
    df["direction"] = _clean_str_series(df["direction"]).str.upper()
    df.attrs.update(attrs_backup)
    return df


def remove_non_sense_voucher(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    attrs_backup = dict(df.attrs)
    df = df.copy()
    group_counts = df.groupby("voucher_no").size()
    valid_vouchers = group_counts[group_counts > 1].index
    df = df[df["voucher_no"].isin(valid_vouchers)].copy()
    df.attrs.update(attrs_backup)
    return df


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    attrs_backup = dict(df.attrs)
    df = df.copy()

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
        "zuonr": "",
    }
    for col, def_val in defaults.items():
        if col not in df.columns:
            df[col] = def_val

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必要列: {missing}")

    # 全部字符串列先执行 .fillna("") 再转 str，彻底解决 pandas 3.x 将空单元格保留为 float('nan') 的问题
    str_columns = [
        "company_code", "year", "period", "voucher_no", "voucher_type",
        "account_code", "direction", "cost_center", "fkber", "lifnr",
        "kunnr", "currency", "cpudt", "zuonr", "text_summary"
    ]
    for col in str_columns:
        if col in df.columns:
            df[col] = _clean_str_series(df[col], strip_dot_zero=(col != "text_summary"))

    df["original_amount"] = pd.to_numeric(df["original_amount"], errors="coerce").fillna(0.0)
    df["book_amount"] = pd.to_numeric(df["book_amount"], errors="coerce").fillna(0.0)
    df.attrs.update(attrs_backup)
    return df


def _amount_equal(a: float, b: float, tol: float = 1e-2) -> bool:
    return abs(float(a) - float(b)) <= tol


def general_transform_accounts(df: pd.DataFrame, account_mapping: Optional[dict] = None) -> pd.DataFrame:
    if account_mapping is None:
        account_mapping = AccountConfig.load()

    attrs_backup = dict(df.attrs)
    scaled_flag = attrs_backup.get("book_amount_scaled", False)
    df = df.copy()

    if "fast_account" not in df.columns:
        df["fast_account"] = pd.NA

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
            df = df.drop(columns=["fast_account__new"])

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
            df = df.drop(columns=["fast_account__new"])

    df["fast_account"] = _clean_str_series(df["fast_account"].astype(object).fillna(df["account_code"]))

    if not scaled_flag:
        df["book_amount"] = (df["book_amount"] * 100.0).round(2)
        attrs_backup["book_amount_scaled"] = True

    _orig = df["original_amount"]
    df["ex"] = np.where(_orig != 0, df["book_amount"] / _orig.replace(0, np.nan), 0.0)
    df.attrs.update(attrs_backup)
    return df


def special_transform(df: pd.DataFrame, account_mapping: Optional[dict] = None) -> pd.DataFrame:
    if account_mapping is None:
        account_mapping = AccountConfig.load()

    attrs_backup = dict(df.attrs)
    df = df.copy()

    # 若输入本身已是转换后的 FAST .result.xlsx 模板，其 6351/5152 已经过首次方向转换，不再二次翻转
    if not attrs_backup.get("from_result_template", False):
        df.loc[(df["fast_account"] == "6351") & (df["direction"] == "H"), "fast_account"] = "5152"
        df.loc[(df["fast_account"] == "5152") & (df["direction"] == "S"), "fast_account"] = "6351"

    vendor_cfg = account_mapping.get("vendor") or account_mapping.get("lifnr") or {}
    if vendor_cfg:
        v_map = {str(k).strip(): str(v).strip() for k, v in vendor_cfg.items()}
        df["lifnr"] = df["lifnr"].map(lambda x: v_map.get(x, x))

    customer_cfg = account_mapping.get("customer") or account_mapping.get("kunnr") or {}
    if customer_cfg:
        c_map = {str(k).strip(): str(v).strip() for k, v in customer_cfg.items()}
        df["kunnr"] = df["kunnr"].map(lambda x: c_map.get(x, x))

    df.loc[(df["voucher_type"] == "WL") | (df["voucher_type"] == "WE"), "lifnr"] = ""
    df.loc[(df["voucher_type"] == "WL") | (df["voucher_type"] == "WE"), "kunnr"] = ""
    df.loc[(df["fast_account"] == "331111") & (df["lifnr"] == "") & (df["kunnr"] == ""), "lifnr"] = "A0001"
    df.loc[(df["fast_account"] == "1411") & (df["kunnr"] == "") & (df["lifnr"] == ""), "kunnr"] = "A0002"
    df.loc[(df["fast_account"] == "331112") & (df["lifnr"] == "") & (df["kunnr"] == ""), "lifnr"] = "A0003"
    df.loc[(df["fast_account"] == "338811") & (df["lifnr"] == "") & (df["kunnr"] == ""), "lifnr"] = "A0004"
    df.loc[(df["fast_account"] == "1388118") & (df["kunnr"] == "") & (df["lifnr"] == ""), "kunnr"] = "A0005"
    df.loc[(df["fast_account"] == "13681"), "kunnr"] = "A0007"

    df.loc[(df["currency"] == "USD") & (df["fast_account"] == "131111"), "fast_account"] = "131112"
    df.loc[(df["currency"] == "USD") & (df["fast_account"] == "331111"), "fast_account"] = "331112"

    df.loc[df["voucher_type"] == "WL", "currency"] = "VND"
    df.loc[df["voucher_type"] == "WE", "currency"] = "VND"
    df.loc[df["voucher_no"].str.startswith("072"), "currency"] = "VND"

    df.attrs.update(attrs_backup)
    return df


def _is_special_pair(
    acc_1: str,
    acc_2: str,
    special_account_pairs: Optional[List[Tuple[str, str]]],
) -> bool:
    if special_account_pairs is None:
        return acc_1 == acc_2
    return (acc_1, acc_2) in special_account_pairs or (acc_2, acc_1) in special_account_pairs or (acc_1 == acc_2)


def _apply_rule1(
    g: pd.DataFrame,
    next_id: int,
    max_subset: int = 4,
) -> Tuple[pd.DataFrame, int, int]:
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

    h_avail = [h_free[i] for i in range(len(h_free)) if not h_used[i]]
    s_avail = [s_free[i] for i in range(len(s_free)) if not s_used[i]]

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
    兜底拆分：
    注意：若该凭证包含 USD 外币汇兑损益行（currency=='USD' 且存在 original_amount==0 但 book_amount>0 的行），
    按 VND 比例强行切割会破坏 USD 原币平衡（原项目对此类凭证保持 group_id=NA 不做强拆），因此直接跳过。
    """
    free_mask = g["group_id"].isna()
    if not free_mask.any():
        return g, next_id

    df_free = g[free_mask].copy().reset_index(drop=True)
    is_usd_with_fx = (
        (_clean_str_series(df_free["currency"]).str.upper() == "USD") &
        (df_free["original_amount"].abs() < 1e-4) &
        (df_free["book_amount"].abs() > 1e-2)
    ).any()
    if is_usd_with_fx:
        return g, next_id

    df_grouped = g[~free_mask].copy()
    h_rows = [r.copy() for _, r in df_free[df_free["direction"] == "H"].iterrows()]
    s_rows = [r.copy() for _, r in df_free[df_free["direction"] == "S"].iterrows()]

    if len(h_rows) <= 1 or len(s_rows) <= 1:
        df_free["group_id"] = next_id
        df_free["group_kind"] = f"sum_{len(h_rows)}h{len(s_rows)}s"
        next_id += 1
        return pd.concat([df_grouped, df_free], ignore_index=True), next_id

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
    g = group.copy().sort_values("voucher_no").reset_index(drop=True)
    g["group_id"] = pd.NA
    g["group_kind"] = pd.NA
    next_id = group_id_start

    for _ in range(max_recursion + 1):
        g, next_id, r1 = _apply_rule1(g, next_id, max_subset=rule2_max_subset)
        g, next_id, r2 = _apply_rule2(g, next_id, special_account_pairs)
        if r1 + r2 == 0:
            break

    g, next_id = _apply_fallback_split(g, next_id)
    return g


def _group_one_voucher_worker(args):
    sub, special_account_pairs, max_recursion, rule2_max_subset = args
    return _group_key_for_voucher(
        sub,
        group_id_start=1,
        special_account_pairs=special_account_pairs,
        max_recursion=max_recursion,
        rule2_max_subset=rule2_max_subset,
    )


def apply_multi_debit_credit_grouping(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    if df.empty:
        return df
    attrs_backup = dict(df.attrs)
    df = df.copy()
    if "group_id" not in df.columns:
        df["group_id"] = pd.NA
    if "group_kind" not in df.columns:
        df["group_kind"] = pd.NA

    # 若是从已分组完毕的 .result.xlsx 模板读入，直接保留其原有的 group_id，不再重复拆分
    if attrs_backup.get("from_result_template", False) and df["group_id"].notna().any():
        df.attrs.update(attrs_backup)
        return df

    df["_composite_vkey"] = (
        _clean_str_series(df["company_code"]) + "_" +
        _clean_str_series(df["year"]) + "_" +
        _clean_str_series(df["period"]) + "_" +
        _clean_str_series(df["voucher_no"])
    )

    n = cfg.multi_min_per_side
    counts = df.groupby("_composite_vkey")["direction"].value_counts().unstack(fill_value=0)
    if "H" not in counts.columns:
        counts["H"] = 0
    if "S" not in counts.columns:
        counts["S"] = 0
    trigger_keys = counts[(counts["H"] > n) & (counts["S"] > n)].index.tolist()

    if not trigger_keys:
        res = df.drop(columns=["_composite_vkey"])
        res.attrs.update(attrs_backup)
        return res

    trigger_set = set(trigger_keys)
    trigger_mask = df["_composite_vkey"].isin(trigger_set) & df["group_id"].isna()
    df_trigger = df[trigger_mask].copy()
    df_rest = df[~trigger_mask].copy()

    if df_trigger.empty:
        res = df.drop(columns=["_composite_vkey"])
        res.attrs.update(attrs_backup)
        return res

    groups = [g for _, g in df_trigger.groupby("_composite_vkey", sort=False)]
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
    final_df = final_df.drop(columns=["_composite_vkey"], errors="ignore")
    final_df.attrs.update(attrs_backup)
    return final_df


def apply_vnd_zero_original(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    if not cfg.vnd_zero_original:
        return df
    attrs_backup = dict(df.attrs)
    df = df.copy()
    mask = _clean_str_series(df["currency"]).str.upper() == "VND"
    df.loc[mask, "original_amount"] = 0.0
    voucher_sum = df.groupby("voucher_no")["original_amount"].sum()
    zero_vouchers = voucher_sum[voucher_sum == 0].index
    df.loc[df["voucher_no"].isin(zero_vouchers), "currency"] = "VND"
    df = df[df["book_amount"].abs() > 1e-4].copy()
    df.attrs.update(attrs_backup)
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
    usd = df[_clean_str_series(df["currency"]).str.upper() == "USD"].copy()
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
    def __init__(self, config: Optional[ProcessConfig] = None):
        self.config = config or ProcessConfig()
        self.logger = setup_logger(__name__)

    def load(self, source: Any, **kwargs) -> pd.DataFrame:
        if isinstance(source, pd.DataFrame):
            df = source.copy()
            if not all(c in df.columns for c in ["voucher_no", "account_code", "direction"]):
                parsed = _parse_fast_cty_template(df)
                if parsed is not None:
                    return parsed
            return df

        path = str(source)
        if path.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(path, **kwargs)
            if not all(c in df.columns for c in ["voucher_no", "account_code", "direction"]):
                raw_no_hdr = pd.read_excel(path, header=None)
                parsed = _parse_fast_cty_template(raw_no_hdr, source_path=path)
                if parsed is not None:
                    self.logger.info(f"成功将 FAST 模板格式文件反向解析为标准结构: {os.path.basename(path)} ({len(parsed)} 行)")
                    return parsed
            return df
        else:
            df = pd.read_csv(path, **kwargs)
            if not all(c in df.columns for c in ["voucher_no", "account_code", "direction"]):
                raw_no_hdr = pd.read_csv(path, header=None)
                parsed = _parse_fast_cty_template(raw_no_hdr, source_path=path)
                if parsed is not None:
                    return parsed
            return df

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
# 5. 100% 像素级还原图三：老项目 utils/export.py 的 FAST 导入模板导出函数
# ==============================================================================
def _clean_number(val: float) -> Any:
    r = round(float(val), 2)
    return int(r) if r.is_integer() else r


def export_to_sap(df: pd.DataFrame, filename: str, use_db_dedup: bool = False) -> str:
    """
    100% 字节级还原老项目 utils/export.py：
    1. 严格按 split_num = 20000 切分文件，且检查 `vno != belnr` 确保同一凭证绝不被跨文件腰斩；
    2. 汇率列 (第5列) 原样保留 ex 浮点精度，不强行截断为 2 位小数；
    3. 摘要列 (第15列) 使用 _to_str_list 确保 100% 为纯 Python str，彻底根除 pandas 3.x float('nan') 报错。
    """
    out_path = os.path.join(BASE_DIR, filename) if not os.path.isabs(filename) else filename
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if df is None or df.empty:
        wb = openpyxl.Workbook()
        wb.save(out_path)
        return out_path

    df_out = df.copy()

    if use_db_dedup:
        try:
            from utils.update_so_ct import update_voucher_no
            df_out = update_voucher_no(df_out)
        except Exception as e:
            logging.getLogger("SAP2FAST").warning(f"数据库凭证号去重跳过: {e}")

    # 使用 _to_str_list 提取纯 Python 字符串列表，100% 免疫 pandas 3.x 的 float('nan') 混入问题
    vno_arr = _to_str_list(df_out, "voucher_no")
    cpudt_arr = _to_str_list(df_out, "cpudt")
    year_arr = _to_str_list(df_out, "year")
    period_arr = _to_str_list(df_out, "period")
    cur_arr = [c.upper() or "VND" for c in _to_str_list(df_out, "currency")]
    ex_arr = pd.to_numeric(df_out["ex"], errors="coerce").fillna(0.0).to_numpy()
    fast_acc_arr = _to_str_list(df_out, "fast_account")
    lifnr_arr = _to_str_list(df_out, "lifnr")
    kunnr_arr = _to_str_list(df_out, "kunnr")
    dir_arr = [d.upper() or "H" for d in _to_str_list(df_out, "direction")]
    orig_arr = pd.to_numeric(df_out["original_amount"], errors="coerce").fillna(0.0).to_numpy()
    book_arr = pd.to_numeric(df_out["book_amount"], errors="coerce").fillna(0.0).to_numpy()
    txt_arr = _to_str_list(df_out, "text_summary", strip_dot_zero=False)
    zuonr_arr = _to_str_list(df_out, "zuonr", strip_dot_zero=False)
    gid_arr = df_out["group_id"].tolist() if "group_id" in df_out.columns else [None] * len(df_out)

    wk = openpyxl.Workbook()
    sh = wk.active
    split_num = 20000
    file_no = 1
    belnr = ""
    radd = 0
    exported_files = []

    base_stem = out_path[:-14] if out_path.endswith("_1.result.xlsx") else (
        out_path[:-12] if out_path.endswith(".result.xlsx") else out_path
    )

    for idx in range(len(df_out)):
        row = idx + 6
        vno = vno_arr[idx]

        # 与老项目 utils/export.py 第24行完全一致：满 20000 行且当前凭证号与上一行不同时才切分新文件
        if row >= split_num * file_no and vno != belnr:
            save_p = f"{base_stem}_{file_no}.result.xlsx" if len(df_out) >= split_num else out_path
            wk.save(save_p)
            exported_files.append(save_p)
            file_no += 1
            wk = openpyxl.Workbook()
            sh = wk.active
            radd = 6 - row

        target_row = row + radd
        cur = cur_arr[idx]
        pdate = resolve_period_date(cpudt_arr[idx], year_arr[idx], period_arr[idx])

        sh.cell(target_row, 1, "CTY")
        sh.cell(target_row, 2, vno)
        sh.cell(target_row, 3, pdate)
        sh.cell(target_row, 4, cur)
        sh.cell(target_row, 5, 1 if cur == "VND" else float(ex_arr[idx]))

        cell_acc = sh.cell(target_row, 7, fast_acc_arr[idx])
        cell_acc.data_type = "s"

        if cur != "VND":
            sh.cell(target_row, 11 if dir_arr[idx] == "S" else 12, _clean_number(orig_arr[idx]))
        sh.cell(target_row, 13 if dir_arr[idx] == "S" else 14, _clean_number(book_arr[idx]))

        partner = f"{lifnr_arr[idx]}{kunnr_arr[idx]}"
        if partner:
            cell_kh = sh.cell(target_row, 9, partner)
            cell_kh.data_type = "s"

        summary_val = " ".join([txt_arr[idx], zuonr_arr[idx]]).strip()
        if summary_val:
            sh.cell(target_row, 15, summary_val)

        gid = gid_arr[idx]
        if gid is not None and pd.notna(gid) and str(gid).strip() != "":
            sh.cell(target_row, 43, int(float(gid)))
        belnr = vno

    final_p = f"{base_stem}_{file_no}.result.xlsx" if (file_no > 1 or out_path.endswith(".result.xlsx")) else out_path
    wk.save(final_p)
    exported_files.append(final_p)
    logging.getLogger("SAP2FAST").info(f"已导出 FAST 模板文件: {exported_files}")
    return exported_files[0]


# ==============================================================================
# 6. 修复后的 DataReconciler 与 SAPExporter
# ==============================================================================
class DataReconciler:
    def __init__(self, fiscalym: str):
        self.logger = setup_logger(__name__)
        self.fiscalym = str(fiscalym).strip()
        self.calendarym = fiscalym_to_calendarym(self.fiscalym)
        try:
            from db import get_hana_pool, get_mssql_pool
            self.sap_pool = get_hana_pool()
            self.fast_pool = get_mssql_pool()
            self._get_foreign_currency_account_dict()
        except Exception as e:
            self.logger.warning(f"数据库连接初始化失败（离线环境可忽略）: {e}")
            self.sap_pool = None
            self.fast_pool = None
            self.foreign_currency_account = []

    def _get_foreign_currency_account_dict(self) -> None:
        if not self.fast_pool:
            return
        from db import MSSQLSQL
        df = self.fast_pool.execute_query(MSSQLSQL.QUERY_TK)
        df["account_code"] = _clean_str_series(df["account_code"])
        self.foreign_currency_account = df.to_dict(orient="records")

    def _get_sap_fast_arising(self) -> None:
        if not self.sap_pool or not self.fast_pool:
            raise RuntimeError("数据库连接不可用，请使用 --mode local 运行离线转换。")
        from db import HanaSQL, MSSQLSQL
        sapsql = HanaSQL.format_account_arising_month({
            "gjahr": self.fiscalym[:4],
            "monat": self.fiscalym[4:6]
        })
        self.df_sap_account = self.sap_pool.execute_query(sapsql)
        fastsql = MSSQLSQL.format_account_arising_month({"ym": self.calendarym})
        self.df_fast_account = self.fast_pool.execute_query(fastsql)

    def compare_account_diff(self, account_mapping: Optional[dict] = None) -> List[str]:
        self._get_sap_fast_arising()
        df_fast = self.df_fast_account.copy()
        df_sap = self.df_sap_account.copy()
        fast_compare = df_fast.rename(columns={"book_amount": "book_amount_fast"})
        sap_compare = df_sap.rename(columns={"book_amount": "book_amount_sap"})

        if account_mapping is None:
            account_mapping = AccountConfig.load()

        df = sap_compare.copy()
        if "fast_account" not in df.columns:
            df["fast_account"] = pd.NA

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
            fkber_map = pd.DataFrame(fkber_rows).drop_duplicates(subset=["fkber", "account_code"], keep="last")
            df = df.merge(fkber_map, on=["fkber", "account_code"], how="left", suffixes=("", "__new"))
            if "fast_account__new" in df.columns:
                df["fast_account"] = df["fast_account__new"].combine_first(df["fast_account"])
                df = df.drop(columns=["fast_account__new"])

        account_rows = [
            {"account_code": str(k).strip(), "fast_account": str(v).strip()}
            for k, v in (account_mapping.get("account") or {}).items()
        ]
        if account_rows:
            account_map = pd.DataFrame(account_rows).drop_duplicates(subset=["account_code"], keep="last")
            df = df.merge(account_map, on="account_code", how="left", suffixes=("", "__new"))
            if "fast_account__new" in df.columns:
                df["fast_account"] = df["fast_account__new"].combine_first(df["fast_account"])
                df = df.drop(columns=["fast_account__new"])

        df["fast_account"] = _clean_str_series(df["fast_account"].astype(object).fillna(df["account_code"]))

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
        df_diff = df_diff.fillna({"book_amount_sap": 0.0, "book_amount_fast": 0.0})
        df_diff["book_amount_sap"] = df_diff["book_amount_sap"].astype(float)
        df_diff["book_amount_fast"] = df_diff["book_amount_fast"].astype(float)
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

        from db import HanaSQL, MSSQLSQL
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

        sap_voucher_nos = _clean_str_series(df_sap_account_list["voucher_no"]).unique().tolist()
        raw_fast_voucher_nos = _clean_str_series(df_fast_account_list["voucher_no"]).unique().tolist()

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
        return check_exist_voucher_no(missing_voucher_nos, self.calendarym)

    def fetch_sap_to_fast(self) -> Optional[str]:
        voucher_nos = self.compare_account_diff()
        if not voucher_nos:
            self.logger.info(f"会计期间 {self.fiscalym} 无缺失凭证号！")
            return None

        from db import HanaSQL
        step = 500
        dfs = []
        for i in range(0, len(voucher_nos), step):
            batch = voucher_nos[i:i + step]
            sql = HanaSQL.format_voucher_no({
                "voucher_nos": ",".join([f"'{x}'" for x in batch]),
                "gjahr": self.fiscalym[:4]
            })
            df_batch = self.sap_pool.execute_query(sql)
            dfs.append(df_batch)

        df = pd.concat(dfs, axis=0, ignore_index=True)
        processor = DataProcessor(ProcessConfig())
        df = processor.run(df)
        return export_to_sap(df, filename=f"output/{self.fiscalym}_{time.time()}_added_data.xlsx_1.result.xlsx", use_db_dedup=True)


class SAPExporter:
    def __init__(self, cpudt: str = ""):
        self.logger = setup_logger(__name__)
        if cpudt == "":
            cpudt = get_next_fast_date()
        fiscalym = calandarym_to_ficalym(cpudt)
        period = {"gjahr": fiscalym[:4], "monat": fiscalym[4:6]}
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
        processor = DataProcessor(ProcessConfig())
        conn = get_hana_pool()
        while True:
            fiscalym = self.status.current_period["gjahr"] + self.status.current_period["monat"]
            df = conn.execute_query(HanaSQL.format_voucher_by_cpudt(self.status.get_kwargs()))
            if df is not None and not df.empty:
                df = processor.run(df)
                export_to_sap(df, filename=f"output/{fiscalym}_{time.time()}_sync_data.xlsx_1.result.xlsx", use_db_dedup=True)
            if not self.status.next_cpudt():
                break


# ==============================================================================
# 7. 离线工具集：批量转换 / 17列原始样表生成 / 离线平账审计报告
# ==============================================================================
def generate_raw_sap_sample_file() -> str:
    """生成一份涵盖全部 8 步转换规则的 17 列原始 SAP 凭证测试表 (samples/sample_raw_sap.xlsx)"""
    sample_dir = os.path.join(BASE_DIR, "samples")
    os.makedirs(sample_dir, exist_ok=True)
    sample_path = os.path.join(sample_dir, "sample_raw_sap.xlsx")

    rows = [
        # 1. 普通 1借1贷 VND 凭证 + 331111 空客商自动补 A0001
        {"cpudt": "20240301", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000001", "voucher_type": "SA", "account_code": "1001000001", "direction": "H", "book_amount": 1000.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Thanh toan tien hang", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240301", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000001", "voucher_type": "SA", "account_code": "2202000001", "direction": "S", "book_amount": 1000.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Thanh toan tien hang", "zuonr": "", "currency": "VND"},
        # 2. 多借多贷 (2借2贷) + 特别科目组 (6603030001, 1122000001) 规则3拆分 + 规则1配对 (4行拆为5行)
        {"cpudt": "20240302", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000002", "voucher_type": "SA", "account_code": "6603030001", "direction": "H", "book_amount": 600.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Chi phi san xuat", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240302", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000002", "voucher_type": "SA", "account_code": "6603030002", "direction": "H", "book_amount": 400.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Chi phi san xuat", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240302", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000002", "voucher_type": "SA", "account_code": "2202000001", "direction": "S", "book_amount": 300.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Chi phi san xuat", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240302", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000002", "voucher_type": "SA", "account_code": "1122000001", "direction": "S", "book_amount": 700.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Chi phi san xuat", "zuonr": "", "currency": "VND"},
        # 3. USD 外币分录 + 应收/应付 USD 科目自动重分类 (131111->131112, 331111->331112)
        {"cpudt": "20240303", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000003", "voucher_type": "SA", "account_code": "1121000001", "direction": "H", "book_amount": 100.0, "original_amount": 100.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Thu ngoai te", "zuonr": "", "currency": "USD"},
        {"cpudt": "20240303", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000003", "voucher_type": "SA", "account_code": "2202000001", "direction": "S", "book_amount": 100.0, "original_amount": 100.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Thu ngoai te", "zuonr": "", "currency": "USD"},
    ]
    pd.DataFrame(rows).to_excel(sample_path, index=False)
    logging.getLogger("SAP2FAST").info(f"已生成 17 列原始 SAP 测试样表: {sample_path}")
    return sample_path


def audit_local_files(input_pattern: str = "input/*.xlsx", output_dir: str = "output") -> str:
    """离线审计功能：对指定文件进行借贷平账、USD原币平账、多借多贷分组率与科目统计"""
    logger = setup_logger("OfflineAudit")
    patterns = [p.strip() for p in input_pattern.replace(";", ",").split(",") if p.strip()]
    files: List[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(os.path.join(BASE_DIR, pat))))

    if not files:
        logger.warning(f"未找到待审计文件: {input_pattern}")
        return ""

    processor = DataProcessor(ProcessConfig())
    audit_records = []

    for f in files:
        base = os.path.basename(f)
        if base.startswith("~$"):
            continue
        try:
            df = processor.load(f)
            df = ensure_columns(df)
            df = normalize_direction(df)
            is_raw = not df.attrs.get("from_result_template", False)

            total_rows = len(df)
            total_vouchers = df["voucher_no"].nunique()
            h_df = df[df["direction"] == "H"]
            s_df = df[df["direction"] == "S"]

            vnd_h = float(h_df["book_amount"].sum())
            vnd_s = float(s_df["book_amount"].sum())
            vnd_diff = round(vnd_h - vnd_s, 2)

            usd_df = df[_clean_str_series(df["currency"]).str.upper() == "USD"]
            usd_h = float(usd_df[usd_df["direction"] == "H"]["original_amount"].sum())
            usd_s = float(usd_df[usd_df["direction"] == "S"]["original_amount"].sum())
            usd_diff = round(usd_h - usd_s, 2)

            grouped_rows = int(df["group_id"].notna().sum()) if "group_id" in df.columns else 0

            audit_records.append({
                "file_name": base,
                "file_type": "17列原始SAP表" if is_raw else "43列FAST结果模板",
                "total_rows": total_rows,
                "unique_vouchers": total_vouchers,
                "grouped_split_rows": grouped_rows,
                "book_debit_H": vnd_h,
                "book_credit_S": vnd_s,
                "book_diff": vnd_diff,
                "usd_debit_H": usd_h,
                "usd_credit_S": usd_s,
                "usd_diff": usd_diff,
                "balance_status": "PASS" if (abs(vnd_diff) < 0.01 and abs(usd_diff) < 0.05) else "FAIL",
            })
            logger.info(
                f"[审计完成] {base} | 类型={'原始SAP' if is_raw else 'FAST模板'} | "
                f"行数={total_rows} | 凭证数={total_vouchers} | 本位币差额={vnd_diff} | USD差额={usd_diff}"
            )
        except Exception as e:
            logger.error(f"审计文件 {base} 失败: {e}")

    os.makedirs(os.path.join(BASE_DIR, output_dir), exist_ok=True)
    report_path = os.path.join(BASE_DIR, output_dir, "offline_audit_report.csv")
    pd.DataFrame(audit_records).to_csv(report_path, index=False, encoding="utf-8-sig")
    logger.info(f"离线审计汇总报告已输出至: {report_path}")
    return report_path


def reprocess_local_files(input_pattern: str = "input/*.xlsx", output_dir: str = "output") -> List[str]:
    logger = setup_logger("LocalReprocess")
    patterns = [p.strip() for p in input_pattern.replace(";", ",").split(",") if p.strip()]
    files: List[str] = []
    for pat in patterns:
        files.extend(sorted(glob.glob(os.path.join(BASE_DIR, pat))))
    if not files:
        logger.warning(f"未找到匹配的本地文件: {input_pattern}")
        return []

    out_dir = os.path.join(BASE_DIR, output_dir) if not os.path.isabs(output_dir) else output_dir
    os.makedirs(out_dir, exist_ok=True)

    processor = DataProcessor(ProcessConfig())
    out_files: List[str] = []
    for f in files:
        base = os.path.basename(f)
        if base.startswith("~$"):
            continue
        try:
            t0 = time.time()
            df_res = processor.run(f)
            if df_res is None or df_res.empty:
                logger.warning(f"跳过 {base}: 处理后无有效数据。")
                continue

            stem = os.path.splitext(base)[0]
            out_name = f"fixed_{stem}.xlsx" if not base.endswith(".result.xlsx") else f"fixed_{base}"
            out_file = export_to_sap(df_res, filename=os.path.join(output_dir, out_name), use_db_dedup=False)
            out_files.append(out_file)
            logger.info(f"离线转换成功 (耗时 {time.time() - t0:.2f}s): {base} -> {out_file} ({len(df_res)} 行)")
        except Exception as e:
            logger.error(f"处理文件 {f} 出错: {e}", exc_info=True)
    return out_files


# ==============================================================================
# 8. 命令行主入口
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="SAP2FAST (2396fast) 终极重构修复版")
    parser.add_argument(
        "--mode",
        choices=["reconcile", "export", "local", "all"],
        default="local",
        help="运行模式: local=本地离线文件转FAST模板(默认,免连库); reconcile=按月核对; export=按日增量; all=全流程"
    )
    parser.add_argument("--fiscalym", default="202403", help="会计期间 YYYYMM")
    parser.add_argument("--cpudt", default="", help="增量同步日期 YYYYMMDD")
    parser.add_argument("--input", default="input/*.xlsx", help="local 模式的本地文件(支持通配符, 兼容17列原始表与CTY模板表)")
    parser.add_argument("--output", default="output", help="local 模式输出目录")
    parser.add_argument("--generate-sample", action="store_true", help="[离线专用] 生成17列原始SAP测试样表并立即转换为FAST模板")
    parser.add_argument("--audit", action="store_true", help="[离线专用] 对输入文件执行借贷平衡、USD平账与科目分布审计")
    args = parser.parse_args()

    logger = setup_logger("MAIN")
    logger.info(f"启动 SAP2FAST，当前模式: {args.mode}")

    if args.generate_sample:
        sample_file = generate_raw_sap_sample_file()
        reprocess_local_files(input_pattern=sample_file, output_dir=args.output)
        return

    if args.audit:
        audit_local_files(input_pattern=args.input, output_dir=args.output)
        return

    if args.mode == "local":
        reprocess_local_files(input_pattern=args.input, output_dir=args.output)
    elif args.mode == "export":
        SAPExporter(cpudt=args.cpudt).run()
    elif args.mode == "reconcile":
        DataReconciler(fiscalym=args.fiscalym).fetch_sap_to_fast()
    elif args.mode == "all":
        SAPExporter(cpudt=args.cpudt).run()
        DataReconciler(fiscalym=args.fiscalym).fetch_sap_to_fast()


if __name__ == "__main__":
    main()