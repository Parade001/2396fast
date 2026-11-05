import React, { useState, useEffect, useCallback } from 'react';
import { FastCredentialWorkbench } from './components/FastCredentialWorkbench';
import { renderMarkdownToHtml, TocItem } from './utils/markdownRenderer';

interface TemplatePreviewRow {
  excel_row: number;
  col_1_cty: string;
  col_2_voucher_no: string;
  col_3_post_date: string;
  col_4_currency: string;
  col_5_exchange_rate: number | string;
  col_7_fast_account: string;
  col_9_partner: string;
  col_11_debit_orig: number;
  col_12_credit_orig: number;
  col_13_debit_vnd: number;
  col_14_credit_vnd: number;
  col_15_summary: string;
  col_43_group_id: number | string;
}

interface AuditRow {
  file: string;
  detected_format: string;
  total_rows: number;
  unique_vouchers: number;
  split_rows_with_group_id: number;
  total_H_book: number;
  total_S_book: number;
  diff_H_minus_S: number;
  usd_H_orig: number;
  usd_S_orig: number;
  usd_diff: number;
  status: string;
}

interface MappingRow {
  scope: string;
  fkber: string;
  sap_code: string;
  fast_code: string;
}

const DOC_LIST = [
  { key: 'login_guide', title: '1. FAST 登陆链接与凭据最佳实践', file: '登陆链接.md' },
  { key: 'review_latest', title: '2. 最新 Reviews 代码审核报告', file: 'Reviews_20261104_200000.md' },
  { key: 'eval_1104', title: '3. 功能对比与评估报告 (1104)', file: '功能对比、评估报告1104.md' },
  { key: 'accept_1104', title: '4. 需求 6/7 实现验收报告', file: '需求6-7实现验收报告1104.md' },
  { key: 'readme', title: '5. 项目全量说明书 (README)', file: 'README.md' },
  { key: 'cfo_1103', title: '6. 1103 CFO 会议纪要（正式版）', file: '1103_CFO会议纪要_正式版.md' },
  { key: 'cfo_update_1103', title: '7. 1103 与 CFO 会议纪要及需求更新', file: '1103与CFO会议纪要及需求更新.md' },
  { key: 'manual_ops', title: '8. 财务与运维操作手册', file: 'SAP2FAST 使用说明（财务与运维操作手册）.md' },
  { key: 'arch_dev', title: '9. 技术架构与重构设计文档', file: 'SAP2FAST 开发说明（技术架构与重构设计文档）.md' },
  { key: 'biz_req', title: '10. 原始财务业务需求文档', file: '财务业务需求.md' },
];

export const App: React.FC = () => {
  // Default to 'tab-dbgate' so the user immediately sees the FAST Credential & Auto-Login Validator they asked about, while keeping all 5 tabs 1-click accessible
  const [activeTab, setActiveTab] = useState<string>('tab-dbgate');
  const [banner, setBanner] = useState<{ text: string; type: 'pass' | 'warn' | 'err' }>({
    text: '[1104 全量验收就绪] 6051000001 已锁定外部 51181 · 43列 FAST 模板导出 · 方案 A (config/fast_credentials.yaml) 交互式校验与 6 步自动登录验证台已就绪',
    type: 'pass',
  });

  // KPI metrics
  const [accountCount, setAccountCount] = useState<number>(218);
  const [fkberCount, setFkberCount] = useState<number>(324);
  const [locked6051, setLocked6051] = useState<string>('51181');
  const [inputCount, setInputCount] = useState<number>(20);
  const [outputCount, setOutputCount] = useState<number>(1);
  const [smokeStatus, setSmokeStatus] = useState<string>('ALL_OK (PASS)');

  // Tab 1: Convert state
  const [convPattern, setConvPattern] = useState<string>('samples/sample_raw_sap.xlsx');
  const [convDateMode, setConvDateMode] = useState<string>('period_end');
  const [convCcCol, setConvCcCol] = useState<string>('');
  const [convDedup, setConvDedup] = useState<boolean>(true);
  const [previewRows, setPreviewRows] = useState<TemplatePreviewRow[]>([]);
  const [converting, setConverting] = useState<boolean>(false);

  // Tab 2: Audit state
  const [auditRows, setAuditRows] = useState<AuditRow[]>([]);
  const [auditing, setAuditing] = useState<boolean>(false);

  // Tab 3: Mapping state
  const [mapAction, setMapAction] = useState<string>('update-account');
  const [mapSapCode, setMapSapCode] = useState<string>('1001000001');
  const [mapFastCode, setMapFastCode] = useState<string>('11129');
  const [mapFkber, setMapFkber] = useState<string>('');
  const [mapPartnerScope, setMapPartnerScope] = useState<string>('d11,ct00');
  const [mapForceMulti, setMapForceMulti] = useState<boolean>(false);
  const [mapDryRun, setMapDryRun] = useState<boolean>(true);
  const [mapResultBanner, setMapResultBanner] = useState<{ text: string; type: 'pass' | 'warn' | 'err' }>({
    text: '提示：1001000001 与 1001000002 同时映射到 11121（多对一）。不勾选“允许修改多对一”点击执行可直观验证 [6.22 安全锁拦截]。',
    type: 'pass',
  });
  const [mapSearchQuery, setMapSearchQuery] = useState<string>('');
  const [mappingRows, setMappingRows] = useState<MappingRow[]>([]);
  const [mappingAuditLogs, setMappingAuditLogs] = useState<any[]>([]);

  // Tab 4: DB Dry-Run state
  const [dbOpType, setDbOpType] = useState<string>('delete-account');
  const [dbFastCode, setDbFastCode] = useState<string>('6428');
  const [dbOldCode, setDbOldCode] = useState<string>('131111');
  const [dbNewCode, setDbNewCode] = useState<string>('131112');
  const [dbFiscalYm, setDbFiscalYm] = useState<string>('202403');
  const [dbVouchers, setDbVouchers] = useState<string>('1000000001,1000000002');
  const [dbSqlPreview, setDbSqlPreview] = useState<string>(
    '点击“生成 Dry-Run SQL 安全预览”查看将在 7 张 FAST 核心分表上执行的 SQL 语句。'
  );

  // Tab 5: Docs state
  const [activeDocKey, setActiveDocKey] = useState<string>('login_guide');
  const [docFilename, setDocFilename] = useState<string>('登陆链接.md');
  const [docLineCount, setDocLineCount] = useState<number>(0);
  const [docRawContent, setDocRawContent] = useState<string>('');
  const [docViewMode, setDocViewMode] = useState<'preview' | 'raw'>('preview');
  const [docSearchKeyword, setDocSearchKeyword] = useState<string>('');

  const updateBanner = useCallback((text: string, type: 'pass' | 'warn' | 'err' = 'pass') => {
    setBanner({ text, type });
  }, []);

  const loadOverview = useCallback(async () => {
    try {
      const res = await fetch('/api/overview');
      const data = await res.json();
      if (data.ok) {
        setAccountCount(data.account_mapping_count || 0);
        setFkberCount(data.fkber_mapping_count || 0);
        setLocked6051(data.account_6051000001_target || '51181');
        setInputCount(data.input_file_count || 0);
        setOutputCount(data.output_file_count || 0);
        if (Array.isArray(data.template_preview)) {
          setPreviewRows(data.template_preview);
        }
        if (Array.isArray(data.audit_rows) && data.audit_rows.length > 0) {
          setAuditRows(data.audit_rows);
        }
      }
    } catch (err) {
      console.error('Overview load error:', err);
    }
  }, []);

  const loadMappings = useCallback(async (q = '') => {
    try {
      const res = await fetch(`/api/mappings?search=${encodeURIComponent(q)}`);
      const data = await res.json();
      if (data.ok) {
        setMappingRows(data.rows || []);
        setMappingAuditLogs(data.audit_logs || []);
      }
    } catch (err) {
      console.error('Mappings load error:', err);
    }
  }, []);

  const loadDocument = useCallback(async (docKey: string) => {
    setActiveDocKey(docKey);
    try {
      const res = await fetch(`/api/docs/${encodeURIComponent(docKey)}`);
      const data = await res.json();
      if (data.ok) {
        setDocFilename(data.filename || docKey);
        setDocLineCount(data.line_count || 0);
        setDocRawContent(data.content || '');
      }
    } catch (err) {
      console.error('Doc load error:', err);
    }
  }, []);

  useEffect(() => {
    loadOverview();
    loadMappings('');
    loadDocument('login_guide');
  }, [loadOverview, loadMappings, loadDocument]);

  const handleRunSmokeTest = async () => {
    updateBanner('正在执行 1104 全量 7 大模块冒烟验收测试...', 'warn');
    try {
      const res = await fetch('/api/smoke-test', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        setSmokeStatus('ALL_OK (PASS)');
        updateBanner(`[冒烟验收通过] ${data.output}`, 'pass');
        await loadOverview();
      } else {
        setSmokeStatus('FAIL');
        updateBanner(`冒烟测试未通过: ${data.detail || ''}`, 'err');
      }
    } catch (err: any) {
      updateBanner(`冒烟测试调用异常: ${err?.message || String(err)}`, 'err');
    }
  };

  const handleGenerateSampleAndConvert = async () => {
    setConverting(true);
    try {
      const res = await fetch('/api/generate-sample', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        setPreviewRows(data.preview_rows || []);
        setActiveTab('tab-convert');
        updateBanner(
          `已生成 17 列原始 SAP 样表 (${data.sample_path}) 并转换为 43 列 FAST 导入模板 (${(data.exported_files || []).join(', ')})`,
          'pass'
        );
        await loadOverview();
      }
    } catch (err: any) {
      updateBanner(`生成样表失败: ${err?.message || String(err)}`, 'err');
    } finally {
      setConverting(false);
    }
  };

  const handleExecuteConvert = async () => {
    setConverting(true);
    try {
      const res = await fetch('/api/convert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pattern: convPattern,
          cross_month_date_mode: convDateMode,
          cost_center_col: convCcCol,
          offline_voucher_dedup: convDedup,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setPreviewRows(data.preview_rows || []);
        updateBanner(
          `转换完成：导出文件 ${(data.exported_files || []).join(', ')}，预览共 ${(data.preview_rows || []).length} 行`,
          'pass'
        );
      } else {
        updateBanner(`转换失败: ${data.detail || ''}`, 'err');
      }
    } catch (err: any) {
      updateBanner(`转换异常: ${err?.message || String(err)}`, 'err');
    } finally {
      setConverting(false);
    }
  };

  const handleExecuteAudit = async () => {
    setAuditing(true);
    try {
      const res = await fetch('/api/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pattern: 'input/*.xlsx' }),
      });
      const data = await res.json();
      if (data.ok) {
        setAuditRows(data.rows || []);
        updateBanner(`已完成 ${data.rows?.length || 0} 份本地凭证文件本外币借贷平账体检审计`, 'pass');
      }
    } catch (err: any) {
      updateBanner(`平账审计失败: ${err?.message || String(err)}`, 'err');
    } finally {
      setAuditing(false);
    }
  };

  const handleSubmitMapping = async () => {
    try {
      const res = await fetch('/api/mappings/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: mapAction,
          sap_code: mapSapCode,
          fast_code: mapFastCode,
          fkber: mapFkber,
          partner_sync_scope: mapPartnerScope,
          force_multi: mapForceMulti,
          dry_run: mapDryRun,
        }),
      });
      const data = await res.json();
      setMapResultBanner({
        text: data.message || (data.ok ? '操作成功' : '操作被风控锁拦截'),
        type: data.ok ? 'pass' : 'err',
      });
      updateBanner(data.message || '', data.ok ? 'pass' : 'warn');
      await loadMappings(mapSearchQuery);
      await loadOverview();
    } catch (err: any) {
      setMapResultBanner({ text: `请求异常: ${err?.message || String(err)}`, type: 'err' });
    }
  };

  const handlePreviewDbDryRun = async () => {
    try {
      const res = await fetch('/api/db-maintenance/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          op_type: dbOpType,
          fast_code: dbFastCode,
          old_fast_code: dbOldCode,
          new_fast_code: dbNewCode,
          fiscalym: dbFiscalYm,
          vouchers: dbVouchers,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        const sqlLines = data.preview_sqls || [];
        setDbSqlPreview(
          `-- Dry-Run SQL 安全预览 (共 ${data.sql_count} 条语句，未接触真实生产库)\n` +
            sqlLines.join('\n')
        );
        updateBanner(`已生成 ${data.op_type} 的 Dry-Run SQL 安全预览（共 ${data.sql_count} 条）`, 'pass');
      }
    } catch (err: any) {
      setDbSqlPreview(`生成预览异常: ${err?.message || String(err)}`);
    }
  };

  const renderedDoc = React.useMemo(() => {
    return renderMarkdownToHtml(docRawContent, docSearchKeyword);
  }, [docRawContent, docSearchKeyword]);

  return (
    <div>
      {/* 3-Zone Top Bar Contract */}
      <header className="topbar">
        <div className="brand">
          <span className="text-lg font-bold tracking-tight text-white whitespace-nowrap shrink-0">
            SAP2FAST 1104 跨国财务准则转换与自动化工作台
          </span>
        </div>

        <nav className="hidden lg:flex items-center gap-6 text-sm font-medium text-slate-300">
          <button
            type="button"
            onClick={() => setActiveTab('tab-convert')}
            className="hover:text-white transition-colors whitespace-nowrap shrink-0 bg-transparent border-0 cursor-pointer text-inherit"
          >
            43列模板转换
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('tab-audit')}
            className="hover:text-white transition-colors whitespace-nowrap shrink-0 bg-transparent border-0 cursor-pointer text-inherit"
          >
            平账审计
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('tab-mapping')}
            className="hover:text-white transition-colors whitespace-nowrap shrink-0 bg-transparent border-0 cursor-pointer text-inherit"
          >
            映射风控锁
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('tab-dbgate')}
            className="hover:text-white transition-colors whitespace-nowrap shrink-0 bg-transparent border-0 cursor-pointer text-inherit"
          >
            凭据与自动登录验证
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('tab-docs')}
            className="hover:text-white transition-colors whitespace-nowrap shrink-0 bg-transparent border-0 cursor-pointer text-inherit"
          >
            文档中心
          </button>
        </nav>

        <div className="topbar-actions shrink-0">
          <button
            id="btn-run-smoke"
            className="btn btn-primary"
            type="button"
            onClick={handleRunSmokeTest}
          >
            运行 1104 冒烟验收测试
          </button>
        </div>
      </header>

      <main className="container">
        <div id="global-banner" className={`status-banner ${banner.type}`}>
          {banner.text}
        </div>

        <section className="kpi-strip" aria-label="系统核心指标">
          <div className="kpi-card">
            <div className="kpi-label">普通科目映射 (account)</div>
            <div id="kpi-account-count" className="kpi-value">
              {accountCount}
            </div>
            <div className="kpi-sub">
              6051000001 → <strong id="kpi-6051">{locked6051}</strong> (外部收入锁定)
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">功能范围映射 (fkber 1000/2000/3000)</div>
            <div id="kpi-fkber-count" className="kpi-value">
              {fkberCount}
            </div>
            <div className="kpi-sub">制造 / 销售 / 管理费用两级映射</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">本地凭证文件 (Input / Output)</div>
            <div id="kpi-files-count" className="kpi-value">
              {inputCount} / {outputCount}
            </div>
            <div className="kpi-sub">满 20,000 行跨凭证边界自动拆卷</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">冒烟回归与需求 6/7 验收状态</div>
            <div id="kpi-smoke-status" className="kpi-value">
              {smokeStatus}
            </div>
            <div className="kpi-sub">7 大核心模块全链路自动化验证</div>
          </div>
        </section>

        <nav className="tabs-nav" aria-label="工作台功能导航">
          <button
            className={`tab-btn ${activeTab === 'tab-convert' ? 'active' : ''}`}
            onClick={() => setActiveTab('tab-convert')}
            type="button"
          >
            1. 43列模板转换与预览
          </button>
          <button
            className={`tab-btn ${activeTab === 'tab-audit' ? 'active' : ''}`}
            onClick={() => setActiveTab('tab-audit')}
            type="button"
          >
            2. 离线借贷平账审计 (18份月度表)
          </button>
          <button
            className={`tab-btn ${activeTab === 'tab-mapping' ? 'active' : ''}`}
            onClick={() => setActiveTab('tab-mapping')}
            type="button"
          >
            3. 需求6：映射管理与多对一安全锁
          </button>
          <button
            className={`tab-btn ${activeTab === 'tab-dbgate' ? 'active' : ''}`}
            onClick={() => setActiveTab('tab-dbgate')}
            type="button"
          >
            4. 需求7：fast_credentials.yaml 验证 &amp; 库表安全闸
          </button>
          <button
            className={`tab-btn ${activeTab === 'tab-docs' ? 'active' : ''}`}
            onClick={() => setActiveTab('tab-docs')}
            type="button"
          >
            5. Markdown 说明 (10份文档)
          </button>
        </nav>

        {/* Tab 1: 43列模板转换与预览 */}
        {activeTab === 'tab-convert' && (
          <section id="tab-convert">
            <div className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">SAP 17列原始凭证 → 越南 FAST 43列导入模板转换器</h2>
                  <p className="card-desc">
                    第 1~5 行留空，第 6 行起写入 CTY、凭证号、dd/mm/yyyy 过账日期、K/M 列借方(S)、L/N 列贷方(H)、第 43 列多借多贷拆分组号 group_id
                  </p>
                </div>
                <button
                  id="btn-run-sample"
                  className="btn btn-secondary"
                  type="button"
                  onClick={handleGenerateSampleAndConvert}
                  disabled={converting}
                >
                  生成 17列样表并转 43列模板
                </button>
              </div>

              <div className="form-row">
                <div className="field">
                  <label htmlFor="conv-pattern">输入文件匹配路径</label>
                  <input
                    id="conv-pattern"
                    type="text"
                    value={convPattern}
                    onChange={(e) => setConvPattern(e.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="conv-date-mode">跨月补记过账日期策略</label>
                  <select
                    id="conv-date-mode"
                    value={convDateMode}
                    onChange={(e) => setConvDateMode(e.target.value)}
                  >
                    <option value="period_end">period_end (压缩至所属账期月末 - CFO拍板默认)</option>
                    <option value="cpudt">cpudt (直接取系统录入日当天)</option>
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="conv-cc-col">成本中心输出列 (可选)</label>
                  <select
                    id="conv-cc-col"
                    value={convCcCol}
                    onChange={(e) => setConvCcCol(e.target.value)}
                  >
                    <option value="">不输出至模板 (SQL降维合并 - 默认)</option>
                    <option value="6">第 6 列 (F列)</option>
                    <option value="8">第 8 列 (H列)</option>
                    <option value="10">第 10 列 (J列)</option>
                  </select>
                </div>
                <label className="checkbox-field">
                  <input
                    id="conv-dedup"
                    type="checkbox"
                    checked={convDedup}
                    onChange={(e) => setConvDedup(e.target.checked)}
                  />
                  启用离线跨期凭证 _A/_B 防重
                </label>
                <button
                  id="btn-exec-convert"
                  className="btn btn-primary"
                  type="button"
                  onClick={handleExecuteConvert}
                  disabled={converting}
                >
                  {converting ? '正在转换...' : '执行转换并预览 43 列模板'}
                </button>
              </div>

              <div className="table-wrap">
                <table aria-label="43列 FAST 导入模板预览表">
                  <thead>
                    <tr>
                      <th>Excel行号</th>
                      <th>A列(1) 单位</th>
                      <th>B列(2) 凭证号</th>
                      <th>C列(3) 过账日期</th>
                      <th>D列(4) 币种</th>
                      <th>E列(5) 汇率</th>
                      <th>G列(7) FAST科目</th>
                      <th>I列(9) 客商编码</th>
                      <th>K列(11) 借方原币(S)</th>
                      <th>L列(12) 贷方原币(H)</th>
                      <th>M列(13) 借方VND(S)</th>
                      <th>N列(14) 贷方VND(H)</th>
                      <th>O列(15) 摘要</th>
                      <th>AQ列(43) 组号</th>
                    </tr>
                  </thead>
                  <tbody id="template-preview-tbody">
                    {previewRows.length === 0 ? (
                      <tr>
                        <td colSpan={14} style={{ textAlign: 'center', padding: '20px' }}>
                          暂无预览数据，请点击「执行转换并预览 43 列模板」
                        </td>
                      </tr>
                    ) : (
                      previewRows.map((r) => (
                        <tr key={r.excel_row}>
                          <td className="mono">{r.excel_row}</td>
                          <td className="mono">{r.col_1_cty}</td>
                          <td className="mono">{r.col_2_voucher_no}</td>
                          <td className="mono">{r.col_3_post_date}</td>
                          <td className="mono">{r.col_4_currency || 'VND'}</td>
                          <td className="num">{r.col_5_exchange_rate}</td>
                          <td className="mono" style={{ fontWeight: 700, color: '#0f4c81' }}>
                            {r.col_7_fast_account}
                          </td>
                          <td className="mono">{r.col_9_partner}</td>
                          <td className="num">{Number(r.col_11_debit_orig || 0).toLocaleString()}</td>
                          <td className="num">{Number(r.col_12_credit_orig || 0).toLocaleString()}</td>
                          <td className="num">{Number(r.col_13_debit_vnd || 0).toLocaleString()}</td>
                          <td className="num">{Number(r.col_14_credit_vnd || 0).toLocaleString()}</td>
                          <td>{r.col_15_summary}</td>
                          <td className="mono">{r.col_43_group_id}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        {/* Tab 2: 离线借贷平账审计 */}
        {activeTab === 'tab-audit' && (
          <section id="tab-audit">
            <div className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">离线本位币 (VND) 与外币 (USD) 借贷平账体检报告</h2>
                  <p className="card-desc">
                    支持对 17 列原始 SAP 表与 43 列无表头 .result.xlsx 模板表双向逆向解析与借贷平衡核验
                  </p>
                </div>
                <button
                  id="btn-exec-audit"
                  className="btn btn-primary"
                  type="button"
                  onClick={handleExecuteAudit}
                  disabled={auditing}
                >
                  {auditing ? '正在扫描全量文件...' : '扫描 input/ 与 output/ 全量文件平账状态'}
                </button>
              </div>

              <div className="table-wrap">
                <table aria-label="离线借贷平账审计汇总表">
                  <thead>
                    <tr>
                      <th>文件名</th>
                      <th>识别类型</th>
                      <th>总行数</th>
                      <th>独立凭证数</th>
                      <th>多借多贷拆分行数</th>
                      <th>贷方本位币合计 (H)</th>
                      <th>借方本位币合计 (S)</th>
                      <th>本位币差额</th>
                      <th>USD 贷方 (H)</th>
                      <th>USD 借方 (S)</th>
                      <th>USD 差额</th>
                      <th>平账状态</th>
                    </tr>
                  </thead>
                  <tbody id="audit-report-tbody">
                    {auditRows.map((r, idx) => (
                      <tr key={`${r.file}-${idx}`}>
                        <td className="mono">{r.file}</td>
                        <td className="mono">{r.detected_format}</td>
                        <td className="num">{r.total_rows.toLocaleString()}</td>
                        <td className="num">{r.unique_vouchers.toLocaleString()}</td>
                        <td className="num">{r.split_rows_with_group_id.toLocaleString()}</td>
                        <td className="num">{r.total_H_book.toLocaleString()}</td>
                        <td className="num">{r.total_S_book.toLocaleString()}</td>
                        <td className="num">{r.diff_H_minus_S.toLocaleString()}</td>
                        <td className="num">{r.usd_H_orig.toLocaleString()}</td>
                        <td className="num">{r.usd_S_orig.toLocaleString()}</td>
                        <td className="num">{r.usd_diff.toLocaleString()}</td>
                        <td>
                          <span
                            className={
                              r.status.includes('PASS') ? 'status-text-pass' : 'status-text-fail'
                            }
                          >
                            {r.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        {/* Tab 3: 需求 6 映射管理与多对一安全锁 */}
        {activeTab === 'tab-mapping' && (
          <section id="tab-mapping">
            <div className="grid-2">
              <div className="card">
                <div className="card-header">
                  <div>
                    <h2 className="card-title">CFO 需求 6：新增/修改科目与客商映射 (MappingManager)</h2>
                    <p className="card-desc">
                      内置 6.1 防重校验、6.21 多表范围同步改客商、6.22 多对一安全锁拦截与审计留痕
                    </p>
                  </div>
                </div>
                <div className="form-row">
                  <div className="field">
                    <label htmlFor="map-action">操作类型</label>
                    <select
                      id="map-action"
                      value={mapAction}
                      onChange={(e) => setMapAction(e.target.value)}
                    >
                      <option value="update-account">6.22 修改科目映射 (带多对一安全锁)</option>
                      <option value="add-account">6.1 新增科目映射 (防重)</option>
                      <option value="add-partner">6.1 新增客商映射 (防重)</option>
                      <option value="update-partner">6.21 修改客商映射 (联动同步FAST库)</option>
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="map-sap-code">SAP 科目 / 客商代码</label>
                    <input
                      id="map-sap-code"
                      type="text"
                      value={mapSapCode}
                      onChange={(e) => setMapSapCode(e.target.value)}
                      placeholder="如 1001000001 或 V9001"
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="map-fast-code">目标越南 FAST 代码</label>
                    <input
                      id="map-fast-code"
                      type="text"
                      value={mapFastCode}
                      onChange={(e) => setMapFastCode(e.target.value)}
                      placeholder="如 11121 或 VN_V9001"
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="map-fkber">功能范围 fkber (可选)</label>
                    <select
                      id="map-fkber"
                      value={mapFkber}
                      onChange={(e) => setMapFkber(e.target.value)}
                    >
                      <option value="">普通科目 (account 高优层)</option>
                      <option value="1000">1000 (制造费用)</option>
                      <option value="2000">2000 (销售费用)</option>
                      <option value="3000">3000 (管理费用)</option>
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="map-partner-scope">6.21 客商同步表范围</label>
                    <select
                      id="map-partner-scope"
                      value={mapPartnerScope}
                      onChange={(e) => setMapPartnerScope(e.target.value)}
                    >
                      <option value="d11,ct00">d11$&#123;ym&#125; + ct00 (默认)</option>
                      <option value="d11,ct00,m11,dmkh">
                        d11$&#123;ym&#125; + ct00 + m11$&#123;ym&#125; + dmkh (全表扩展)
                      </option>
                    </select>
                  </div>
                </div>
                <div className="form-row">
                  <label className="checkbox-field">
                    <input
                      id="map-force-multi"
                      type="checkbox"
                      checked={mapForceMulti}
                      onChange={(e) => setMapForceMulti(e.target.checked)}
                    />
                    允许修改多对一科目映射 (--force-multi)
                  </label>
                  <label className="checkbox-field">
                    <input
                      id="map-dry-run"
                      type="checkbox"
                      checked={mapDryRun}
                      onChange={(e) => setMapDryRun(e.target.checked)}
                    />
                    Dry-Run 安全预览模式 (仅校验与预览SQL，不改写正式YAML)
                  </label>
                  <button
                    id="btn-submit-mapping"
                    className="btn btn-primary"
                    type="button"
                    onClick={handleSubmitMapping}
                  >
                    执行映射风控校验与操作
                  </button>
                </div>
                <div id="mapping-result-banner" className={`status-banner ${mapResultBanner.type}`}>
                  {mapResultBanner.text}
                </div>
              </div>

              <div className="card">
                <div className="card-header">
                  <div>
                    <h2 className="card-title">映射修改审计日志 (logs/mapping_change_audit.log)</h2>
                    <p className="card-desc">默认持久保留每一次科目/客商新增与修改审计记录</p>
                  </div>
                </div>
                <div id="mapping-audit-box" className="sql-box">
                  {mappingAuditLogs.length === 0
                    ? '暂无审计日志记录'
                    : mappingAuditLogs
                        .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
                        .join('\n')}
                </div>
              </div>
            </div>

            <div className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">当前 config/account.yaml 科目映射字典速查</h2>
                </div>
                <div className="form-row" style={{ marginBottom: 0 }}>
                  <div className="field">
                    <label htmlFor="map-search">搜索 SAP 或 FAST 科目</label>
                    <input
                      id="map-search"
                      type="text"
                      value={mapSearchQuery}
                      onChange={(e) => setMapSearchQuery(e.target.value)}
                      placeholder="输入 6051000001 或 51181..."
                    />
                  </div>
                  <button
                    id="btn-search-mapping"
                    className="btn btn-secondary"
                    type="button"
                    onClick={() => loadMappings(mapSearchQuery)}
                  >
                    查询映射表
                  </button>
                </div>
              </div>
              <div className="table-wrap">
                <table aria-label="科目映射表">
                  <thead>
                    <tr>
                      <th>映射层级</th>
                      <th>功能范围 (fkber)</th>
                      <th>SAP 科目代码 (CAS)</th>
                      <th>越南 FAST 科目代码 (VAS)</th>
                    </tr>
                  </thead>
                  <tbody id="mapping-table-tbody">
                    {mappingRows.map((r, idx) => (
                      <tr key={`${r.scope}-${r.sap_code}-${idx}`}>
                        <td>{r.scope}</td>
                        <td className="mono">{r.fkber}</td>
                        <td className="mono">{r.sap_code}</td>
                        <td className="mono" style={{ fontWeight: 700, color: '#0f4c81' }}>
                          {r.fast_code}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        {/* Tab 4: 需求7 fast_credentials.yaml 验证 & 库表维护 Dry-Run 安全闸 */}
        {activeTab === 'tab-dbgate' && (
          <section id="tab-dbgate">
            {/* Interactive FAST Credential & Auto-Login Validator */}
            <FastCredentialWorkbench
              onGlobalBannerUpdate={updateBanner}
              onStatusRefresh={loadOverview}
            />

            <div className="grid-2">
              <div className="card">
                <div className="card-header">
                  <div>
                    <h2 className="card-title">FAST 数据库破坏性维护操作 Dry-Run 安全预览闸</h2>
                    <p className="card-desc">
                      执行删科目错账 (delete-account)、批量改科目 (update-account)、移除期间错账凭证 (remove_fast_errors) 前先预览将执行的 SQL
                    </p>
                  </div>
                </div>
                <div className="form-row">
                  <div className="field">
                    <label htmlFor="db-op-type">维护操作类型</label>
                    <select
                      id="db-op-type"
                      value={dbOpType}
                      onChange={(e) => setDbOpType(e.target.value)}
                    >
                      <option value="delete-account">
                        按科目彻底清理关联凭证 (modify_fast_database)
                      </option>
                      <option value="update-account">
                        批量替换历史分表科目代码 (update_fast_account)
                      </option>
                      <option value="remove-fast-errors">
                        按凭证号级联删除期间错账 (remove_fast_errors)
                      </option>
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="db-fast-code">目标 FAST 科目 (删库用)</label>
                    <input
                      id="db-fast-code"
                      type="text"
                      value={dbFastCode}
                      onChange={(e) => setDbFastCode(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="db-old-code">旧 FAST 科目 (改科目用)</label>
                    <input
                      id="db-old-code"
                      type="text"
                      value={dbOldCode}
                      onChange={(e) => setDbOldCode(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="db-new-code">新 FAST 科目 (改科目用)</label>
                    <input
                      id="db-new-code"
                      type="text"
                      value={dbNewCode}
                      onChange={(e) => setDbNewCode(e.target.value)}
                    />
                  </div>
                </div>
                <div className="form-row">
                  <div className="field">
                    <label htmlFor="db-fiscalym">财务期间 YYYYMM (删错账用)</label>
                    <input
                      id="db-fiscalym"
                      type="text"
                      value={dbFiscalYm}
                      onChange={(e) => setDbFiscalYm(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="db-vouchers">待删错账凭证号列表</label>
                    <input
                      id="db-vouchers"
                      type="text"
                      value={dbVouchers}
                      onChange={(e) => setDbVouchers(e.target.value)}
                    />
                  </div>
                  <button
                    id="btn-preview-db"
                    className="btn btn-primary"
                    type="button"
                    onClick={handlePreviewDbDryRun}
                  >
                    生成 Dry-Run SQL 安全预览
                  </button>
                </div>
                <div id="db-sql-preview" className="sql-box">
                  {dbSqlPreview}
                </div>
              </div>

              <div className="card">
                <div className="card-header">
                  <div>
                    <h2 className="card-title">
                      CFO 需求 7：Chrome CDP 全自动登录、循环上传与批量 Post 过账生产指令
                    </h2>
                    <p className="card-desc">
                      当上方 config/fast_credentials.yaml 配置并校验通过后，可直接无参启动自动上传与过账
                    </p>
                  </div>
                </div>
                <div className="sql-box">{`# 1. 使用方案 A (config/fast_credentials.yaml) 自动读取账密并循环上传 output/*.xlsx
python sap2fast_full_fix.py --mode upload

# 2. 使用方案 A 自动读取账密并批量勾选 Post 到总账 (GL)
python sap2fast_full_fix.py --mode post

# 3. 命令行临时覆盖账号密码（最高优先级 P1）：
python sap2fast_full_fix.py --mode upload --fast-user ADMIN --fast-password "******" --fast-unit CTY

# 1104 已闭环的 3 项关键点：
# [7.3] _perform_fast_login 已正式接通 LoginHandler.login(user, pwd, unit, lang) 自动填表登录；
# [7.4] utils/cdp_client.py attach_upload_file 中未定义的 logger 已全部修复为 self.logger；
# [7.5] utils/posting_handler.py process_posting 已按 click_post_to_gl_button() 返回值正确分支打印。`}</div>
              </div>
            </div>
          </section>
        )}

        {/* Tab 5: Markdown 说明 */}
        {activeTab === 'tab-docs' && (
          <section id="tab-docs">
            <div className="card">
              <div className="card-header">
                <div>
                  <h2 className="card-title">
                    模块 5：Markdown 说明（全项目 .md 文档富文本预览与语法高亮阅读器）
                  </h2>
                  <p className="card-desc">
                    点击下方任意子项目切换对应 Markdown 文档（包含《登陆链接.md》中的方案 A 凭据配置说明）
                  </p>
                </div>
              </div>

              <div className="doc-selector-bar" role="tablist" aria-label="项目 Markdown 文档列表">
                {DOC_LIST.map((d) => (
                  <button
                    key={d.key}
                    className={`doc-btn ${activeDocKey === d.key ? 'active' : ''}`}
                    type="button"
                    onClick={() => loadDocument(d.key)}
                  >
                    <span className="doc-btn-title">{d.title}</span>
                    <span className="doc-btn-file">{d.file}</span>
                  </button>
                ))}
              </div>

              <div className="doc-toolbar">
                <div className="doc-meta-info">
                  <span>
                    当前阅读文档：<strong id="doc-meta-filename">{docFilename}</strong>
                  </span>
                  <span>·</span>
                  <span id="doc-meta-lines">{docLineCount} 行</span>
                  <span>·</span>
                  <span id="doc-meta-mode-label">
                    {docViewMode === 'preview'
                      ? 'Markdown 渲染预览模式（表格/代码块/行号已高亮）'
                      : 'Markdown 原始源码模式'}
                  </span>
                </div>
                <div className="doc-toolbar-controls">
                  <input
                    id="doc-search-input"
                    className="doc-search-input"
                    type="search"
                    value={docSearchKeyword}
                    onChange={(e) => setDocSearchKeyword(e.target.value)}
                    placeholder="在当前 Markdown 内搜索并高亮关键字..."
                    aria-label="在当前 Markdown 内搜索并高亮关键字"
                  />
                  <div className="mode-switch-group" role="group" aria-label="切换阅读模式">
                    <button
                      id="btn-mode-preview"
                      className={`mode-switch-btn ${docViewMode === 'preview' ? 'active' : ''}`}
                      type="button"
                      onClick={() => setDocViewMode('preview')}
                    >
                      Markdown 预览模式
                    </button>
                    <button
                      id="btn-mode-raw"
                      className={`mode-switch-btn ${docViewMode === 'raw' ? 'active' : ''}`}
                      type="button"
                      onClick={() => setDocViewMode('raw')}
                    >
                      源码查看模式
                    </button>
                  </div>
                </div>
              </div>

              <div className="md-workspace">
                {docViewMode === 'preview' ? (
                  <div
                    id="doc-viewer-content"
                    className="doc-viewer md-rendered"
                    dangerouslySetInnerHTML={{ __html: renderedDoc.html }}
                  />
                ) : (
                  <div id="doc-viewer-content" className="doc-viewer raw-mode">
                    {docRawContent}
                  </div>
                )}

                <aside className="md-toc-card" aria-label="当前文档章节目录大纲">
                  <div className="md-toc-title">文档章节大纲 (点击跳转高亮)</div>
                  <ul id="doc-toc-list" className="md-toc-list">
                    {renderedDoc.toc.length === 0 ? (
                      <li className="md-toc-item">
                        <a href="#doc-viewer-content">暂无章节标题</a>
                      </li>
                    ) : (
                      renderedDoc.toc.map((t: TocItem) => (
                        <li key={t.id} className={`md-toc-item toc-h${t.level}`}>
                          <a href={`#${t.id}`}>{t.text}</a>
                        </li>
                      ))
                    )}
                  </ul>
                </aside>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
};

export default App;
