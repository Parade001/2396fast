# SAP2FAST 开发说明（架构设计、核心算法与维护手册）

> 本文档面向研发工程师，详述 `sap2fast_full_fix.py` 的内部模块设计、数据结构约定、核心算法实现细节、双格式逆向解析机制以及扩展开发规范[cite: 19]。

---

## 1. 架构设计与模块解耦原则

### 1.1 为什么采用单文件高内聚 + 数据库延迟加载架构？
原多模块工程 (`2396fast`) 将代码分散在 `config/`、`db/`、`utils/`、`modules/` 四个包中，由于 `utils/__init__.py` 在顶层无条件导入了浏览器自动化依赖（`requests`、`websocket`、`pyautogui`、`pyperclip`），且 `utils/export.py` 在导出时强耦合了 `db/mssql_db.py` 的在线凭证号去重查询 (`update_voucher_no`)，导致一旦缺少爬虫库或脱离企业内网，整个工程完全无法启动或陷入长时阻塞[cite: 12, 15, 19]。

重构后的 `sap2fast_full_fix.py` 遵循以下三大工程原则：
1. **核心转换与导出 100% 自包含**：`AccountConfig`、`ProcessConfig`、`StatusConfig`、`DataProcessor`（8 步管线）、`_parse_fast_cty_template`（模板逆向解析器）、`export_to_sap`（43 列 FAST 模板生成器）全部内聚于单文件中，仅依赖 `pandas`、`numpy`、`openpyxl`、`pyyaml` 标准数据处理栈[cite: 13, 18, 19]。
2. **数据库层函数级延迟导入（Lazy Import）**：所有涉及 `from db import get_hana_pool, get_mssql_pool, HanaSQL, MSSQLSQL` 的语句均被封装在 `DataReconciler` 和 `SAPExporter` 的成员方法内部。在运行 `--mode local`、`--generate-sample`、`--audit` 时，**Python 解释器完全不会加载 `db/` 包**，从根源上杜绝离线环境下的网络连接异常[cite: 13, 18]。
3. **显式控制数据库副作用 (`use_db_dedup` 开关)**：在 `export_to_sap(df, filename, use_db_dedup=False)` 中，离线模式默认 `use_db_dedup=False`，彻底跳过 `update_voucher_no` 的 MSSQL 查询；仅在 `SAPExporter` 和 `DataReconciler` 在线模式下显式传入 `use_db_dedup=True`。

---

## 2. 双格式输入自适应解析机制 (`DataProcessor.load`)

为了同时支持「17 列原始 SAP 导出表」与「43 列无表头 FAST `.result.xlsx` 模板表」，`DataProcessor.load()` 实现了两阶段嗅探机制：

```text
输入文件 (.xlsx / .xls / .csv)
   │
   ├─► 1. 默认按带表头读取 pd.read_excel(path)
   │      ├─ 若包含 ['voucher_no', 'account_code', 'direction'] ──► 判定为【17列原始SAP表】
   │      │    └─ attrs['book_amount_scaled'] = False
   │      │    └─ attrs['from_result_template'] = False
   │      │
   │      └─ 若不包含上述列名 ──► 2. 按无表头重读 pd.read_excel(path, header=None)
   │             └─ 调用 _parse_fast_cty_template() 扫描第 0 列为 "CTY" 的起始行 (第6行)
   │                  ├─ 向量化提取第 1,2,3,4,6,8,10,11,12,13,14,42 列还原为 17 列结构
   │                  ├─ 将 C 列 dd/mm/yyyy 反推为 8 位 cpudt 与财务期间 (year, period)
   │                  ├─ 标记 attrs['book_amount_scaled'] = True (防止二次 ×100)
   │                  └─ 标记 attrs['from_result_template'] = True (防止 6351/5152 二次翻转与重复拆组)
```

> **关键实现细节（`df.attrs` 透传保护）**：
> 在 `pandas` 中，部分 DataFrame 操作（如 `df.merge()`、`pd.concat()`、`df.drop()`）会重置新生成的 DataFrame 的 `attrs` 字典。因此，在 `ensure_columns`、`normalize_direction`、`remove_non_sense_voucher`、`general_transform_accounts`、`special_transform`、`apply_multi_debit_credit_grouping`、`apply_vnd_zero_original` 每个步骤的入口与出口，代码均通过 `attrs_backup = dict(df.attrs)` 和 `df.attrs.update(attrs_backup)` 强制透传元数据标记。

---

## 3. 多借多贷拆分算法与外币汇兑保护深度解析

### 3.1 触发条件与三维唯一分组键
- **唯一凭证键**：`_composite_vkey = company_code + "_" + year + "_" + period + "_" + voucher_no`，防止不同期间复用同一凭证号导致跨期分录被混在一起拆分[cite: 3, 13, 14, 19]。
- **触发条件**：同一 `_composite_vkey` 下，`count(direction == 'H') > 1` 且 `count(direction == 'S') > 1`[cite: 11, 13, 14]。

### 3.2 四级拆解流水线 (`_group_key_for_voucher`)
对每一张触发多借多贷的凭证，在 `max_recursion`（默认 2 轮）循环内依次执行：
1. **第一级：同科目等额对冲 (`_apply_rule1` 子步骤 A)**
   - 匹配条件：`fast_account[h] == fast_account[s]` 且 `abs(book_amount[h] - book_amount[s]) <= 0.01`。标记 `group_kind = "pair"`。
2. **第二级：异科目 1对1 / 1对N / N对1 精确组合 (`_apply_rule1` 子步骤 B/C/D)**
   - 先找 1借1贷等额项（`sum_1h1s`）；
   - 再对剩余项在 `k_num in range(2, rule2_max_subset + 1)`（默认上限 4）内通过 `itertools.combinations` 枚举子集，若 `sum(combo) == target`，则配成 `sum_1h{k}s` 或 `sum_{k}h1s`[cite: 11, 13]。
3. **第三级：特别科目对差额拆分 (`_apply_rule2`)**
   - 针对预设的 7 组特别科目对 `special_account_pairs`（如 `('6603030001', '1122000001')` 等，支持双向匹配），当 `S` 行金额 `a` 与 `H` 行金额 `b` 不相等时，从较大行拆出与较小行相等的金额组成 `split` 组，并将剩余差额 `abs(a - b)` 作为未分组新行放回池中，进入下一轮递归继续触发规则 1[cite: 11, 13]。
4. **第四级：双指针贪心兜底拆分与 USD 汇兑保护 (`_apply_fallback_split`)**
   - **USD 外币汇兑损益免拆保护**：在 `202403_..._1.result.xlsx` 等真实业务数据中，存在币种为 `USD` 且含 `6351/5152` 汇兑损益行的复合凭证（汇兑损益行 `original_amount == 0.00 USD`，但 `book_amount > 0 VND`）。若对此类凭证按 VND 比例强行切割原币，会导致正常业务行的美元原币被切分给汇兑行，造成子组内 USD 借贷不平。因此代码显式检测 `is_usd_with_fx`，命中时保持 `group_id = pd.NA` 原样输出，与老项目行為 100% 一致。
   - **普通复合凭证双指针消减**：对非外币汇兑的残留多借多贷行，使用双指针 `hi, si` 每次取 `match_amt = min(h_amt, s_amt)` 生成一对 1借1贷子组（`fallback_1h1s`），直至全部消减完毕[cite: 13, 14, 19]。

---

## 4. 43 列 FAST 导出引擎性能优化与分卷规范 (`export_to_sap`)

1. **NumPy 数组预提取加速**：
   - 在写入 20,000 行 Excel 单元格前，先将 DataFrame 各列一次性转换为底层 `numpy.ndarray`（`vno_arr`、`cpudt_arr`、`book_arr` 等），避免在 20,000 次循环中反复调用 `Series.get()` 或 `iloc`，使 20,000 行 Excel 的构建与保存总耗时稳定在 **4.5 ~ 5.0 秒**。
2. **跨文件凭证完整性保护**：
   - 严格遵循老项目 `utils/export.py` 第 24 行的分卷条件：
     ```python
     if row >= split_num * file_no and vno != belnr:
     ```
   - 当行号达到 `20000 * file_no` 时，如果当前行的凭证号 `vno` 与上一行 `belnr` 相同（说明同一张凭证还没有写完），程序会继续把该凭证剩余的分录写完，直到切换到下一张新凭证时才新建下一个 `.result.xlsx` 文件，并在新文件中通过偏移量 `radd = 6 - row` 将起始行精准重置回 **第 6 行**[cite: 15]。

---

## 5. 开发者扩展指南

### 5.1 如何注入自定义特殊转换钩子 (`special_transforms`)？
`ProcessConfig` 提供了 `special_transforms: List[Callable[[pd.DataFrame], pd.DataFrame]]` 扩展点[cite: 11, 13]。若未来新增特定业务规则，无需修改 `DataProcessor` 内部源码，只需定义转换函数并注入 `ProcessConfig`：

```python
from sap2fast_full_fix import DataProcessor, ProcessConfig, export_to_sap

def my_custom_rule(df):
    df = df.copy()
    # 示例：将特定成本中心下的某科目重分类
    mask = (df["cost_center"] == "CC01") & (df["fast_account"] == "6421")
    df.loc[mask, "fast_account"] = "6411"
    return df

cfg = ProcessConfig(special_transforms=[my_custom_rule])
processor = DataProcessor(cfg)
df_result = processor.run("samples/sample_raw_sap.xlsx")
export_to_sap(df_result, "output/custom_output.result.xlsx")
```