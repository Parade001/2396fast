---
name: code-review
version: "1.0.0"
description: "零幻觉代码审核与行级变更审计 Skill：每次代码或配置改动完成后，必须生成带 Reviews+时间戳 的 Markdown 审查报告 (Reviews_YYYYMMDD_HHMMSS.md)，按模块、功能、文件路径、精确行号范围列出全部真实变动、二次核对结果与需人工重点审核清单。"
author: sap2fast-qa-team
category: engineering-audit
tags:
  - code-review
  - line-by-line-audit
  - zero-hallucination
  - diff-verification
  - quality-gate
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(*)
---

# 零幻觉代码审核与行级变更审计 Skill (`code-review`)

## 1. 强制触发条件 (Mandatory Trigger)
**每当完成任何代码、脚本、配置文件或文档的修改后**，必须在项目根目录下自动生成一份以 `Reviews_` + 时间戳命名的 Markdown 审核报告文件：
- **命名规范**：`Reviews_<YYYYMMDD_HHMMSS>.md`（例如：`Reviews_20261104_181500.md`）

## 2. 零幻觉铁律 (100% Zero-Hallucination Rules)
1. **严禁凭记忆编造行号或代码内容**：
   - 生成报告前，必须通过实际文件读取 (`view_file`)、`git status`、`wc -l` 或 `grep -n` 核实每一个被修改文件的**真实物理总行数**、**精确起止行号 (`L<start>-L<end>`)**、**真实函数/类名**与**真实代码片段**。
2. **二次交叉核对 (Secondary Verification)**：
   - 必须实际运行语法编译 (`python3 -m py_compile ...`) 与冒烟回归测试 (`python3 tests/_smoke_test.py`)，并将真实的命令退出码与输出写入报告。

## 3. `Reviews_<时间戳>.md` 报告标准结构

每次生成的 `Reviews_<时间戳>.md` 必须严格包含以下 5 个章节，方便人工逐行二次审核：

### 第一部分：本次提交变更总览表 (Change Summary Matrix)
| 序号 | 所属模块 | 功能分类 | 文件路径 | 变更类型 (新增/修改) | 文件总行数 | 涉及精确行号范围 | 二次核对状态 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |

### 第二部分：按模块与功能的行项目级变更明细 (Module & Line-Item Breakdown)
按「模块 → 功能点 → 文件路径 → 行号区间 (`Lxx - Lyy`)」逐一列出：
- **变更前逻辑 / 状态**
- **变更后逻辑 / 具体代码或配置项**
- **变更目的与业务影响**

### 第三部分：需要人工重点审核的项目清单 (Mandatory Manual Review Checklist)
将涉及**生产环境账号密码、内网 IP/URL、数据库写操作开关、财务准则特殊科目规则**等AI无法替业务方拍板或需现场环境确认的改动逐条列出，标明精确文件路径与行号，供人工逐行复核打勾 (`- [ ]`)。

### 第四部分：自动化编译与冒烟回归二次核对记录 (Automated Verification Evidence)
记录本次改动后实际执行的编译检查、冒烟测试、接口连通性核验结果。
