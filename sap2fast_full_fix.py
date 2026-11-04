#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAP2FAST (2390 / 2396fast) 终极全量修复与扩展版核心工程单文件 (1104 全量验收闭环版)
================================================================================
版本说明 (1104 全量闭环版)：
1. [100% 复现 2.3 #9  FAST 模板导出 (export_to_sap)]：
   - 严格第 1~5 行留空，第 6 行从 A 列 "CTY" 开始逐行写入；
   - 每一行强制写入第 1(CTY)、2(凭证号)、3(过账日期 dd/mm/yyyy)、4(币种)、5(汇率)、
     7(FAST科目)、9(客商 lifnr+kunnr)、11/12(原币借S/贷H)、13/14(本位币借S/贷H)、
     15(摘要 text_summary+zuonr)、43(分组号 group_id，空则写 '')，确保任何文件恒为标准 43 列；
   - 支持可选成本中心列输出 (cost_center_col=6/8/10) 与跨月补记日期策略 (period_end / cpudt)；
   - 严格按 split_num = 20000 且跨凭证边界 (`not safe_eq(vno, belnr)`) 切分 `*_1.result.xlsx`。
2. [100% 复现 2.3 #10 在线凭证号 DB 去重 (update_voucher_no) + 1103 CFO 离线跨期防重 (dedup_vouchers_offline)]：
   - 在线导出时自动查询 FAST 库 c11$000000，若凭证号已存在则循环追加 _A, _B, _C 后缀；
   - 离线模式 (--mode local) 自动扫描跨期/跨文件同名凭证号 (`company_code + year + period` 不同但 `voucher_no` 相同)，
     自动为后续期间追加 `_A`, `_B`, `_C` 后缀，彻底解决 SAP 三维唯一键与 FAST 一维 `so_ct` 冲突！
3. [100% 复现 2.3 #13/#17/#18 + 1104 安全闸 (--dry-run 预览 + 二次确认 + 审计日志)]：
   - `remove_fast_errors`、`modify_fast_database`、`update_fast_account` 均支持 `dry_run=True` SQL 预览、
     交互二次确认 (`--yes` 免确认) 与 `logs/db_maintenance_audit.log` 审计留痕。
4. [100% 落实 CFO 1103 会议拍板与需求 6 (MappingManager)]：
   - 物理移除 `config/account.yaml` 第 172 行重复键 `6051000001: 51182` 并保留代码层 `51181` 锁定；
   - 新增未映射 10 位 SAP 科目主动嗅探告警 (`detect_unmapped_accounts`) 与交互式补录 (`--interactive-mapping`)；
   - 完整实现 6.1 防重添加、6.21 改客商联动改库 (支持配置表范围 `d11,ct00,m11,dmkh` 与 `dry_run`)、
     6.22 改科目多对一安全锁与审计日志。
5. [100% 闭环 CFO 需求 7 (自动登录 + 循环上传 + 批量过账)]：
   - 接通 `LoginHandler.login(fast_user, fast_password, fast_unit)` 全自动填表登录（无账密时支持优雅回退人工确认）；
   - 修复 `CDPClient.attach_upload_file` 中 `logger` 未定义的 `NameError` 阻断性 Bug；
   - 修复 `PostingHandler.process_posting` 中无条件打印“点击 Post TO GL 按钮失败”的误导日志 Bug。
================================================================================
"""

import os
import re
import sys
import json
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

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
    """加载与保存 config/account.yaml 配置文件"""
    config: Dict[str, Any] = {}
    yaml_path: str = os.path.join(BASE_DIR, "config", "account.yaml")

    @classmethod
    def load(cls, yaml_path: Optional[str] = None, force_reload: bool = False) -> Dict[str, Any]:
        if cls.config and not force_reload and (yaml_path is None or yaml_path == cls.yaml_path):
            return cls.config
        if yaml_path is not None:
            cls.yaml_path = yaml_path
        if os.path.exists(cls.yaml_path):
            try:
                with open(cls.yaml_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    cls.config = (yaml.safe_load(content) if yaml else _fallback_yaml_load(content)) or {}
                # CFO 1103 明确指令：6051000001 只映射外部 51181（YAML 重复键已物理删除，此处作双重防御锁定）
                if "account" in cls.config and isinstance(cls.config["account"], dict):
                    if "6051000001" in cls.config["account"]:
                        cls.config["account"]["6051000001"] = "51181"
            except Exception as e:
                logging.getLogger("SAP2FAST").warning(f"加载 account.yaml 失败: {e}")
                cls.config = {}
        return cls.config

    @classmethod
    def save(cls, new_config: Optional[Dict[str, Any]] = None) -> None:
        if new_config is not None:
            cls.config = new_config
        os.makedirs(os.path.dirname(cls.yaml_path), exist_ok=True)
        if yaml:
            with open(cls.yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(cls.config, f, allow_unicode=True, sort_keys=False)
        else:
            raise RuntimeError("保存 YAML 需要安装 PyYAML: pip install pyyaml")


AccountConfig.load()


class Config:
    """兼容老项目 Config 及 upload_to_gl.py / post_to_gl.py 的 get_chrome_config()"""
    def __init__(self, config_path: Optional[str] = None):
        self.config = AccountConfig.load(config_path)

    def get_chrome_config(self) -> Dict[str, Any]:
        chrome_cfg = self.config.get("chrome") or {}
        default_chrome = os.getenv(
            "CHROME_PATH",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            if os.name == "nt"
            else "/ms-playwright/chrome-headless-shell-linux64/chrome-headless-shell"
        )
        return {
            "chrome_path": chrome_cfg.get("chrome_path", default_chrome),
            "remote_debugging_port": int(chrome_cfg.get("remote_debugging_port", os.getenv("CHROME_PORT", "9222"))),
            "max_retries": int(chrome_cfg.get("max_retries", 3)),
            "close_browser_on_exit": bool(chrome_cfg.get("close_browser_on_exit", True)),
            "fast_url": chrome_cfg.get(
                "fast_url",
                os.getenv("FAST_URL", "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx")
            ),
            "fast_user": chrome_cfg.get("fast_user", os.getenv("FAST_USER", "")),
            "fast_password": chrome_cfg.get("fast_password", os.getenv("FAST_PASSWORD", "")),
            "fast_unit": chrome_cfg.get("fast_unit", os.getenv("FAST_UNIT", "CTY")),
        }


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
    # 1104 新增可配置增强项
    offline_voucher_dedup: bool = True
    warn_unmapped_accounts: bool = True
    interactive_mapping: bool = False
    cross_month_date_mode: str = "period_end"  # "period_end" (CFO 拍板默认) 或 "cpudt"
    cost_center_col: Optional[int] = None      # None (默认不输出) 或指定空列 6 / 8 / 10


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
# 2. 日志与通用工具函数 (含 safe_eq, resolve_period_date, 在线/离线凭证号去重)
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


def _write_db_maintenance_audit(action: str, detail: Dict[str, Any]) -> None:
    """写入数据库破坏性操作/维护审计日志 (logs/db_maintenance_audit.log)"""
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    audit_file = os.path.join(log_dir, "db_maintenance_audit.log")
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        **detail,
    }
    with open(audit_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def safe_eq(val: Any, target: Any) -> bool:
    """安全的相等比较（100% 复现 utils/general.py 的 safe_eq，处理 pandas.NA/NaN）"""
    try:
        if pd.isna(val):
            return False
    except (TypeError, ValueError):
        pass
    return str(val).strip() == str(target).strip()


def _clean_str_series(s: pd.Series, strip_dot_zero: bool = True) -> pd.Series:
    """兼容 pandas 2.x / 3.x / 4.x 的字符串列安全清洗函数（零 FutureWarning）"""
    res = s.map(lambda x: "" if (x is None or pd.isna(x)) else str(x).strip()).astype(str)
    if strip_dot_zero:
        res = res.str.replace(r"\.0$", "", regex=True)
    return res.replace({"nan": "", "None": "", "<NA>": ""})


def _to_str_list(df: pd.DataFrame, col: str, strip_dot_zero: bool = True) -> List[str]:
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


def resolve_period_date(cpudt: Any, year: Any, period: Any, cross_month_mode: str = "period_end") -> str:
    """
    解析 FAST 过账日期 (dd/mm/yyyy)，越南财年 4 月起算 (+3 偏移)。
    cross_month_mode:
      - 'period_end' (CFO 1103 拍板默认): 当 cpudt 晚于所属期间自然月时，压缩记在所属期间自然月月末最后一天；
      - 'cpudt': 当 cpudt 晚于所属期间自然月时，直接取 cpudt 当天。
    """
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
        if cross_month_mode == "cpudt":
            return f"{cd}/{cm:02d}/{cy}"
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
        from db import get_mssql_pool, MSSQLSQL
        pool = get_mssql_pool()
        df = pool.execute_query(MSSQLSQL.QUERY_LATEST_DATE)
        if not df.empty and pd.notna(df.iloc[0, 0]):
            return (pd.to_datetime(df.iloc[0, 0]) + timedelta(days=1)).strftime("%Y%m%d")
    except Exception:
        pass
    return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


def get_sap_begin_end_cupdt(fiscalym: str) -> Tuple[str, str]:
    try:
        from db import get_hana_pool, HanaSQL
        pool = get_hana_pool()
        sql = HanaSQL.format_begin_end_cpudt({"gjahr": fiscalym[:4], "monat": fiscalym[4:6]})
        df = pool.execute_query(sql)
        if not df.empty and pd.notna(df.iloc[0, 0]):
            return str(df.iloc[0, 0]), str(df.iloc[0, 1])
    except Exception:
        pass
    ym = fiscalym_to_calendarym(fiscalym)
    first_d = datetime.strptime(ym + "01", "%Y%m%d")
    next_m = (first_d.replace(day=28) + timedelta(days=4)).replace(day=1)
    last_d = next_m - timedelta(days=1)
    return first_d.strftime("%Y%m%d"), last_d.strftime("%Y%m%d")


def _next_suffix_label(idx: int) -> str:
    """生成 A, B, ..., Z, AA, AB 后缀"""
    res = ""
    n = idx
    while n >= 0:
        res = chr(ord("A") + (n % 26)) + res
        n = n // 26 - 1
    return res


def dedup_vouchers_offline(
    df: pd.DataFrame,
    seen_registry: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """
    [1104 新增 - 落实 CFO 1103 会议纪要 §1.3 离线期间防重机制]：
    SAP 凭证号按 (company_code, year, period, voucher_no) 三维唯一，而 FAST 仅按单字段 so_ct (voucher_no) 一维唯一。
    在离线模式下，扫描数据表内及跨文件批次内是否存在“不同期间/公司复用同一 voucher_no”的情况：
    若发现同一 voucher_no 出现在多个不同的 (company_code, year, period) 下，首个期间保留原号，
    后续期间自动追加 `_A`、`_B`、`_C` 后缀（同时确保长度不超过 12 位限制）。
    """
    if df is None or df.empty or "voucher_no" not in df.columns:
        return df

    attrs_backup = dict(df.attrs)
    df_out = df.copy()
    registry = seen_registry if seen_registry is not None else {}

    comp_s = _clean_str_series(df_out["company_code"]) if "company_code" in df_out.columns else pd.Series("2390", index=df_out.index)
    yr_s = _clean_str_series(df_out["year"]) if "year" in df_out.columns else pd.Series("", index=df_out.index)
    prd_s = _clean_str_series(df_out["period"]) if "period" in df_out.columns else pd.Series("", index=df_out.index)
    vno_s = _clean_str_series(df_out["voucher_no"])

    period_keys = comp_s + "_" + yr_s + "_" + prd_s
    unique_pairs = pd.DataFrame({"pkey": period_keys, "vno": vno_s}).drop_duplicates()

    remap: Dict[Tuple[str, str], str] = {}
    used_vnos: Set[str] = set(registry.keys()) | set(vno_s.unique())
    renamed_count = 0

    for _, row in unique_pairs.iterrows():
        pkey = row["pkey"]
        vno = row["vno"]
        if not vno:
            continue
        if vno not in registry:
            registry[vno] = pkey
            remap[(pkey, vno)] = vno
        elif registry[vno] == pkey:
            remap[(pkey, vno)] = vno
        else:
            suffix_idx = 0
            while True:
                sfx = f"_{_next_suffix_label(suffix_idx)}"
                max_base_len = max(1, 12 - len(sfx))
                candidate = f"{vno[:max_base_len]}{sfx}"
                if candidate not in used_vnos and candidate not in registry:
                    registry[candidate] = pkey
                    used_vnos.add(candidate)
                    remap[(pkey, vno)] = candidate
                    renamed_count += 1
                    break
                suffix_idx += 1

    if renamed_count > 0:
        df_out["voucher_no"] = [
            remap.get((pk, vn), vn) for pk, vn in zip(period_keys.tolist(), vno_s.tolist())
        ]
        setup_logger("OfflineVoucherDedup").info(
            f"离线跨期凭证号去重完成：自动为 {renamed_count} 组跨期同名凭证追加 _A/_B 后缀"
        )

    df_out.attrs.update(attrs_backup)
    return df_out


def update_voucher_no(df: pd.DataFrame) -> pd.DataFrame:
    """
    [修复 2.3 #10] 100% 复现 utils/update_so_ct.py 的凭证号数据库查重与 _A/_B/_C 后缀更新逻辑：
    分批查询 FAST 全局表 c11$000000，若 voucher_no 已存在，则从字符 'A' 开始尝试 f"{v_no}_{suffix_char}"，
    直到在 c11$000000 中不存在为止（离线无法连库时自动降级为离线跨期查重并秒级返回）。
    """
    if df is None or df.empty or "voucher_no" not in df.columns:
        return df

    logger = setup_logger("UpdateVoucherNo")
    df_out = dedup_vouchers_offline(df.copy())
    voucher_no_list = _clean_str_series(df_out["voucher_no"]).unique().tolist()
    voucher_no_list = [v for v in voucher_no_list if v]
    if not voucher_no_list:
        return df_out

    try:
        from db import get_mssql_pool, MSSQLSQL
        pool = get_mssql_pool()
        batch_len = 1000
        renamed_count = 0

        for i in range(0, len(voucher_no_list), batch_len):
            batch = voucher_no_list[i:i + batch_len]
            placeholders = ",".join(["%s"] * len(batch))
            query = MSSQLSQL.format_query_voucher_by_no(placeholders)
            found_df = pool.execute_query(query, tuple(batch))
            found_set = set(_clean_str_series(found_df["voucher_no"]).tolist()) if not found_df.empty else set()

            for v_no in batch:
                if v_no in found_set:
                    suffix_idx = 0
                    while True:
                        sfx = _next_suffix_label(suffix_idx)
                        suffix_voucher_no = f"{v_no}_{sfx}"
                        suffix_query = MSSQLSQL.format_query_voucher_by_no("%s")
                        suffix_result = pool.execute_query(suffix_query, (suffix_voucher_no,))
                        if suffix_result.empty:
                            df_out.loc[df_out["voucher_no"] == v_no, "voucher_no"] = suffix_voucher_no
                            renamed_count += 1
                            break
                        suffix_idx += 1

        if renamed_count > 0:
            logger.info(f"update_voucher_no 去重完成：共为 {renamed_count} 张已存在凭证追加 _A/_B 后缀")
    except Exception as e:
        logger.warning(f"无法连接 FAST 数据库执行在线查重（已自动执行离线跨期防重）: {e}")

    return df_out


def check_exist_voucher_no(missing_voucher_nos: List[str], calendarym: str) -> List[str]:
    """[复现 2.3 #11] 100% 复现 utils/update_so_ct.py 的 check_exist_voucher_no"""
    if not missing_voucher_nos:
        return []
    result = list(missing_voucher_nos)
    try:
        from db import get_mssql_pool
        pool = get_mssql_pool()
        step = 500
        for i in range(0, len(missing_voucher_nos), step):
            batch = missing_voucher_nos[i:i + step]
            vouchers = ",".join([f"('%{v}%')" for v in batch])
            with pool.cursor() as cursor:
                sql = f"""
                DECLARE @tmp TABLE (pat NVARCHAR(20));
                INSERT INTO @tmp VALUES {vouchers};
                SELECT LTRIM(so_ct) AS voucher_no FROM [m11${calendarym}] t
                WHERE EXISTS (
                    SELECT 1 FROM @tmp p WHERE t.so_ct LIKE p.pat
                );
                """
                cursor.execute(sql)
                tmp = cursor.fetchall()
                if not tmp:
                    continue
                for p in tmp:
                    _p = str(p[0]).strip()
                    if "_" in _p:
                        _p = _p.split("_")[0] if len(_p.split("_")[0]) > 3 else _p.split("_")[1]
                    if "-" in _p:
                        _p = _p.split("-")[0] if len(_p.split("-")[0]) > 3 else _p.split("-")[1]
                    if _p in result:
                        result.remove(_p)
    except Exception:
        pass
    return result


# ==============================================================================
# 3. 双格式智能解析器：支持读取「17列原始SAP表」与「43列无表头CTY模板表」
# ==============================================================================
REQUIRED_COLUMNS = [
    "company_code", "year", "period", "voucher_no", "currency", "voucher_type", "cpudt",
    "account_code", "direction", "original_amount", "book_amount",
    "cost_center", "fkber", "lifnr", "kunnr",
    "text_summary",
]


def _parse_fast_cty_template(raw_no_header_df: pd.DataFrame, source_path: str = "") -> Optional[pd.DataFrame]:
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
        "company_code": os.getenv("COMPANY_CODE", "2390"),
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
# 4. 核心数据转换引擎 (DataProcessor 8 步管线 + 未映射科目主动检测)
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
        "company_code": os.getenv("COMPANY_CODE", "2390"),
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


def detect_unmapped_accounts(df: pd.DataFrame) -> List[Dict[str, str]]:
    """
    [1104 新增 - 落实 CFO 1103 会议纪要 §2.1 & README §3.2 未映射 10 位 SAP 科目主动筛查]：
    检测转换后 fast_account 仍保留为 8~10 位原始 SAP 科目代码的未映射记录。
    """
    if df is None or df.empty or "account_code" not in df.columns or "fast_account" not in df.columns:
        return []
    acc_s = _clean_str_series(df["account_code"])
    fast_s = _clean_str_series(df["fast_account"])
    fkber_s = _clean_str_series(df["fkber"]) if "fkber" in df.columns else pd.Series("", index=df.index)

    unmapped_mask = (acc_s == fast_s) & (acc_s.str.len() >= 8) & (acc_s.str.isdigit())
    if not unmapped_mask.any():
        return []

    sub = pd.DataFrame({
        "account_code": acc_s[unmapped_mask],
        "fkber": fkber_s[unmapped_mask],
    }).drop_duplicates()
    return sub.to_dict(orient="records")


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
            subset=["account_code"], keep="first"
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

    # CFO 1103 拍板：6051000001 统一只映射外部 51181
    df.loc[df["account_code"] == "6051000001", "fast_account"] = "51181"

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
    if not df_rest.empty:
        df_rest["group_id"] = pd.to_numeric(df_rest["group_id"], errors="coerce").astype("Int64")
        df_rest["group_kind"] = df_rest["group_kind"].fillna("").astype(str)
        final_df = pd.concat([grouped_df, df_rest], ignore_index=True)
    else:
        final_df = grouped_df
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

        unmapped = detect_unmapped_accounts(out)
        if unmapped and cfg.warn_unmapped_accounts:
            self.logger.warning(f"发现 {len(unmapped)} 组未映射的 10 位 SAP 科目: {unmapped}")
            if cfg.interactive_mapping and sys.stdin and sys.stdin.isatty():
                mgr = MappingManager()
                for item in unmapped:
                    sap_c = item["account_code"]
                    fkb = item["fkber"] or None
                    ans = input(f"请输入 SAP 科目 {sap_c} (fkber={fkb}) 对应的越南 FAST 科目 (回车跳过): ").strip()
                    if ans:
                        mgr.add_account_mapping(sap_c, ans, fkber=fkb)
                        mask = (out["account_code"] == sap_c) & ((out["fkber"] == (fkb or "")) if fkb else True)
                        out.loc[mask, "fast_account"] = ans
                unmapped = detect_unmapped_accounts(out)
        out.attrs["unmapped_accounts"] = unmapped

        if cfg.special_transforms:
            out = apply_special_transforms(out, cfg)
        out = apply_multi_debit_credit_grouping(out, cfg)
        if cfg.offline_voucher_dedup:
            out = dedup_vouchers_offline(out)
        out = apply_vnd_zero_original(out, cfg)
        usd_res = check_usd_balance(out, cfg)
        if not usd_res.ok:
            self.logger.warning(f"USD 平衡校验提示: {usd_res.message}")

        # 严格锁定前 20 列顺序，使得 df.itertuples(index=False) 的下标 data[0]..data[19] 与老项目 utils/export.py 100% 一致
        std_col_order = [
            "cpudt", "company_code", "year", "period", "voucher_no", "voucher_type",
            "account_code", "direction", "book_amount", "original_amount", "cost_center",
            "fkber", "lifnr", "kunnr", "text_summary", "zuonr", "currency",
            "fast_account", "ex", "group_id"
        ]
        extra_cols = [c for c in out.columns if c not in std_col_order]
        attrs_backup = dict(out.attrs)
        out = out[std_col_order + extra_cols]
        out.attrs.update(attrs_backup)
        return out

    def run(self, source: Any, **kwargs) -> pd.DataFrame:
        return self._process(self.load(source, **kwargs))


# ==============================================================================
# 5. [100% 复现 2.3 #9] 字节级对齐老项目 utils/export.py 的 FAST 模板导出函数
# ==============================================================================
def _clean_number(val: float) -> Any:
    r = round(float(val), 2)
    return int(r) if r.is_integer() else r


def export_to_sap(
    df: pd.DataFrame,
    filename: str = "",
    use_db_dedup: bool = True,
    cross_month_date_mode: str = "period_end",
    cost_center_col: Optional[int] = None,
) -> List[str]:
    """
    [100% 字节级复现 utils/export.py]：
    1. 第 1~5 行留空，从第 6 行 (start=6) 开始写入；
    2. 每一行固定写入第 1(CTY), 2(voucher_no), 3(date), 4(currency), 5(ex), 7(fast_account),
       9(lifnr+kunnr), 11/12(原币S/H), 13/14(本位币S/H), 15(summary), 43(group_id，空则写 '')；
       确保生成的每一个 Sheet 的 max_column 恒等于 43！
    3. 达到 split_num = 20000 行且 `not safe_eq(vno, belnr)` 时切分新文件 `filename + '_%d.result.xlsx' % file_no`。
    """
    if df is None or df.empty:
        return []

    out_path = os.path.join(BASE_DIR, filename) if (filename and not os.path.isabs(filename)) else (filename or os.path.join(BASE_DIR, "output", "fast_export.xlsx"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    df_out = df.copy()
    if use_db_dedup:
        df_out = update_voucher_no(df_out)
    else:
        df_out = dedup_vouchers_offline(df_out)

    vno_arr = _to_str_list(df_out, "voucher_no")
    cpudt_arr = _to_str_list(df_out, "cpudt")
    year_arr = _to_str_list(df_out, "year")
    period_arr = _to_str_list(df_out, "period")
    cur_arr = [c.upper() or "VND" for c in _to_str_list(df_out, "currency")]
    ex_arr = pd.to_numeric(df_out["ex"], errors="coerce").fillna(0.0).to_numpy()
    fast_acc_arr = _to_str_list(df_out, "fast_account")
    lifnr_arr = _to_str_list(df_out, "lifnr")
    kunnr_arr = _to_str_list(df_out, "kunnr")
    cc_arr = _to_str_list(df_out, "cost_center")
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
    exported_files: List[str] = []

    base_filename = re.sub(r"_\d+\.result\.xlsx$", "", out_path)
    if base_filename.endswith(".result.xlsx"):
        base_filename = base_filename[:-12]

    for idx in range(len(df_out)):
        row = idx + 6
        vno = vno_arr[idx]

        if row >= split_num * file_no and not safe_eq(vno, belnr):
            save_p = f"{base_filename}_{file_no}.result.xlsx"
            wk.save(save_p)
            exported_files.append(save_p)
            file_no += 1
            wk = openpyxl.Workbook()
            sh = wk.active
            radd = 6 - row

        target_row = row + radd
        currency = cur_arr[idx]
        pdate = resolve_period_date(cpudt_arr[idx], year_arr[idx], period_arr[idx], cross_month_mode=cross_month_date_mode)

        sh.cell(target_row, 1, "CTY")
        sh.cell(target_row, 2, vno)
        sh.cell(target_row, 3, pdate)
        sh.cell(target_row, 4, currency)
        sh.cell(target_row, 5, 1 if currency == "VND" else float(ex_arr[idx]))

        if cost_center_col in (6, 8, 10) and cc_arr[idx]:
            sh.cell(target_row, cost_center_col, cc_arr[idx])

        cell_acc = sh.cell(target_row, 7, fast_acc_arr[idx])
        cell_acc.data_type = "s"

        if currency != "VND":
            sh.cell(target_row, 11 if dir_arr[idx] == "S" else 12, _clean_number(orig_arr[idx]))
        sh.cell(target_row, 13 if dir_arr[idx] == "S" else 14, _clean_number(book_arr[idx]))

        cell_kh = sh.cell(target_row, 9, f"{lifnr_arr[idx]}{kunnr_arr[idx]}")
        cell_kh.data_type = "s"

        sh.cell(target_row, 15, " ".join([txt_arr[idx], zuonr_arr[idx]]).strip())

        gid = gid_arr[idx]
        sh.cell(target_row, 43, int(float(gid)) if (gid is not None and pd.notna(gid) and str(gid).strip() != "") else "")
        belnr = vno

    final_p = f"{base_filename}_{file_no}.result.xlsx"
    wk.save(final_p)
    exported_files.append(final_p)
    logging.getLogger("SAP2FAST").info(f"成功导出 FAST 专用 43 列模板文件: {exported_files}")
    return exported_files


# ==============================================================================
# 6. [修复 2.3 #12, #13, #14 + 1104 安全闸] DataReconciler 与 SAPExporter
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
            account_map = pd.DataFrame(account_rows).drop_duplicates(subset=["account_code"], keep="first")
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

    def remove_fast_errors(
        self,
        gjahr: int,
        monat: int,
        so_ct: Set[str],
        dry_run: bool = False,
        confirm: bool = True,
    ) -> int:
        """
        [修复 2.3 #13 + 1104 安全闸] 完整实现移除 FAST 差异科目/错账凭证数据，
        支持 dry_run SQL 预览、二次确认与审计日志。
        """
        clean_vnos = sorted(set(str(v).strip() for v in (so_ct or set()) if str(v).strip()))
        if not clean_vnos:
            self.logger.info("remove_fast_errors: 传入的待删凭证集合为空，无需删除。")
            return 0

        from db import MSSQLSQL
        ym = fiscalym_to_calendarym(f"{int(gjahr):04d}{int(monat):02d}")
        voucher_list_str = ",".join([f"'{v}'" for v in clean_vnos])

        update_sql = MSSQLSQL.format_update_stt_rec({"ym": ym, "so_cts": voucher_list_str})
        delete_sql = MSSQLSQL.format_delete_stt_rec({"ym": ym})

        _write_db_maintenance_audit(
            "REMOVE_FAST_ERRORS",
            {
                "fiscal_ym": f"{int(gjahr):04d}{int(monat):02d}",
                "calendar_ym": ym,
                "voucher_count": len(clean_vnos),
                "vouchers": clean_vnos,
                "dry_run": dry_run,
            },
        )

        if dry_run:
            self.logger.info(
                f"[DryRun 安全预览] remove_fast_errors 将在期间 {ym} 级联删除 {len(clean_vnos)} 张凭证: {clean_vnos}"
            )
            self.logger.info(f"[DryRun SQL 1] {update_sql.strip()}")
            return len(clean_vnos)

        if not confirm and sys.stdin and sys.stdin.isatty():
            ans = input(f"[安全确认] 确认从 FAST 库 (期间 {ym}) 级联删除 {len(clean_vnos)} 张凭证吗？(y/N): ").strip().lower()
            if ans != "y":
                self.logger.warning("用户取消了 remove_fast_errors 删除操作。")
                return 0

        if not self.fast_pool:
            raise RuntimeError("FAST 数据库连接不可用，无法执行 remove_fast_errors。")

        with self.fast_pool.begin_transaction() as tx:
            tx.execute(update_sql)
            tx.execute(delete_sql)
            tx.commit()

        self.logger.info(f"成功从 FAST 数据库 (期间 {ym}) 移除 {len(clean_vnos)} 张错账凭证！")
        return len(clean_vnos)

    def fetch_sap_to_fast(self) -> List[str]:
        voucher_nos = self.compare_account_diff()
        if not voucher_nos:
            self.logger.info(f"会计期间 {self.fiscalym} 无缺失凭证号！")
            return []

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
        return export_to_sap(df, filename=f"output/{self.fiscalym}_{time.time()}_added_data.xlsx", use_db_dedup=True)


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
                export_to_sap(df, filename=f"output/{fiscalym}_{time.time()}_sync_data.xlsx", use_db_dedup=True)
            if not self.status.next_cpudt():
                break


# ==============================================================================
# 7. [修复 2.3 #17 & #18 + 1104 安全闸 + CFO 需求 6] FAST 库维护与 MappingManager
# ==============================================================================
def modify_fast_database(
    account_no: str,
    years: Optional[List[str]] = None,
    dry_run: bool = False,
    confirm: bool = True,
) -> Dict[str, Any]:
    """
    [修复 2.3 #17 + 1104 安全闸] 100% 复现 modify_fast_database.py，
    增加 dry_run 预览清单、二次确认开关与审计日志留痕。
    """
    logger = setup_logger("ModifyFastDB")
    update_tables = ["c11$000000", "r00${ym}", "d11${ym}", "o11$000000", "m11${ym}", "i11${ym}"]
    months = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
    yrs = years or ["2024", "2025"]
    acc = account_no.strip()

    sql_statements: List[str] = []
    for y in yrs:
        for mm in months:
            ym = f"{y}{mm}"
            for tbl in update_tables:
                table_real = tbl.replace("{ym}", ym)
                sql_statements.append(
                    f"DELETE FROM {table_real} WHERE stt_rec IN (SELECT DISTINCT stt_rec FROM ct00 WHERE tk='{acc}');"
                )
    sql_statements.append(f"DELETE FROM ct00 WHERE tk='{acc}';")

    _write_db_maintenance_audit(
        "DELETE_FAST_ACCOUNT",
        {"fast_account": acc, "years": yrs, "sql_count": len(sql_statements), "dry_run": dry_run},
    )

    if dry_run:
        logger.info(f"[DryRun 安全预览] modify_fast_database 将对科目 '{acc}' 执行 {len(sql_statements)} 条级联 DELETE 语句")
        for s in sql_statements[:6]:
            logger.info(f"  [DryRun SQL] {s}")
        return {"account": acc, "dry_run": True, "sql_count": len(sql_statements), "sql_preview": sql_statements[:10]}

    if not confirm and sys.stdin and sys.stdin.isatty():
        ans = input(f"[安全确认] 即将彻底删除 FAST 库 ({yrs}) 中科目 '{acc}' 的关联凭证，共 {len(sql_statements)} 条语句，确认执行吗？(y/N): ").strip().lower()
        if ans != "y":
            logger.warning("用户取消了 modify_fast_database 操作。")
            return {"account": acc, "dry_run": False, "cancelled": True, "sql_count": 0}

    from db import get_mssql_pool
    pool = get_mssql_pool()
    for sql in sql_statements[:-1]:
        try:
            pool.execute_none(sql)
        except Exception as e:
            logger.warning(f"清理表跳过或异常: {e}")
    pool.execute_none(sql_statements[-1])
    logger.info(f"modify_fast_database 完成：已彻底删除 FAST 库中科目 {acc} 的关联凭证记录。")
    return {"account": acc, "dry_run": False, "sql_count": len(sql_statements)}


def update_fast_account(
    old_account: str,
    new_account: str,
    years: Optional[List[str]] = None,
    scope: str = "ALL",
    commit: bool = True,
    dry_run: bool = False,
    confirm: bool = True,
) -> Dict[str, Any]:
    """
    [修复 2.3 #18 + 1104 安全闸] 100% 复现并修复 update_account.py，
    支持真实事务提交、dry_run SQL 预览、二次确认与审计日志。
    """
    logger = setup_logger("UpdateFastAccount")
    yrs = years or ["2024", "2025"]
    months = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]

    sql_usd = [
        "UPDATE r00${yyyymm} SET tk='{new_account}' WHERE tk='{old_account}' AND ma_nt='USD'",
        "UPDATE r00${yyyymm} SET tk_du='{new_account}' WHERE tk_du='{old_account}' AND ma_nt='USD'",
        "UPDATE d11${yyyymm} SET tk='{new_account}' FROM m11${yyyymm} a, d11${yyyymm} b WHERE a.stt_rec=b.stt_rec AND a.ma_nt='USD' AND b.tk='{old_account}'",
        "UPDATE i11${yyyymm} SET d$=REPLACE(d$,'{old_account}','{new_account}') WHERE d$ LIKE '%{old_account}%' AND m$='#10$USD'",
        "UPDATE ct00 SET tk='{new_account}' WHERE tk='{old_account}' AND ma_nt='USD'",
        "UPDATE ct00 SET tk_du='{new_account}' WHERE tk_du='{old_account}' AND ma_nt='USD'",
    ]
    sql_all = [
        "UPDATE r00${yyyymm} SET tk='{new_account}' WHERE tk='{old_account}'",
        "UPDATE r00${yyyymm} SET tk_du='{new_account}' WHERE tk_du='{old_account}'",
        "UPDATE d11${yyyymm} SET tk='{new_account}' FROM m11${yyyymm} a, d11${yyyymm} b WHERE a.stt_rec=b.stt_rec AND b.tk='{old_account}'",
        "UPDATE i11${yyyymm} SET d$=REPLACE(d$,'{old_account}','{new_account}') WHERE d$ LIKE '%{old_account}%'",
        "UPDATE ct00 SET tk='{new_account}' WHERE tk='{old_account}'",
        "UPDATE ct00 SET tk_du='{new_account}' WHERE tk_du='{old_account}'",
    ]

    is_usd_only = str(scope).upper() in ("1", "USD")
    templates = sql_usd if is_usd_only else sql_all
    old_acc = old_account.strip()
    new_acc = new_account.strip()

    statements: List[str] = []
    for y in yrs:
        for m in months:
            yyyymm = f"{y}{m}"
            for tmpl in templates:
                statements.append(tmpl.format(yyyymm=yyyymm, old_account=old_acc, new_account=new_acc))

    is_preview = dry_run or (not commit)
    _write_db_maintenance_audit(
        "UPDATE_FAST_ACCOUNT",
        {
            "old_account": old_acc,
            "new_account": new_acc,
            "years": yrs,
            "scope": "USD" if is_usd_only else "ALL",
            "sql_count": len(statements),
            "dry_run": is_preview,
        },
    )

    if is_preview:
        logger.info(
            f"[DryRun 安全预览] update_fast_account ({old_acc} -> {new_acc}, 范围={'仅USD' if is_usd_only else '全币种'}) "
            f"将生成 {len(statements)} 条 UPDATE 语句"
        )
        for s in statements[:6]:
            logger.info(f"  [DryRun SQL] {s}")
        return {
            "old_account": old_acc,
            "new_account": new_acc,
            "dry_run": True,
            "sql_count": len(statements),
            "sql_preview": statements[:10],
        }

    if not confirm and sys.stdin and sys.stdin.isatty():
        ans = input(
            f"[安全确认] 即将把 FAST 库 ({yrs}) 中科目 '{old_acc}' 批量替换为 '{new_acc}'，共 {len(statements)} 条语句，确认执行吗？(y/N): "
        ).strip().lower()
        if ans != "y":
            logger.warning("用户取消了 update_fast_account 操作。")
            return {"old_account": old_acc, "new_account": new_acc, "dry_run": False, "cancelled": True, "sql_count": 0}

    from db import get_mssql_pool
    pool = get_mssql_pool()
    with pool.begin_transaction() as tx:
        for stmt in statements:
            tx.execute(stmt)
        tx.commit()

    logger.info(f"update_fast_account 完成：{old_acc} -> {new_acc} (范围={'仅USD' if is_usd_only else '全币种'})")
    return {"old_account": old_acc, "new_account": new_acc, "dry_run": False, "sql_count": len(statements)}


class MappingManager:
    """[实现 CFO 需求 6：6.1 / 6.21 / 6.22] SAP 与 FAST 客商及科目映射管理器"""
    def __init__(self, yaml_path: Optional[str] = None):
        self.logger = setup_logger("MappingManager")
        self.audit_log_path = os.path.join(BASE_DIR, "logs", "mapping_change_audit.log")
        self.cfg = AccountConfig.load(yaml_path, force_reload=True)

    def _write_audit_log(self, action: str, detail: Dict[str, Any], keep_log: bool = True) -> None:
        if not keep_log:
            return
        os.makedirs(os.path.dirname(self.audit_log_path), exist_ok=True)
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            **detail
        }
        with open(self.audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def add_partner_mapping(self, sap_partner: str, fast_partner: str, partner_type: str = "vendor", keep_log: bool = True) -> bool:
        node = "vendor" if partner_type.lower() in ("vendor", "lifnr") else "customer"
        mapping = self.cfg.setdefault(node, {})
        existing_fast_codes = set(str(v).strip() for v in mapping.values())

        if fast_partner.strip() in existing_fast_codes:
            self.logger.error(f"[6.1 拦截] 新增失败：FAST 客商代码 '{fast_partner}' 已存在于 {node} 映射表中！")
            return False

        mapping[str(sap_partner).strip()] = str(fast_partner).strip()
        AccountConfig.save(self.cfg)
        self._write_audit_log("6.1_ADD_PARTNER", {"type": node, "sap": sap_partner, "fast": fast_partner}, keep_log)
        self.logger.info(f"[6.1 成功] 已新增客商映射: {sap_partner} -> {fast_partner}")
        return True

    def add_account_mapping(self, sap_account: str, fast_account: str, fkber: Optional[str] = None, keep_log: bool = True) -> bool:
        sap_acc = str(sap_account).strip()
        fast_acc = str(fast_account).strip()
        if fkber:
            fk_node = self.cfg.setdefault("fkber", {}).setdefault(str(fkber).strip(), {})
            if sap_acc in fk_node:
                self.logger.warning(f"[6.1 提醒] 功能范围 {fkber} 下 SAP 科目 {sap_acc} 已存在映射 ({fk_node[sap_acc]})，请改用修改接口。")
                return False
            fk_node[sap_acc] = fast_acc
        else:
            acc_node = self.cfg.setdefault("account", {})
            if sap_acc in acc_node:
                self.logger.warning(f"[6.1 提醒] SAP 科目 {sap_acc} 已存在映射 ({acc_node[sap_acc]})，请改用修改接口。")
                return False
            acc_node[sap_acc] = fast_acc

        AccountConfig.save(self.cfg)
        self._write_audit_log("6.1_ADD_ACCOUNT", {"fkber": fkber, "sap": sap_acc, "fast": fast_acc}, keep_log)
        self.logger.info(f"[6.1 成功] 已新增科目映射: {sap_acc} (fkber={fkber}) -> {fast_acc}")
        return True

    def update_partner_mapping(
        self,
        sap_partner: str,
        new_fast_partner: str,
        partner_type: str = "vendor",
        sync_fast_db: bool = True,
        years: Optional[List[str]] = None,
        sync_tables: Tuple[str, ...] = ("d11", "ct00"),
        dry_run: bool = False,
        keep_log: bool = True,
    ) -> bool:
        """
        [实现 6.21 修改客商映射]：
        1. 判断新 FAST 客商代码是否已存在，存在则提醒拦截；
        2. 修改 config/account.yaml（dry_run=True 时仅预览 SQL 不落盘不改库）；
        3. 同步更新 FAST 数据库客商字段（默认覆盖 d11${ym} 与 ct00，支持扩展 m11${ym} 与 dmkh）；
        4. 默认保留修改审计日志。
        """
        node = "vendor" if partner_type.lower() in ("vendor", "lifnr") else "customer"
        mapping = self.cfg.setdefault(node, {})
        sap_k = str(sap_partner).strip()
        new_fast_v = str(new_fast_partner).strip()

        if new_fast_v in set(str(v).strip() for v in mapping.values()):
            self.logger.warning(f"[6.21 提醒] 目标 FAST 客商代码 '{new_fast_v}' 已存在于配置表中，请核实！")
            return False

        old_fast_v = str(mapping.get(sap_k, sap_k)).strip()

        yrs = years or ["2024", "2025"]
        months = [f"{m:02d}" for m in range(1, 13)]
        sql_list: List[str] = []
        if "d11" in sync_tables:
            for y in yrs:
                for m in months:
                    sql_list.append(f"UPDATE d11${y}{m} SET ma_kh='{new_fast_v}' WHERE RTRIM(ma_kh)='{old_fast_v}'")
        if "m11" in sync_tables:
            for y in yrs:
                for m in months:
                    sql_list.append(f"UPDATE m11${y}{m} SET ma_kh='{new_fast_v}' WHERE RTRIM(ma_kh)='{old_fast_v}'")
        if "ct00" in sync_tables:
            sql_list.append(f"UPDATE ct00 SET ma_kh='{new_fast_v}' WHERE RTRIM(ma_kh)='{old_fast_v}'")
        if "dmkh" in sync_tables:
            sql_list.append(f"UPDATE dmkh SET ma_kh='{new_fast_v}' WHERE RTRIM(ma_kh)='{old_fast_v}'")

        if dry_run:
            self.logger.info(
                f"[6.21 DryRun 预览] 客商映射 {sap_k}: {old_fast_v} -> {new_fast_v} | "
                f"同步表范围={sync_tables} | 将执行 {len(sql_list)} 条 SQL"
            )
            return True

        mapping[sap_k] = new_fast_v
        AccountConfig.save(self.cfg)

        if sync_fast_db and old_fast_v and sql_list:
            try:
                from db import get_mssql_pool
                pool = get_mssql_pool()
                with pool.begin_transaction() as tx:
                    for stmt in sql_list:
                        tx.execute(stmt)
                    tx.commit()
                self.logger.info(f"[6.21 数据库同步] 已将 FAST 库 ({sync_tables}) 中客商 {old_fast_v} 同步更新为 {new_fast_v}")
            except Exception as e:
                self.logger.warning(f"[6.21 数据库同步跳过/失败] {e}")

        self._write_audit_log(
            "6.21_UPDATE_PARTNER",
            {
                "type": node,
                "sap": sap_k,
                "old_fast": old_fast_v,
                "new_fast": new_fast_v,
                "sync_db": sync_fast_db,
                "sync_tables": list(sync_tables),
            },
            keep_log
        )
        return True

    def update_account_mapping(
        self,
        sap_account: str,
        new_fast_account: str,
        fkber: Optional[str] = None,
        force_multi: bool = False,
        sync_fast_db: bool = True,
        years: Optional[List[str]] = None,
        dry_run: bool = False,
        keep_log: bool = True,
    ) -> bool:
        sap_k = str(sap_account).strip()
        new_fast_v = str(new_fast_account).strip()
        acc_node = self.cfg.setdefault("account", {})

        if fkber:
            target_dict = self.cfg.setdefault("fkber", {}).setdefault(str(fkber).strip(), {})
        else:
            target_dict = acc_node

        if sap_k not in target_dict:
            self.logger.error(f"[6.22 拦截] 未找到 SAP 科目 {sap_k} 的现有映射，无法修改。")
            return False

        old_fast_v = str(target_dict[sap_k]).strip()

        related_sap_accounts = [k for k, v in acc_node.items() if str(v).strip() == old_fast_v]
        for fk, sub_dict in (self.cfg.get("fkber") or {}).items():
            if isinstance(sub_dict, dict):
                for k, v in sub_dict.items():
                    if str(v).strip() == old_fast_v:
                        related_sap_accounts.append(f"{fk}:{k}")

        is_one_to_one = len(set(related_sap_accounts)) <= 1
        if not is_one_to_one and not force_multi:
            self.logger.error(
                f"[6.22 安全锁拦截] 原 FAST 科目 '{old_fast_v}' 存在多对一映射关系 (关联 SAP 科目: {related_sap_accounts})！"
                f"原则上禁止单独修改；若确认要同步修改 FAST 库中所有相关科目数据，请附加 --force-multi 参数。"
            )
            return False

        if dry_run:
            self.logger.info(
                f"[6.22 DryRun 预览] SAP 科目 {sap_k} (fkber={fkber}): {old_fast_v} -> {new_fast_v} "
                f"(一对一={is_one_to_one}, 关联科目数={len(set(related_sap_accounts))})"
            )
            if sync_fast_db and old_fast_v != new_fast_v:
                update_fast_account(old_fast_v, new_fast_v, years=years, scope="ALL", commit=False, dry_run=True)
            return True

        target_dict[sap_k] = new_fast_v
        AccountConfig.save(self.cfg)

        if sync_fast_db and old_fast_v != new_fast_v:
            try:
                update_fast_account(old_fast_v, new_fast_v, years=years, scope="ALL", commit=True, dry_run=False, confirm=True)
            except Exception as e:
                self.logger.warning(f"[6.22 数据库同步跳过/失败] {e}")

        self._write_audit_log(
            "6.22_UPDATE_ACCOUNT",
            {
                "fkber": fkber,
                "sap": sap_k,
                "old_fast": old_fast_v,
                "new_fast": new_fast_v,
                "is_one_to_one": is_one_to_one,
                "force_multi": force_multi,
                "sync_db": sync_fast_db,
            },
            keep_log
        )
        self.logger.info(f"[6.22 成功] 已修改科目映射: {sap_k} -> {new_fast_v} (原={old_fast_v})")
        return True


# ==============================================================================
# 8. [实现 CFO 需求 7 - 1104 全自动登录 + 循环上传 + 批量过账闭环]
# ==============================================================================
def _perform_fast_login(
    login_handler: Any,
    config: Dict[str, Any],
    logger: logging.Logger,
    auto_login: bool = True,
    interactive_fallback: bool = True,
) -> bool:
    """
    执行 FAST 网页登录：
    优先使用配置的 fast_user / fast_password / fast_unit 调用 LoginHandler.login() 自动填表登录；
    若未配置账密或自动登录未成功，在交互式终端下优雅回退提示人工确认。
    """
    fast_user = str(config.get("fast_user") or "").strip()
    fast_password = str(config.get("fast_password") or "").strip()
    fast_unit = str(config.get("fast_unit") or "CTY").strip()

    if auto_login and fast_user and fast_password:
        logger.info(f"正在执行 FAST 自动填表登录 (账号: {fast_user}, 单位: {fast_unit})...")
        if login_handler.login(fast_user, fast_password, fast_unit):
            logger.info("FAST 网页自动登录验证通过！")
            return True
        logger.warning("FAST 自动填表登录未能确认跳转，准备检查或切换人工确认...")
    else:
        if not login_handler.navigate_to_login():
            logger.error("导航到登录页面失败")
            return False

    if interactive_fallback and sys.stdin and sys.stdin.isatty():
        logger.info("请确认完成 FAST 网页登录，按 Enter 键继续...")
        input()
        return True

    if fast_user and fast_password:
        return login_handler.is_logged_in()

    logger.error("未提供 FAST_USER / FAST_PASSWORD 且当前为非交互模式，无法完成登录。")
    return False


def upload_to_fast(
    input_dir: str = "output",
    fast_user: str = "",
    fast_password: str = "",
    fast_unit: str = "",
    auto_login: bool = True,
    interactive_fallback: bool = True,
    headless: bool = False,
) -> int:
    logger = setup_logger("FastAutoUpload")
    config = Config().get_chrome_config()
    if fast_user:
        config["fast_user"] = fast_user
    if fast_password:
        config["fast_password"] = fast_password
    if fast_unit:
        config["fast_unit"] = fast_unit

    chrome_path = config.get("chrome_path", "")
    remote_debugging_port = int(config.get("remote_debugging_port", 9222))
    fast_url = config.get("fast_url", "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx")

    if not chrome_path or not os.path.exists(chrome_path):
        logger.error(f"Chrome 路径无效: {chrome_path}")
        return 1

    from utils.chrome_launcher import ChromeLauncher
    from utils.cdp_client import CDPClient
    from utils.login_handler import LoginHandler
    from utils.upload_handler import UploadHandler

    chrome_launcher = ChromeLauncher(chrome_path, remote_debugging_port)
    cdp_client = None
    try:
        if not chrome_launcher.start_chrome(headless=headless):
            logger.error("Chrome 启动失败")
            return 1

        cdp_client = CDPClient(f"http://localhost:{remote_debugging_port}")
        if not cdp_client.connect_to_page():
            logger.error("连接 CDP 失败")
            return 1

        login_handler = LoginHandler(cdp_client, login_url=fast_url)
        upload_handler = UploadHandler(cdp_client)

        if not _perform_fast_login(
            login_handler,
            config,
            logger,
            auto_login=auto_login,
            interactive_fallback=interactive_fallback,
        ):
            return 1

        upload_handler.navigate_to_upload_page()

        target_dir = os.path.join(BASE_DIR, input_dir) if not os.path.isabs(input_dir) else input_dir
        files = sorted(glob.glob(os.path.join(target_dir, "*.xlsx")))
        uploaded_dir = os.path.join(target_dir, "uploaded")
        os.makedirs(uploaded_dir, exist_ok=True)

        for f in files:
            if os.path.basename(f).startswith("~$"):
                continue
            ok = upload_handler.upload_file(f)
            if ok:
                logger.info(f"已成功上传文件: {f}")
                shutil.move(f, os.path.join(uploaded_dir, os.path.basename(f)))
                time.sleep(2)
            else:
                logger.error(f"上传文件失败: {f}")
                return 1
    except Exception as e:
        logger.error(f"自动上传发生错误: {e}", exc_info=True)
        return 1
    finally:
        if cdp_client:
            cdp_client.close()
    return 0


def postToGl(
    fast_user: str = "",
    fast_password: str = "",
    fast_unit: str = "",
    auto_login: bool = True,
    interactive_fallback: bool = True,
    headless: bool = False,
) -> int:
    logger = setup_logger("FastAutoPost")
    config = Config().get_chrome_config()
    if fast_user:
        config["fast_user"] = fast_user
    if fast_password:
        config["fast_password"] = fast_password
    if fast_unit:
        config["fast_unit"] = fast_unit

    chrome_path = config.get("chrome_path", "")
    remote_debugging_port = int(config.get("remote_debugging_port", 9222))
    max_retries = int(config.get("max_retries", 3))
    fast_url = config.get("fast_url", "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx")

    if not chrome_path or not os.path.exists(chrome_path):
        logger.error(f"Chrome 路径无效: {chrome_path}")
        return 1

    from utils.chrome_launcher import ChromeLauncher
    from utils.cdp_client import CDPClient
    from utils.login_handler import LoginHandler
    from utils.posting_handler import PostingHandler

    chrome_launcher = ChromeLauncher(chrome_path, remote_debugging_port)
    cdp_client = None
    try:
        if not chrome_launcher.start_chrome(headless=headless):
            logger.error("Chrome 启动失败")
            return 1

        cdp_client = CDPClient(f"http://localhost:{remote_debugging_port}")
        if not cdp_client.connect_to_page():
            logger.error("连接 CDP 失败")
            return 1

        login_handler = LoginHandler(cdp_client, login_url=fast_url)
        posting_handler = PostingHandler(cdp_client)

        if not _perform_fast_login(
            login_handler,
            config,
            logger,
            auto_login=auto_login,
            interactive_fallback=interactive_fallback,
        ):
            return 1

        posting_handler.navigate_to_posting_page()
        retry_count = 0
        total_processed = 0
        while retry_count < max_retries:
            if posting_handler.process_posting():
                total_processed += 1
                logger.info(f"成功过账第 {total_processed} 批记录")
                retry_count = 0
                time.sleep(2)
            else:
                retry_count += 1
                time.sleep(2)
        logger.info(f"批量 Post 过账完成，共处理 {total_processed} 批记录")
    except Exception as e:
        logger.error(f"自动过账发生错误: {e}", exc_info=True)
        return 1
    finally:
        if cdp_client:
            cdp_client.close()
    return 0


# ==============================================================================
# 9. 离线工具集：批量转换 / 17列原始样表生成 / 离线平账审计报告
# ==============================================================================
def generate_raw_sap_sample_file() -> str:
    sample_dir = os.path.join(BASE_DIR, "samples")
    os.makedirs(sample_dir, exist_ok=True)
    sample_path = os.path.join(sample_dir, "sample_raw_sap.xlsx")

    rows = [
        {"cpudt": "20240301", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000001", "voucher_type": "SA", "account_code": "1001000001", "direction": "H", "book_amount": 1000.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Thanh toan tien hang", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240301", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000001", "voucher_type": "SA", "account_code": "2202000001", "direction": "S", "book_amount": 1000.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Thanh toan tien hang", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240302", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000002", "voucher_type": "SA", "account_code": "6603030001", "direction": "H", "book_amount": 600.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Chi phi san xuat", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240302", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000002", "voucher_type": "SA", "account_code": "6603030002", "direction": "H", "book_amount": 400.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Chi phi san xuat", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240302", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000002", "voucher_type": "SA", "account_code": "2202000001", "direction": "S", "book_amount": 300.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Chi phi san xuat", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240302", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000002", "voucher_type": "SA", "account_code": "1122000001", "direction": "S", "book_amount": 700.0, "original_amount": 0.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Chi phi san xuat", "zuonr": "", "currency": "VND"},
        {"cpudt": "20240303", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000003", "voucher_type": "SA", "account_code": "1121000001", "direction": "H", "book_amount": 100.0, "original_amount": 100.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Thu ngoai te", "zuonr": "", "currency": "USD"},
        {"cpudt": "20240303", "company_code": "2390", "year": "2024", "period": "03", "voucher_no": "1000000003", "voucher_type": "SA", "account_code": "2202000080", "direction": "S", "book_amount": 100.0, "original_amount": 100.0, "cost_center": "", "fkber": "1000", "lifnr": "", "kunnr": "", "text_summary": "Thu ngoai te", "zuonr": "", "currency": "USD"},
    ]
    pd.DataFrame(rows).to_excel(sample_path, index=False)
    logging.getLogger("SAP2FAST").info(f"已生成 17 列原始 SAP 测试样表: {sample_path}")
    return sample_path


def audit_local_files(input_pattern: str = "input/*.xlsx", output_dir: str = "output") -> str:
    logger = setup_logger("OfflineAudit")
    patterns = [p.strip() for p in input_pattern.replace(";", ",").split(",") if p.strip()]
    files: List[str] = []
    for pat in patterns:
        full_pat = pat if os.path.isabs(pat) else os.path.join(BASE_DIR, pat)
        files.extend(sorted(glob.glob(full_pat)))

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
                "book_credit_H": vnd_h,
                "book_debit_S": vnd_s,
                "book_diff": vnd_diff,
                "usd_credit_H": usd_h,
                "usd_debit_S": usd_s,
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


def reprocess_local_files(
    input_pattern: str = "input/*.xlsx",
    output_dir: str = "output",
    process_cfg: Optional[ProcessConfig] = None,
) -> List[str]:
    logger = setup_logger("LocalReprocess")
    patterns = [p.strip() for p in input_pattern.replace(";", ",").split(",") if p.strip()]
    files: List[str] = []
    for pat in patterns:
        full_pat = pat if os.path.isabs(pat) else os.path.join(BASE_DIR, pat)
        files.extend(sorted(glob.glob(full_pat)))
    if not files:
        logger.warning(f"未找到匹配的本地文件: {input_pattern}")
        return []

    out_dir = os.path.join(BASE_DIR, output_dir) if not os.path.isabs(output_dir) else output_dir
    os.makedirs(out_dir, exist_ok=True)

    cfg = process_cfg or ProcessConfig()
    processor = DataProcessor(cfg)
    seen_voucher_registry: Dict[str, str] = {}
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

            if cfg.offline_voucher_dedup:
                df_res = dedup_vouchers_offline(df_res, seen_registry=seen_voucher_registry)

            out_name = f"fixed_{base}"
            exported = export_to_sap(
                df_res,
                filename=os.path.join(output_dir, out_name),
                use_db_dedup=False,
                cross_month_date_mode=cfg.cross_month_date_mode,
                cost_center_col=cfg.cost_center_col,
            )
            out_files.extend(exported)
            logger.info(f"离线转换成功 (耗时 {time.time() - t0:.2f}s): {base} -> {exported} ({len(df_res)} 行)")
        except Exception as e:
            logger.error(f"处理文件 {f} 出错: {e}", exc_info=True)
    return out_files


# ==============================================================================
# 10. 命令行主入口
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="SAP2FAST (2390/2396fast) 1104 终极全量修复版")
    parser.add_argument(
        "--mode",
        choices=[
            "local", "export", "reconcile", "all",
            "upload", "post",
            "delete-account", "update-account", "manage-mapping"
        ],
        default="local",
        help="运行模式: local=离线转FAST模板; export=按日增量; reconcile=按月核对; upload=网页上传; post=网页过账; delete-account=按科目删FAST库; update-account=改FAST科目; manage-mapping=新增/修改映射配置"
    )
    parser.add_argument("--fiscalym", default="202403", help="会计期间 YYYYMM")
    parser.add_argument("--cpudt", default="", help="增量同步日期 YYYYMMDD")
    parser.add_argument("--input", default="input/*.xlsx", help="local 模式的输入文件")
    parser.add_argument("--output", default="output", help="输出目录")
    parser.add_argument("--generate-sample", action="store_true", help="生成17列原始SAP样表并转换为FAST模板")
    parser.add_argument("--audit", action="store_true", help="对输入文件执行离线借贷平账审计")

    # 映射与库表维护参数
    parser.add_argument("--sap-code", default="", help="SAP 科目或客商代码")
    parser.add_argument("--fast-code", default="", help="目标 FAST 科目或客商代码")
    parser.add_argument("--old-fast-code", default="", help="待替换的旧 FAST 科目代码")
    parser.add_argument("--fkber", default=None, help="功能范围 (如 1000/2000/3000)")
    parser.add_argument("--map-action", choices=["add-account", "add-partner", "update-account", "update-partner"], default="add-account")
    parser.add_argument("--force-multi", action="store_true", help="允许修改多对一科目映射并同步更新FAST库")
    parser.add_argument("--partner-sync-scope", default="d11,ct00", help="修改客商同步改库范围 (默认 d11,ct00，可选 d11,ct00,m11,dmkh)")

    # 1104 新增安全闸与业务配置参数
    parser.add_argument("--dry-run", action="store_true", help="安全预览模式：仅输出将要执行的 SQL 清单与影响范围，不实际修改数据库")
    parser.add_argument("--yes", action="store_true", help="免交互确认直接执行破坏性库表维护操作")
    parser.add_argument("--interactive-mapping", action="store_true", help="遇到未映射10位SAP科目时交互式提示补录")
    parser.add_argument("--cross-month-date", choices=["period_end", "cpudt"], default="period_end", help="跨月补记凭证日期规则 (默认 period_end 压缩至月末)")
    parser.add_argument("--cost-center-col", type=int, choices=[6, 8, 10], default=None, help="可选将成本中心输出至43列模板的指定空列 (6/8/10)")

    # 1104 网页自动化账密参数
    parser.add_argument("--fast-user", default="", help="FAST 网页登录用户名 (不传则读取配置或环境变量 FAST_USER)")
    parser.add_argument("--fast-password", default="", help="FAST 网页登录密码 (不传则读取配置或环境变量 FAST_PASSWORD)")
    parser.add_argument("--fast-unit", default="CTY", help="FAST 网页登录单位 (默认 CTY)")
    parser.add_argument("--headless", action="store_true", help="使用无头模式启动 Chrome CDP")
    args = parser.parse_args()

    logger = setup_logger("MAIN")
    logger.info(f"启动 SAP2FAST (1104全量版)，当前模式: {args.mode}")

    proc_cfg = ProcessConfig(
        interactive_mapping=args.interactive_mapping,
        cross_month_date_mode=args.cross_month_date,
        cost_center_col=args.cost_center_col,
    )

    if args.generate_sample:
        sample_file = generate_raw_sap_sample_file()
        reprocess_local_files(input_pattern=sample_file, output_dir=args.output, process_cfg=proc_cfg)
        return

    if args.audit:
        audit_local_files(input_pattern=args.input, output_dir=args.output)
        return

    if args.mode == "local":
        reprocess_local_files(input_pattern=args.input, output_dir=args.output, process_cfg=proc_cfg)
    elif args.mode == "export":
        SAPExporter(cpudt=args.cpudt).run()
    elif args.mode == "reconcile":
        DataReconciler(fiscalym=args.fiscalym).fetch_sap_to_fast()
    elif args.mode == "all":
        SAPExporter(cpudt=args.cpudt).run()
        DataReconciler(fiscalym=args.fiscalym).fetch_sap_to_fast()
    elif args.mode == "upload":
        upload_to_fast(
            input_dir=args.output,
            fast_user=args.fast_user,
            fast_password=args.fast_password,
            fast_unit=args.fast_unit,
            headless=args.headless,
        )
    elif args.mode == "post":
        postToGl(
            fast_user=args.fast_user,
            fast_password=args.fast_password,
            fast_unit=args.fast_unit,
            headless=args.headless,
        )
    elif args.mode == "delete-account":
        if not args.fast_code:
            logger.error("请通过 --fast-code 指定要删除的 FAST 科目代码")
            return
        modify_fast_database(args.fast_code, dry_run=args.dry_run, confirm=args.yes)
    elif args.mode == "update-account":
        if not args.old_fast_code or not args.fast_code:
            logger.error("请通过 --old-fast-code 和 --fast-code 指定旧科目与新科目")
            return
        update_fast_account(args.old_fast_code, args.fast_code, commit=not args.dry_run, dry_run=args.dry_run, confirm=args.yes)
    elif args.mode == "manage-mapping":
        mgr = MappingManager()
        sync_tables = tuple(x.strip() for x in args.partner_sync_scope.split(",") if x.strip())
        if args.map_action == "add-account":
            mgr.add_account_mapping(args.sap_code, args.fast_code, fkber=args.fkber)
        elif args.map_action == "add-partner":
            mgr.add_partner_mapping(args.sap_code, args.fast_code)
        elif args.map_action == "update-partner":
            mgr.update_partner_mapping(
                args.sap_code,
                args.fast_code,
                sync_tables=sync_tables,
                dry_run=args.dry_run,
            )
        elif args.map_action == "update-account":
            mgr.update_account_mapping(
                args.sap_code,
                args.fast_code,
                fkber=args.fkber,
                force_multi=args.force_multi,
                dry_run=args.dry_run,
            )


if __name__ == "__main__":
    main()