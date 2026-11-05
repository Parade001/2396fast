---
name: huashu-excel
version: "1.2.0"
description: "数据分析与Excel全流程skill：体检脏表、清洗、对齐需求、分析、对账、交付。让AI算出来的数字经得起追问。跨agent通用，依赖仅openpyxl与pandas。"
author: alchaincyf / sap2fast-team
category: finance-data
tags:
  - excel
  - openpyxl
  - pandas
  - reconciliation
  - data-cleaning
  - sap2fast
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(*)
---

# 华叔 Excel 全流程数据处理与对账 Skill (`huashu-excel`)

> **核心理念**：让 AI 算出来的每一个财务数字、每一张导出表格都 100% 经得起财务总监 (CFO) 与审计师的追问。绝不盲目写表，必须经过「脏表体检 → 口径对齐 → 确定性计算 → 交叉对账校验 → 规范交付」五步闭环。

---

## 1. 五步标准工作流 (Five-Step Workflow)

### Step 1: 脏表体检 (Dirty Sheet Health Check)
在读取任何 Excel (`.xlsx` / `.xls`) 或 CSV 文件前，必须先执行结构体检：
1. **表头与物理起始行检查**：
   - 区分标准第 1 行表头（如 SAP 17 列原始表）与**无表头定长模板**（如越南 FAST 导入模板：第 1~5 行留空，第 6 行 A 列起为 `CTY` 数据行）。
2. **合并单元格与隐藏行列探测**：
   - 使用 `openpyxl` 检查 `ws.merged_cells.ranges`，防止合并单元格导致金额漏读或错位。
3. **隐式类型污染排查**：
   - 科目代码（如 `6051000001`、`51181`）、凭证号（如 `1000000001`、`1000000001_A`）、核算期间（`03`）必须强制按字符串 (`str`) 读取并去除 `.0` 浮点尾缀，严禁被转成科学计数法或浮点数。
4. **原生合计行提取**：
   - 若原表自带「合计 / Total / 小计」行，先将其提取为独立校验基准（Checksum Baseline），随后在明细计算时剔除合计行，防止重复累加。

### Step 2: 对齐业务口径 (Requirement & Rule Alignment)
在编写转换或汇总逻辑前，明确并锁定以下财务口径：
1. **科目映射优先级**：
   - 先查普通科目高优字典 (`account`)，未命中再查功能范围二级字典 (`fkber` + `account`)；
   - 特殊锁定规则：SAP `6051000001` 仅对应外部收入 `51181`（严禁被 YAML 底部重复键覆盖为 `51182`）。
2. **本位币与外币规则**：
   - 当币种为 `VND` 时，原币金额强制置 `0`（K/L 列填 `0`），仅在 M/N 列填写本位币金额，汇率填 `1`；
   - 当币种为 `USD` 等外币时，K/L 列填外币原币，M/N 列填折算后的 VND 本位币，E 列填汇率 `abs(VND / USD)`。
3. **日期与分卷规范**：
   - 过账日期统一格式化为越南标准 `dd/mm/yyyy`；
   - 跨月补记凭证默认压缩至所属账期月末最后一天 (`period_end`)；
   - 单文件满 `20,000` 行时，必须在**不同凭证号边界**处切分新卷 (`*_1.result.xlsx`, `*_2.result.xlsx`)，严禁将同一张凭证拆断在两个文件中。

### Step 3: 确定性脚本计算 (Deterministic Computation)
- **禁止口算或估算**：所有聚合、拆分、汇率计算必须通过 Python (`openpyxl` / `pandas`) 确定性脚本执行。
- **多借多贷复合凭证拆分**：
  - 同一凭证号 (`voucher_no`) 若借方行数 > 1 且贷方行数 > 1，按规则 1（同金额配对）→ 规则 2（1 对 N 子集和配对）→ 规则 3（同科目配对）→ 规则 4（按最小绝对值贪心拆分）赋予递增组号 `group_id`（写入第 43 列 AQ 列）。
  - 拆分前后的借方总额、贷方总额、外币总额必须逐凭证守恒。

### Step 4: 对账交叉核验 (Master Cross-Check)
**未通过以下 4 项硬性对账校验，严禁向用户交付结果文件**：
1. **本位币借贷平衡校验**：
   $$\sum \text{借方本位币 (S / M列)} - \sum \text{贷方本位币 (H / N列)} = 0$$
2. **外币 (USD) 借贷平衡校验**：
   $$\left|\sum \text{USD 借方 (K列)} - \sum \text{USD 贷方 (L列)}\right| \le 0.05$$
3. **转换前后总额守恒校验**：
   - 17 列 SAP 原表总借方/总贷方 == 43 列 FAST 导出模板总借方/总贷方。
4. **物理列数与空行校验**：
   - 导出的 FAST 模板工作表 `ws.max_column == 43`，第 1~5 行全空，第 6 行起 `A列 == 'CTY'`。

### Step 5: 规范交付与审计追踪 (Auditable Delivery)
- 交付结果时，同步输出结构化体检报告（包含：总行数、独立凭证数、多借多贷拆分行数、本位币借方合计、本位币贷方合计、外币差额、平账状态 `PASS/FAIL`）。
- 保留转换日志与审计报表（如 `output/audit_reports/audit_summary_*.xlsx`），确保任何一笔分录均可逆向追溯至 SAP 原始凭证行。
