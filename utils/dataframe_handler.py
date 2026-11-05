# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/dataframe_handler.py
模块定位: SAP 凭证 DataFrame 8 步转换与多借多贷复合凭证拆分处理器
================================================================================
【本文件核心作用】
1. 将 SAP 导出的 17 列原始凭证 DataFrame 转换为越南 FAST 会计准则 (VAS Circular 200) 格式；
2. 实现两级科目映射（功能范围 `fkber` + 普通科目 `account`）、汇兑损益重分类、客商编码兜底、
   外币科目重分类、物料凭证转本位币；
3. 实现多借多贷复合分录拆分为一借一贷（3 大规则 + 递归 + 贪心兜底），生成第 43 列 `group_id`。
================================================================================
"""
import itertools
import os
import numpy as np
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import  List, Optional, Tuple,Callable
import pandas as pd

from config import AccountConfig,ProcessConfig
from utils import resolve_period_date


"""
dataframe_handler
==============
Contains kinds of function that converts data from sap to fast used format.
字段约定
--------
company_code, year, period, voucher_no, currency, voucher_type,
account_code, direction ('H' 借 / 'S' 贷),
original_amount, book_amount, cost_center, fkber,
vendor_code, customer_code, text_summary, lifnr,kunnr

多借多贷分组规则 (3 条规则 + 递归):
  1) 相近科目 (同科目代码 + 行号相邻) + 方向相反 + 金额相等 → pair
  2) 剩余未分组中, 1借1贷 / 1借多贷 / 多借1贷 合计金额一致 → sum_*
  3) 特别科目组 (同科目 H a, S b 且 a > b) → 从 a 拆出 b 给 b, 剩余进入下一轮
  最多递归 max_recursion 次.
"""


# 必要字段要求
REQUIRED_COLUMNS = [
    "company_code", "year", "period", "voucher_no", "currency", "voucher_type","cpudt",
    "account_code", "direction", "original_amount", "book_amount",
    "cost_center", "fkber", "lifnr", "kunnr",
    "text_summary",
]


# ---------------------------------------------------------------------------
#  特殊转化：钩子函数
# ---------------------------------------------------------------------------
def apply_special_transforms(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    """按顺序执行用户提供的特殊加工钩子。"""
    for fn in cfg.special_transforms:
        df = fn(df)
    return df

# 标准借贷方向规范
def normalize_direction(df: pd.DataFrame) -> pd.DataFrame:
    """把 direction 统一成 'H' / 'S' 大写。"""
    df = df.copy()
    df["direction"] = df["direction"].astype(str).str.strip().str.upper()
    return df

# 移除借贷科目一样分录
def remove_non_sense_voucher(df: pd.DataFrame) -> pd.DataFrame:
    """移除借贷科目一样的分录。"""
    df = df.copy()
    # 根据 voucher_no 分组，删除只有一条记录的组
    group_counts = df.groupby("voucher_no").size()
    valid_vouchers = group_counts[group_counts > 1].index
    df = df[df["voucher_no"].isin(valid_vouchers)]
    return df

# 确保必要列存在
def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必要列: {missing}")
    df = df.copy()
    # 这些列都是业务标识符，全流程按字符串处理
    # （避免 pandas 推断为 int64 / float64 后再赋字符串导致 LossySetitemError）
    str_columns = [
        "company_code", "voucher_no", "voucher_type", "account_code",
        "cost_center", "fkber", "lifnr", "kunnr", "currency", "cpudt",
    ]
    for col in str_columns:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"nan": "", "None": ""})
    return df

# 借贷相等控制
def _amount_equal(a: float, b: float, tol: float = 1e-4) -> bool:
    return abs(float(a) - float(b)) <= tol

# 一般转化：按映射表生成 fast_account
def general_transform_accounts( df: pd.DataFrame,account_mapping:dict=None) -> pd.DataFrame:
    # 一会计科目转换\
    if account_mapping is None:
        account_mapping=AccountConfig.config
    try:
        df = df.copy()
        #df.fillna('',inplace=True)
        # 1) fkber + account_code 映射: 把嵌套 dict 摊平为一张小表, 一次 left join
        fkber_rows = []
        for k, v in (account_mapping.get('fkber') or {}).items():
            if isinstance(v, dict):
                for saccount, faccount in v.items():
                    fkber_rows.append({
                        'fkber': str(k),
                        'account_code': str(saccount),
                        'fast_account': str(faccount),
                    })
        if fkber_rows:
            fkber_map = pd.DataFrame(fkber_rows).drop_duplicates(
                subset=['fkber', 'account_code'], keep='last'
            )
            df = df.merge(fkber_map, on=['fkber', 'account_code'],
                          how='left', suffixes=('', '__new'))
            if 'fast_account__new' in df.columns:
                df['fast_account'] = df['fast_account__new'].fillna(df['fast_account'])
                df.drop(columns=['fast_account__new'], inplace=True)

        # 2) account_code 映射: account 优先级更高 (在 fkber 之后覆盖)
        account_rows = [
            {'account_code': str(k), 'fast_account': str(v)}
            for k, v in (account_mapping.get('account') or {}).items()
        ]
        if account_rows:
            account_map = pd.DataFrame(account_rows).drop_duplicates(
                subset=['account_code'], keep='last'
            )
            df = df.merge(account_map, on='account_code',
                          how='left', suffixes=('', '__new'))
            if 'fast_account__new' in df.columns:
                df['fast_account'] = df['fast_account__new'].fillna(df['fast_account'])
                df.drop(columns=['fast_account__new'], inplace=True)

        df['book_amount']=df['book_amount']*100
        # 向量化: 避免逐行 apply (22w 行 ~40s -> <1s)
        _orig = df['original_amount']
        df['ex'] = np.where(_orig != 0, df['book_amount'] / _orig.replace(0, np.nan), 0)

        return df
    except Exception as e:
        raise

# 特殊转化：钩子函数
def special_transform(df: pd.DataFrame) -> pd.DataFrame:
    """
    特殊转换
    :param df: 输入DataFrame
    :return: 处理后的DataFrame

    转换规则：
    1、汇兑损益重分类；
    2、特定客商代码赋值；
    3、特定凭证类型客商代码取消；
    """
    df =df.copy()
    #汇兑损益重分类
    df.loc[(df['fast_account'] == '6351') & (df['direction'] == 'H'), 'fast_account'] = '5152'
    df.loc[(df['fast_account'] == '5152') & (df['direction'] == 'S'), 'fast_account'] = '6351'
    
    #客商重分类
    df.loc[(df['voucher_type'] == 'WL') | (df['voucher_type'] == 'WE'), 'lifnr'] = ''
    df.loc[(df['voucher_type'] == 'WL') | (df['voucher_type'] == 'WE'), 'kunnr'] = ''
    df.loc[(df['fast_account'] == '331111') & (df['lifnr']=='') & (df['kunnr']==''), 'lifnr'] = 'A0001'
    df.loc[(df['fast_account']=='1411') & (df['kunnr']=='') & (df['lifnr']==''),'kunnr']='A0002'
    df.loc[(df['fast_account']=='331112') & (df['lifnr']=='') & (df['kunnr']==''),'lifnr']='A0003'
    df.loc[(df['fast_account']=='338811') & (df['lifnr']=='') & (df['kunnr']==''),'lifnr']='A0004'
    df.loc[(df['fast_account']=='1388118') & (df['kunnr']=='') & (df['lifnr']==''),'kunnr']='A0005'
    df.loc[(df['fast_account']=='13681'),'kunnr']='A0007'
    
    #收入本外币重分类    
    df.loc[(df['currency']=='USD') & (df['fast_account']=='131111'),'fast_account'] = '131112'
    df.loc[(df['currency']=='USD') & (df['fast_account']=='331111'),'fast_account'] = '331112'
    
    # 物料凭证直接转换为VND
    df.loc[df['voucher_type']=='WL','currency'] = 'VND'
    df.loc[df['voucher_type']=='WE','currency'] = 'VND'
    df.loc[df['voucher_no'].str.startswith('072'),'currency'] = 'VND'

    return df


# 多借多贷分组: 3 条规则 + 递归
def _is_special_pair(
    acc_h: str,
    acc_s: str,
    special_account_pairs: Optional[List[Tuple[str, str]]],
) -> bool:
    """规则 3 是否允许对 (acc_h, acc_s) 拆分. None 表示任意同科目. """
    if special_account_pairs is None:
        return acc_h == acc_s
    return (acc_h, acc_s) in special_account_pairs

 
def _apply_rule1(
    g: pd.DataFrame,
    next_id: int,
    max_subset: int = 2,
) -> Tuple[pd.DataFrame, int, int]:
    """
    规则 1: 剩余未分组中, 1借1贷 / 1借多贷 / 多借1贷 合计金额一致 → 配对.
    max_subset: 1借N贷 / N借1贷 中 N 的上限 (默认 3).
    numpy 加速版.
    """
    n = len(g)
    if n < 2:
        return g, next_id, 0
    direction = g["direction"].to_numpy()
    amount = g["book_amount"].to_numpy()
    gid = g["group_id"].to_numpy(object).copy()
    gkind = g["group_kind"].to_numpy(object).copy()
    used = ~pd.isna(gid)

    matched = 0
    h_free_mask = (direction == "H") & (~used)
    s_free_mask = (direction == "S") & (~used)
    h_free = np.where(h_free_mask)[0].tolist()
    s_free = np.where(s_free_mask)[0].tolist()
    h_used = [False] * len(h_free)
    s_used = [False] * len(s_free)

    # 1 借 1 贷 (金额相等; 不要求同科目, 跟原代码语义一致)
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

    # N 借 1 贷 (N=2..max_subset)
    for hi, h in enumerate(list(h_avail)):
        if h not in h_avail:  # 可能已被前面的 1HN 配对掉
            continue
        target = amount[h]
        for n in range(2, min(max_subset, len(s_avail)) + 1):
            found = False
            for combo in itertools.combinations(s_avail, n):
                s_sum = 0.0
                for s in combo:
                    s_sum += amount[s]
                if _amount_equal(s_sum, target):
                    gid[h] = next_id
                    gkind[h] = f"sum_1h{n}s"
                    for s in combo:
                        gid[s] = next_id
                        gkind[s] = f"sum_1h{n}s"
                    next_id += 1
                    matched += 1
                    h_avail.remove(h)
                    for s in combo:
                        s_avail.remove(s)
                    found = True
                    break
            if found:
                break

    # 1 借 N 贷 (N=2..max_subset)
    for si, s in enumerate(list(s_avail)):
        if s not in s_avail:
            continue
        target = amount[s]
        for n in range(2, min(max_subset, len(h_avail)) + 1):
            found = False
            for combo in itertools.combinations(h_avail, n):
                h_sum = 0.0
                for h in combo:
                    h_sum += amount[h]
                if _amount_equal(h_sum, target):
                    gid[s] = next_id
                    gkind[s] = f"sum_{n}h1s"
                    for h in combo:
                        gid[h] = next_id
                        gkind[h] = f"sum_{n}h1s"
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
    
    # print("rule1:")
    # print(g)
    return g, next_id, matched


def _apply_rule2(
    g: pd.DataFrame,
    next_id: int,
    special_account_pairs: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[pd.DataFrame, int, int]:
    """
    规则 3: 特别科目组拆分.
    H 行 a > S 行 b, 拆出 b 给 S 行 b 组成 split 组; 剩余 (a-b) 不标记, 进入下一轮.
    numpy 加速版 (行内访问改 numpy 数组, 但行复制仍走 Series).
    """
    if special_account_pairs is None:
        special_account_pairs = [('6603030001','1122000001'),('6603050001','1122000001'),('2221000001','1002938004'),
        ('1411010001','1401000001'),('1401000001','1403010002'),('1002938001','2202000001'),('2202000001','1002938001')]

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
        a = book_amount[i]
        b = book_amount[partner]
        
        # 新 H 拆出 b 部分       
        
        if a >= b:
            new_h_split = g.iloc[i].copy()
            new_h_split["book_amount"] = b
            new_h_split["original_amount"] = amount[partner]
             # 剩余 (a-b) 不标记
            new_h_remaining = g.iloc[i].copy()
            new_h_remaining["original_amount"] = amount[i] - amount[partner]
            new_h_remaining["book_amount"] = a-b
            rows_to_drop.append(i)
            gid[partner] = next_id
            gkind[partner] = "split"
        else:
            new_h_split = g.iloc[partner].copy()
            new_h_split["book_amount"] = a
            new_h_split["original_amount"] = amount[i]
             # 剩余 (b-a) 不标记
            new_h_remaining = g.iloc[partner].copy()
            new_h_remaining["original_amount"] = amount[partner] - amount[i]
            new_h_remaining["book_amount"] = b-a
            rows_to_drop.append(partner)
            gid[i] = next_id
            gkind[i] = "split"

        new_h_split["group_id"] = next_id
        new_h_split["group_kind"] = "split"       

        used[partner] = True
        used[i] = True
        new_rows.append(new_h_split)
        new_rows.append(new_h_remaining)
        next_id += 1
        matched += 1

    g["group_id"] = gid
    g["group_kind"] = gkind

    if rows_to_drop:
        g = g.drop(rows_to_drop).reset_index(drop=True)
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        # for col in g.columns:
        #     if col not in new_df.columns:
        #         new_df[col] = pd.NA
        # new_df = new_df[g.columns]
        g = pd.concat([g, new_df], ignore_index=True)
    # print("rule2:")
    # print(g)
    
    return g, next_id, matched


def _group_key_for_voucher(
    group: pd.DataFrame,
    group_id_start: int,
    special_account_pairs: Optional[List[Tuple[str, str]]] = None,
    max_recursion: int = 2,
    rule2_max_subset: int = 4,
) -> pd.DataFrame:
    """
    对一个 voucher_no 的子表做分组解析.
    规则顺序: 1) 相近科目配对 → 2) 合计金额一致配对 → 3) 特别科目组拆分.
    规则 3 拆分后剩余的 (a-b) 重新进入下一轮 1-3, 最多递归 max_recursion 次.
    """
    g = group.copy().sort_values("voucher_no").reset_index(drop=True)
    g["group_id"] = pd.NA
    g["group_kind"] = pd.NA
    next_id = group_id_start

    for _ in range(max_recursion + 1):
       
        g, next_id, r1 = _apply_rule1(g, next_id, max_subset=rule2_max_subset)
        g, next_id, r2 = _apply_rule2(g, next_id, special_account_pairs)
        if r1 + r2 == 0:
            break

    return g


def apply_multi_debit_credit_grouping(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    """
    多借多贷分组（3 规则 + 递归）.
    触发条件：同一 voucher_no 下, H 数量 > cfg.multi_min_per_side 且 S 数量 > cfg.multi_min_per_side
             (默认 multi_min_per_side=1, 即 H>1 且 S>1, 至少 2+2 行).
    输出新增列 'group_id' 与 'group_kind':
      - group_id: 全局递增
      - group_kind: 'pair' / 'sum_*' / 'split' / NA

    并行化策略：每个 voucher 独立处理 (worker 内用本地 group_id 1..N),
    主进程最后做一次"本地 -> 全局"ID 重编号, 消除并发导致的 ID 冲突.
    """
    if df.empty:
        return df
    df = df.copy()
    df["group_id"] = pd.NA
    df["group_kind"] = pd.NA

    n = cfg.multi_min_per_side
    counts = df.groupby("voucher_no")["direction"].value_counts().unstack(fill_value=0)
    if "H" not in counts.columns:
        counts["H"] = 0
    if "S" not in counts.columns:
        counts["S"] = 0
    trigger_vouchers = counts[(counts["H"] > n) & (counts["S"] > n)].index.tolist()

    if not trigger_vouchers:
        return df

    trigger_set = set(trigger_vouchers)
    trigger_mask = df["voucher_no"].isin(trigger_set)
    df_trigger = df[trigger_mask]
    df_rest = df[~trigger_mask]

    if df_trigger.empty:
        return df

    # 每个 voucher 切一块, 互相独立
    groups = [g for _, g in df_trigger.groupby("voucher_no", sort=False)]
    args_list = [
        (g, cfg.special_account_pairs, cfg.max_recursion, cfg.rule2_max_subset)
        for g in groups
    ]

    # 触发数过少时, 进程启动开销 > 收益, 直接串行
    PARALLEL_THRESHOLD = 16
    if len(groups) >= PARALLEL_THRESHOLD:
        workers = max(2, min(len(groups), (os.cpu_count() or 4)))
        chunksize = max(1, len(groups) // (workers * 4))
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(
                _group_one_voucher_worker, args_list, chunksize=chunksize
            ))
    else:
        results = [_group_one_voucher_worker(a) for a in args_list]

    grouped_df = pd.concat(results, ignore_index=True)

    # 全局重编号: 把 worker 内的本地 1,2,3... 映射成连续的全局 ID
    grouped_df["group_id"] = pd.to_numeric(grouped_df["group_id"], errors="coerce")
    used = grouped_df["group_id"].dropna()
    if not used.empty:
        local_ids = sorted(used.astype(int).unique().tolist())
        id_map = {old: cfg.group_id_start + i for i, old in enumerate(local_ids)}
        grouped_df["group_id"] = grouped_df["group_id"].replace(id_map).astype("Int64")

    if not df_rest.empty:
        return pd.concat([grouped_df, df_rest], ignore_index=True)
    return grouped_df


def _group_one_voucher_worker(args):
    """
    多进程 worker: 处理单个 voucher 的分组, 输出使用本地 group_id (从 1 开始).
    必须是模块级函数, 便于 Windows spawn 模式 pickle.
    """
    sub, special_account_pairs, max_recursion, rule2_max_subset = args
    return _group_key_for_voucher(
        sub,
        group_id_start=1,
        special_account_pairs=special_account_pairs,
        max_recursion=max_recursion,
        rule2_max_subset=rule2_max_subset,
    )


# ---------------------------------------------------------------------------
# 4. VND：原币金额置零
# ---------------------------------------------------------------------------
def apply_vnd_zero_original(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    if not cfg.vnd_zero_original:
        return df
    df = df.copy()
    mask = df["currency"].astype(str).str.upper() == "VND"
    df.loc[mask, "original_amount"] = 0.0
    # 按 voucher_no 分组汇总 original_amount，结果为 0 的凭证 currency 更新为 VND
    voucher_sum = df.groupby("voucher_no")["original_amount"].sum()
    zero_vouchers = voucher_sum[voucher_sum == 0].index
    df.loc[df["voucher_no"].isin(zero_vouchers), "currency"] = "VND"
    df =df[df['book_amount']!=0.0]
    return df


# ---------------------------------------------------------------------------
# 5. USD 校验
# ---------------------------------------------------------------------------
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
    """
    USD 校验：
      - 有分组：每个 group_id 内 H 合计 = S 合计
      - 无分组：每个 voucher_no 内 H 合计 = S 合计
    注: P4.2 拆分组 (group_kind='split') 天然 H≠S, 默认不参与校验.
        设置 include_split_groups=True 可包含在内.
    """
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
        if "H" not in grouped.columns:
            grouped["H"] = 0.0
        if "S" not in grouped.columns:
            grouped["S"] = 0.0
        grouped["diff"] = grouped["H"] - grouped["S"]
        bad = grouped[grouped["diff"].abs() > tol].reset_index()
        if bad.empty:
            return UsdCheckResult(True, "USD 分组校验通过 (split 组已跳过)")
        return UsdCheckResult(False, f"USD 分组不平衡, 违规 {len(bad)} 条", bad)
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
            return UsdCheckResult(True, "USD 凭证级校验通过")
        return UsdCheckResult(False, f"USD 凭证不平衡, 违规 {len(bad)} 条", bad)

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
class DataProcessor:
    def __init__(self, config: Optional[ProcessConfig] = None):
        self.config = config or ProcessConfig()


    def load(self, source, **kwargs) -> pd.DataFrame:
        """
        读取数据。
        source 支持：
          - str (文件路径, 按后缀自动选择 csv/excel)
          - 已有 DataFrame
        """
        if isinstance(source, pd.DataFrame):
            df = source.copy()
        else:
            path = str(source)
            if path.lower().endswith((".xlsx", ".xls")):
                df = pd.read_excel(path, **kwargs)
            else:
                df = pd.read_csv(path, **kwargs)
        return df

    def _process(self, df: pd.DataFrame) -> pd.DataFrame:
        """按规则顺序处理。"""
        if df.empty:
            return df
        cfg = self.config
        out = ensure_columns(df)
        out = normalize_direction(out)
        out = remove_non_sense_voucher(out)
        out = general_transform_accounts(out)
        out = special_transform(out)
        out = apply_multi_debit_credit_grouping(out, cfg)
        out = apply_vnd_zero_original(out, cfg)
        check_usd_balance(out, cfg)        
        return out

    def run(self, source, **kwargs) -> pd.DataFrame:
        return self._process(self.load(source, **kwargs))



