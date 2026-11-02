# SAP2FAST 使用说明（财务与研发操作手册）

> 本手册对应终极重构版单文件主程序 **`sap2fast_full_fix.py`**[cite: 18]。
> 本版本重点强化了**非数据库直连模式（Offline Mode）**下的完整操作闭环：无需连接 SAP HANA 或 `10.7.3.230` SQL Server 数据库，即可在本地完成**样表生成、凭证转换、多借多贷拆分、43 列 FAST 专用模板导出与财务平账审计**[cite: 13, 18]。

---

## 1. 环境准备

### 1.1 Python 与依赖安装
支持 Python 3.9 ~ 3.13（全面兼容 pandas 2.x 与 pandas 3.x）[cite: 18]：

```powershell
# 1. 非数据库直连模式（离线转换、样表生成、离线审计）仅需安装以下 5 个核心库：
pip install pandas numpy openpyxl pyyaml python-dotenv

# 2. 若在内网环境下需要运行数据库直连模式（export / reconcile），额外安装：
pip install hdbcli pymssql sqlalchemy
```

---

## 2. 非数据库直连模式（Offline Mode）操作全指南

在没有内网 VPN 或无法连接 `10.7.3.230` 数据库的环境下，你可以通过以下 **4 条核心命令** 执行对应的业务操作：

### 操作一：一键生成 17 列原始 SAP 样表并完成全链路转换（`--generate-sample`）
- **适用业务场景**：
  - 你刚拿到项目，或者刚在 `config/account.yaml` 中修改了会计科目/功能范围映射规则，想要立刻验证「从 17 列原始 SAP 凭证数据 → 8 步清洗转换 → 多借多贷拆分 → 图三标准的 43 列 FAST 导入模板」全流程是否正常。
- **执行命令**：
  ```powershell
  python sap2fast_full_fix.py --generate-sample
  ```
- **执行效果与产出文件**：
  1. 自动在 `samples/sample_raw_sap.xlsx` 生成一份标准的 **17 列原始 SAP 凭证表**（包含普通 1借1贷凭证、`331111` 空客商自动补 `A0001`、2借2贷多借多贷凭证按特别科目组拆分为 `group_id=1, 2`、以及 `USD` 外币应收应付科目重分类 `131111->131112`）；
  2. 自动调用 `DataProcessor` 与 `export_to_sap` 将其转换为 **图三格式** 的 FAST 导入模板，输出至：`output/fixed_sample_raw_sap.xlsx`。

---

### 操作二：转换单份或指定路径的本地 Excel/CSV 文件（`--mode local --input ...`）
- **适用业务场景**：
  - 财务人员从 SAP 系统手工导出了某个期间的原始凭证表（`.xlsx` / `.xls` / `.csv`），需要将其转换为越南 FAST 软件可直接导入的 `.result.xlsx` 模板文件。
- **执行命令**：
  ```powershell
  # 转换单个文件
  python sap2fast_full_fix.py --mode local --input "samples/sample_raw_sap.xlsx" --output output

  # 同时转换多个指定文件（用英文逗号或分号分隔）
  python sap2fast_full_fix.py --mode local --input "input/sap_202508.xlsx,input/sap_202509.csv" --output output
  ```
- **执行效果与产出文件**：
  - 在 `output/` 目录下生成 `fixed_{原文件名}.xlsx`。若单文件转换后超过 `20,000` 行，程序会自动按凭证边界（绝不把同一张凭证切成两半）分卷保存为 `*_1.result.xlsx`、`*_2.result.xlsx`[cite: 15, 16]。

---

### 操作三：批量重跑 `input/` 目录下的全部文件（默认命令）
- **适用业务场景**：
  - 将多份待处理的 Excel 文件统一放入 `input/` 文件夹，一键批量转换到 `output/` 文件夹。
  - **支持双格式智能识别**：如果放入的是 **17 列原始 SAP 表**，执行完整映射、金额 `×100` 与拆分；如果放入的是像 `input/202403_..._1.result.xlsx` 那样**已经转换过的无表头 `CTY` 模板表（图二）**，程序会自动从第 6 行起逆向解析，并在保持金额不二次放大、汇兑科目不二次翻转的前提下重新校验并输出为标准 FAST 模板。
- **执行命令**：
  ```powershell
  python sap2fast_full_fix.py
  # 等价于：
  python sap2fast_full_fix.py --mode local --input "input/*.xlsx" --output output
  ```

---

### 操作四：对本地文件执行离线财务平账与拆分审计（`--audit`）
- **适用业务场景**：
  - 在将 `.result.xlsx` 导入越南 FAST 软件之前，或者收到别人发来的凭证文件时，快速核查每个文件内：**总行数、独立凭证数、多借多贷拆分组行数、本位币（VND）借贷方合计是否相等、外币（USD）借贷方合计是否相等**。
- **执行命令**：
  ```powershell
  # 审计 input/ 目录下的所有文件
  python sap2fast_full_fix.py --audit --input "input/*.xlsx"

  # 审计转换后的 output/ 目录文件
  python sap2fast_full_fix.py --audit --input "output/*.xlsx"
  ```
- **执行效果与产出文件**：
  - 终端逐行打印每个文件的借贷平衡状态（`PASS` / `FAIL`），并在 `output/offline_audit_report.csv` 生成可用 Excel 直接打开的财务审计汇总表。

---

## 3. 数据库直连模式（Online Mode）操作指南（需内网环境）

当你的电脑已连接公司内网 VPN，且 `.env` 中已正确配置 SAP HANA 与 FAST MSSQL (`10.7.3.230`) 账号密码时[cite: 3, 18]：

| 运行模式 | 命令示例 | 业务说明 |
| :--- | :--- | :--- |
| **按月对账与差异补导** | `python sap2fast_full_fix.py --mode reconcile --fiscalym 202508` | 自动对比 `202508` 财务期间（对应日历月 `202511`）SAP 与 FAST 的科目发生额，生成根目录 `compare.csv`，并将 FAST 缺失的 SAP 凭证拉取转换导出至 `output/202508_*_added_data.xlsx_1.result.xlsx`[cite: 5, 13, 18]。 |
| **按日增量同步导出** | `python sap2fast_full_fix.py --mode export --cpudt 20250801` | 按自然日拉取 SAP 凭证并导出至 `output/*_sync_data.xlsx_1.result.xlsx`（不传 `--cpudt` 则自动取 FAST 最新凭证日期 +1 天）[cite: 7, 13, 18]。 |
| **全流程同步+核对** | `python sap2fast_full_fix.py --mode all --fiscalym 202508 --cpudt 20250801` | 先执行 `export` 增量同步，再执行 `reconcile` 月度对账补导[cite: 13, 18]。 |

---

## 4. 数据处理的完整业务逻辑说明（财务核对必读）

程序在将 SAP 凭证转化为越南 FAST 模板时，严格按顺序执行以下 **8 步业务规则**[cite: 11, 14, 19]：

1. **列规范化与脏数据清理 (`ensure_columns`)**：
   - 将公司代码、凭证号、科目代码、功能范围 (`fkber`)、供应商 (`lifnr`)、客户 (`kunnr`)、币种等转为纯文本字符串，剔除 Excel 常见的 `.0` 小数尾缀与 `nan/None` 字符[cite: 11, 13]。
2. **借贷方向统一 (`normalize_direction`)**：
   - 统一转为大写：在原项目约定中，**`H` 代表借方（写入 FAST 第 12/14 列），`S` 代表贷方（写入 FAST 第 11/13 列）**[cite: 11, 15, 16]。
3. **无效孤立凭证过滤 (`remove_non_sense_voucher`)**：
   - 自动剔除同一 `voucher_no` 下只有 1 条分录的残缺凭证[cite: 11, 13]。
4. **两级会计科目映射与越南盾金额还原 (`general_transform_accounts`)**：
   - **第一级（功能范围+科目）**：读取 `config/account.yaml` 的 `fkber` 节点，按 `(fkber, account_code)` 匹配越南科目[cite: 11, 14, 18]；
   - **第二级（普通科目覆盖）**：读取 `account.yaml` 的 `account` 节点，按 `account_code` 匹配越南科目（**优先级更高，覆盖第一级结果**）[cite: 11, 14, 18]；
   - **金额放大 100 倍**：SAP 数据库存储越南盾（`VND`）时缩小了 100 倍（保留了 2 位小数位），程序自动执行 `book_amount = book_amount * 100` 还原真实越南盾金额，并计算外币汇率 `ex = book_amount / original_amount`[cite: 11, 14]。
5. **特殊业务二级修正 (`special_transform`)**：
   - **汇兑损益重分类**：若映射后科目为 `6351`（汇兑损失）但方向为 `H`（借），自动改为 `5152`（汇兑收益）；若科目为 `5152` 但方向为 `S`（贷），自动改为 `6351`[cite: 11, 13]；
   - **客商编码处理**：凭证类型为 `WL` 或 `WE`（物料凭证）时清空供应商与客户编码；对特定科目且无客商的分录自动赋默认客商：`331111 -> A0001`、`1411 -> A0002`、`331112 -> A0003`、`338811 -> A0004`、`1388118 -> A0005`、`13681 -> A0007`[cite: 11, 13]；
   - **外币应收应付重分类**：币种为 `USD` 时，科目 `131111` 自动改为 `131112`，科目 `331111` 自动改为 `331112`[cite: 11, 13]；
   - **物料凭证币种归并**：凭证类型为 `WL`、`WE` 或凭证号以 `072` 开头的分录，币种强制设为 `VND`[cite: 11, 13]。
6. **多借多贷智能拆分 (`apply_multi_debit_credit_grouping`)**：
   - 越南 FAST 系统要求每组凭证必须为「1借1贷 / 1借多贷 / 多借1贷」，严禁「多借多贷」[cite: 1, 11, 14]。
   - 当同一凭证内 `H 行数 > 1` 且 `S 行数 > 1` 时触发拆分：依次执行**同科目等额配对 (`pair`) → 异科目 1对1 等额配对 (`sum_1h1s`) → 1对N / N对1 组合配对 (`sum_1hNs` / `sum_Nh1s`) → 7 组特别科目对差额递归拆分 (`split`) → 双指针贪心兜底拆分 (`fallback_1h1s`)**，并将生成的全局递增组号写入第 43 列 `group_id`[cite: 11, 13, 15, 16]。
7. **VND 原币置零与零值清理 (`apply_vnd_zero_original`)**：
   - 将币种为 `VND` 的分录的 `original_amount` 置为 `0.0`；若整张凭证的原币金额合计为 `0`，则将该凭证币种统一改为 `VND`；最后剔除 `book_amount == 0` 的零值行[cite: 11, 13, 14]。
8. **越南财年偏移与 43 列 FAST 模板导出 (`resolve_period_date` & `export_to_sap`)**：
   - **越南财年偏移（+3 个月）**：越南财年从每年 4 月起算（即 SAP 财务期间 `01` = 自然月 `4月`，期间 `03` = 自然月 `6月`，期间 `10` = 次年 `1月`）。过账日期按录入日期 `cpudt` 与目标自然月的早晚关系自动取**月初 01 日 / 当天 / 月末最后一天**，格式化为 `dd/mm/yyyy`[cite: 13, 14, 18]；
   - **43 列无表头模板写入**：第 1~5 行留空，从第 6 行起按固定列号写入（A列 `CTY`、B列凭证号、C列过账日期、D列币种、E列汇率、G列科目、I列客商、K/L列原币贷/借、M/N列本位币贷/借、O列摘要、AQ列第43列分组号），满 20,000 行且跨凭证时自动切分新文件[cite: 15, 16, 25]。