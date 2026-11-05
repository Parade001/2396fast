---
name: finance-expert
version: "1.1.0"
description: "跨国财务准则转换与总账内控专家 Skill (CAS 中国准则 <-> VAS 越南准则 Circular 200)：涵盖科目映射两级治理、多对一风控安全锁、外币折算与汇兑损益、跨期凭证防重、FAST 总账过账与科目余额表 (TB) 自动对账。"
author: sap2fast-finance-team
category: finance
tags:
  - finance
  - accounting
  - cas-to-vas
  - sap-s4hana
  - vietnam-fast
  - trial-balance
  - internal-control
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(*)
---

# 跨国财务与总账内控专家 Skill (`finance-expert`)

## 1. 核心专业领域 (Core Domains)

1. **跨国会计准则转换 (CAS → VAS Circular 200)**：
   - **SAP S/4HANA (`ACDOCA`)** 采用中国企业会计准则 (CAS)，通过「10 位科目代码 (`RACCT`) + 4 位功能范围 (`RFAREA/fkber`: `1000` 制造费用 / `2000` 销售费用 / `3000` 管理费用)」双维度核算损益与资产负债。
   - **越南 FAST 财务系统 (`2396fast`)** 采用越南财政部第 200 号通知准则 (VAS Circular 200)，不设功能范围字段，直接通过 `622/627*`（生产成本）、`641*`（销售费用）、`642*`（管理费用）等不同科目冠号区分费用性质。
2. **两级科目映射与多对一内控锁 (CFO 需求 6)**：
   - **映射查找顺序**：优先匹配 `account`（普通科目高优层），未命中再匹配 `fkber[功能范围][科目]`，严禁颠倒。
   - **关键科目锁定**：SAP `6051000001`（其他业务收入）在普通科目层固定锁定为越南外部收入科目 `51181`（内部关联交易 `51182` 由手工甄别调账）。
   - **6.1 防重添加**：新增科目或客商映射前，自动检查原代码与目标 FAST 代码是否已存在，默认禁止无授权的多对一映射。
   - **6.21 客商变更联动同步**：修改客商映射时，支持联动生成并同步更新越南 FAST 数据库历史分表（`d11$YYYYMM`、`ct00`、`m11$YYYYMM`、`dmkh`）。
   - **6.22 多对一修改安全锁**：当多个 SAP 科目共用同一个越南 FAST 科目（如 `1001000001` 与 `1001000002` 同映 `11121`）时，禁止单独修改并同步刷写 FAST 历史库，防止冲毁共用科目的其他历史凭证，必须显式传入 `--force-multi` 方可放行。
3. **跨系统唯一键对齐与跨期凭证防重 (`so_ct`)**：
   - SAP 凭证唯一键为三维组合 `(公司代码 BUKRS, 会计年度 GJAHR, 凭证号 BELNR)`；
   - 越南 FAST 凭证号 `so_ct` 为全局一维唯一键；
   - 在线/离线转换时必须检测跨年度、跨期间同名凭证号，自动追加 `_A`、`_B`、`_C` 后缀。
4. **总账过账与科目余额表 (Trial Balance / TB 表) 对账核验**：
   - 导入越南 FAST 前后，必须核对：
     1. 期初余额 + 本期借方发生额 (`S`) - 本期贷方发生额 (`H`) == 期末余额；
     2. SAP 侧按映射规则汇总后的各 FAST 科目借贷净额 == 越南 FAST 系统 `c11$000000` / TB 表对应科目发生额；
     3. 任何破坏性数据库维护操作（按科目删错账 `modify_fast_database`、批量换科目 `update_fast_account`、按凭证号删期间错账 `remove_fast_errors`）必须先执行 `--dry-run` 预览全部 SQL 并留存审计日志 (`logs/db_maintenance_audit.log`)。
