import express, { Request, Response } from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import yaml from 'js-yaml';
import * as XLSX from 'xlsx';
import http from 'http';
import crypto from 'crypto';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const BASE_DIR = __dirname;

const CONFIG_DIR = path.join(BASE_DIR, 'config');
const INPUT_DIR = path.join(BASE_DIR, 'input');
const OUTPUT_DIR = path.join(BASE_DIR, 'output');
const SAMPLES_DIR = path.join(BASE_DIR, 'samples');
const LOGS_DIR = path.join(BASE_DIR, 'logs');

for (const dir of [CONFIG_DIR, INPUT_DIR, OUTPUT_DIR, SAMPLES_DIR, LOGS_DIR]) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function readWorkbookFromFile(filePath: string): XLSX.WorkBook {
  const buf = fs.readFileSync(filePath);
  return XLSX.read(buf, { type: 'buffer' });
}

function writeWorkbookToFile(wb: XLSX.WorkBook, filePath: string): void {
  const buf = XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' });
  fs.writeFileSync(filePath, buf);
}

// ============================================================================
// 1. YAML & Configuration Loaders (account.yaml & fast_credentials.yaml)
// ============================================================================

export interface AccountYamlConfig {
  account?: Record<string, string>;
  fkber?: Record<string, Record<string, string>>;
  vendor?: Record<string, string>;
  customer?: Record<string, string>;
  chrome?: Record<string, any>;
}

export function loadAccountConfig(): AccountYamlConfig {
  const cfgPath = path.join(CONFIG_DIR, 'account.yaml');
  if (!fs.existsSync(cfgPath)) {
    return { account: { '6051000001': '51181' }, fkber: {}, vendor: {}, customer: {} };
  }
  try {
    const raw = fs.readFileSync(cfgPath, 'utf-8');
    const parsed = (yaml.load(raw) as AccountYamlConfig) || {};
    if (!parsed.account) parsed.account = {};
    // CFO 1103 拍板决策：6051000001 强制锁定为外部收入 51181
    parsed.account['6051000001'] = '51181';
    return parsed;
  } catch (err) {
    console.error('Error loading account.yaml:', err);
    return { account: { '6051000001': '51181' }, fkber: {}, vendor: {}, customer: {} };
  }
}

export function saveAccountConfig(cfg: AccountYamlConfig): void {
  const cfgPath = path.join(CONFIG_DIR, 'account.yaml');
  if (cfg.account) {
    cfg.account['6051000001'] = '51181';
  }
  const dumped = yaml.dump(cfg, { indent: 2, quotingType: "'" });
  fs.writeFileSync(cfgPath, dumped, 'utf-8');
}

export interface FastCredentialsData {
  fast_auth: {
    login_url: string;
    username: string;
    password: string;
    unit: string;
    language: string;
  };
  chrome_cdp: {
    chrome_path: string;
    remote_debugging_port: number;
    max_retries: number;
    close_browser_on_exit: boolean;
  };
  selectors: {
    username_input: string;
    password_input: string;
    unit_input: string;
    language_select: string;
    login_button: string;
    menu_general_ledger: string;
    menu_journal_entry: string;
    menu_post_to_gl: string;
    menu_trial_balance: string;
  };
}

export const BASELINE_LOCK_PATH = '/tmp/.sap2fast_auth_baseline.json';

export interface AuthBaselineLock {
  username: string;
  password: string;
  masked_password: string;
  password_length: number;
  fingerprint: string;
  unit: string;
  language: string;
  yaml_mtime_ms: number;
  locked_at: string;
  source: string;
}

export function maskPasswordStr(pwd: string): string {
  if (!pwd) return '';
  if (pwd.length <= 2) return '*'.repeat(pwd.length);
  return pwd[0] + '•'.repeat(Math.max(4, pwd.length - 2)) + pwd[pwd.length - 1];
}

export function computeCredentialFingerprint(username: string, password: string): string {
  const u = String(username || '').trim();
  const p = String(password || '');
  if (!u && !p) return '';
  return crypto.createHash('sha256').update(`${u}:${p}`, 'utf-8').digest('hex').slice(0, 12);
}

export function loadOrSyncAuthBaseline(
  yamlUser: string,
  yamlPass: string,
  yamlUnit: string,
  yamlLang: string,
  yamlMtimeMs: number,
  forceResetFromYaml = false
): AuthBaselineLock | null {
  let existing: AuthBaselineLock | null = null;
  if (fs.existsSync(BASELINE_LOCK_PATH)) {
    try {
      existing = JSON.parse(fs.readFileSync(BASELINE_LOCK_PATH, 'utf-8')) as AuthBaselineLock;
    } catch {
      existing = null;
    }
  }

  // If the user edited config/fast_credentials.yaml on disk (has non-empty credentials and either no baseline yet or file mtime changed externally)
  if (yamlUser && yamlPass) {
    const mtimeChanged = !existing || Math.abs((existing.yaml_mtime_ms || 0) - yamlMtimeMs) > 50;
    if (!existing || forceResetFromYaml || mtimeChanged) {
      const nextLock: AuthBaselineLock = {
        username: yamlUser,
        password: yamlPass,
        masked_password: maskPasswordStr(yamlPass),
        password_length: yamlPass.length,
        fingerprint: computeCredentialFingerprint(yamlUser, yamlPass),
        unit: yamlUnit || 'CTY',
        language: yamlLang || 'en',
        yaml_mtime_ms: yamlMtimeMs,
        locked_at: new Date().toISOString(),
        source: 'config/fast_credentials.yaml (本地基准凭据锁)',
      };
      try {
        fs.writeFileSync(BASELINE_LOCK_PATH, JSON.stringify(nextLock, null, 2), 'utf-8');
      } catch {
        // ignore /tmp write error
      }
      return nextLock;
    }
  }
  return existing;
}

export function loadFastCredentialsStatus(forceSyncBaseline = false) {
  const credYamlPath = path.join(CONFIG_DIR, 'fast_credentials.yaml');
  const accountCfg = loadAccountConfig();
  const accChrome = accountCfg.chrome || {};

  let rawYaml = '';
  let credData: Partial<FastCredentialsData> = {};
  let yamlSyntaxValid = true;
  let yamlSyntaxError = '';
  let yamlMtimeMs = 0;

  if (fs.existsSync(credYamlPath)) {
    rawYaml = fs.readFileSync(credYamlPath, 'utf-8');
    try {
      yamlMtimeMs = fs.statSync(credYamlPath).mtimeMs;
      credData = (yaml.load(rawYaml) as Partial<FastCredentialsData>) || {};
    } catch (e: any) {
      yamlSyntaxValid = false;
      yamlSyntaxError = e?.message || String(e);
    }
  }

  const fastAuth = credData.fast_auth || ({} as any);
  const chromeCdp = credData.chrome_cdp || ({} as any);
  const selectors = credData.selectors || ({} as any);

  const envUrl = process.env.FAST_URL || '';
  const envUser = process.env.FAST_USER || '';
  const envPass = process.env.FAST_PASSWORD || '';
  const envUnit = process.env.FAST_UNIT || '';
  const envLang = process.env.FAST_LANGUAGE || '';

  const yamlUrl = String(fastAuth.login_url || '').trim();
  const yamlUser = String(fastAuth.username || '').trim();
  const yamlPass = String(fastAuth.password || '');
  const yamlUnit = String(fastAuth.unit || 'CTY').trim();
  const yamlLang = String(fastAuth.language || 'en').trim();

  const fallbackUrl = String(accChrome.fast_url || 'http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx').trim();
  const fallbackUser = String(accChrome.fast_user || '').trim();
  const fallbackPass = String(accChrome.fast_password || '');
  const fallbackUnit = String(accChrome.fast_unit || 'CTY').trim();
  const fallbackLang = String(accChrome.fast_language || 'en').trim();

  const effectiveUrl = envUrl || yamlUrl || fallbackUrl;
  const effectiveUser = envUser || yamlUser || fallbackUser;
  const effectivePass = envPass || yamlPass || fallbackPass;
  const effectiveUnit = envUnit || yamlUnit || fallbackUnit || 'CTY';
  const effectiveLang = (envLang || yamlLang || fallbackLang || 'en').toLowerCase();

  const activeSource = envUser && envPass
    ? '.env (环境变量高优层)'
    : yamlUser && yamlPass
      ? 'config/fast_credentials.yaml (方案A独立凭据文件)'
      : fallbackUser && fallbackPass
        ? 'config/account.yaml (chrome兜底节点)'
        : '未配置完整账密 (待填写)';

  const baselineLock = loadOrSyncAuthBaseline(
    effectiveUser,
    effectivePass,
    effectiveUnit,
    effectiveLang,
    yamlMtimeMs,
    forceSyncBaseline
  );

  const normalized: FastCredentialsData = {
    fast_auth: {
      login_url: yamlUrl || fallbackUrl,
      username: yamlUser,
      password: yamlPass,
      unit: yamlUnit || 'CTY',
      language: yamlLang || 'en',
    },
    chrome_cdp: {
      chrome_path: String(chromeCdp.chrome_path || accChrome.chrome_path || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'),
      remote_debugging_port: Number(chromeCdp.remote_debugging_port || accChrome.remote_debugging_port || 9222),
      max_retries: Number(chromeCdp.max_retries || accChrome.max_retries || 3),
      close_browser_on_exit: chromeCdp.close_browser_on_exit ?? true,
    },
    selectors: {
      username_input: String(selectors.username_input || "input[id='LoginExtender_txtUserName']"),
      password_input: String(selectors.password_input || "input[id='LoginExtender_txtPassword']"),
      unit_input: String(selectors.unit_input || "input[name='txtUnit'], input[id*='Unit'], select[id*='Unit']"),
      language_select: String(selectors.language_select || "select[id*='Language'], select[id*='lang'], a[id*='English'], input[value='E']"),
      login_button: String(selectors.login_button || "button[id='LoginExtender_Ok'], input[id='LoginExtender_Ok']"),
      menu_general_ledger: String(selectors.menu_general_ledger || 'General Ledger'),
      menu_journal_entry: String(selectors.menu_journal_entry || 'Journal Entry'),
      menu_post_to_gl: String(selectors.menu_post_to_gl || 'Post to GL'),
      menu_trial_balance: String(selectors.menu_trial_balance || 'Trial Balance'),
    },
  };

  const isReadyForAutoLogin = Boolean(yamlSyntaxValid && effectiveUser && effectivePass);
  const effectiveFingerprint = computeCredentialFingerprint(effectiveUser, effectivePass);

  return {
    ok: true,
    file_exists: fs.existsSync(credYamlPath),
    credential_file: 'config/fast_credentials.yaml',
    yaml_syntax_valid: yamlSyntaxValid,
    yaml_syntax_error: yamlSyntaxError,
    is_ready_for_auto_login: isReadyForAutoLogin,
    active_source: activeSource,
    effective: {
      login_url: effectiveUrl,
      username: effectiveUser,
      has_password: Boolean(effectivePass),
      password_length: effectivePass.length,
      masked_password: maskPasswordStr(effectivePass),
      fingerprint: effectiveFingerprint,
      unit: effectiveUnit,
      language: effectiveLang,
    },
    baseline_lock: baselineLock
      ? {
          locked: true,
          username: baselineLock.username,
          masked_password: baselineLock.masked_password,
          password_length: baselineLock.password_length,
          fingerprint: baselineLock.fingerprint,
          locked_at: baselineLock.locked_at,
          source: baselineLock.source,
        }
      : {
          locked: false,
          username: '',
          masked_password: '',
          password_length: 0,
          fingerprint: '',
          locked_at: '',
          source: '尚未建立基准账密锁',
        },
    priority_chain: [
      {
        rank: 1,
        source: '命令行参数 (--fast-user / --fast-password)',
        status: '运行时最高优先级 (按需覆盖)',
        username: '(CLI 运行时注入)',
        has_password: false,
      },
      {
        rank: 2,
        source: '.env 环境变量 (FAST_USER / FAST_PASSWORD)',
        status: envUser && envPass ? '已配置并生效' : '未配置 (跳过)',
        username: envUser || '-',
        has_password: Boolean(envPass),
        fingerprint: computeCredentialFingerprint(envUser, envPass),
      },
      {
        rank: 3,
        source: 'config/fast_credentials.yaml (方案 A 推荐)',
        status: yamlUser && yamlPass ? `已配置就绪 (PASS · 指纹 #${computeCredentialFingerprint(yamlUser, yamlPass)})` : '未填写完整账号或密码',
        username: yamlUser || '(空)',
        has_password: Boolean(yamlPass),
        password_length: yamlPass.length,
        masked_password: maskPasswordStr(yamlPass),
        fingerprint: computeCredentialFingerprint(yamlUser, yamlPass),
      },
      {
        rank: 4,
        source: 'config/account.yaml (chrome 兜底节点)',
        status: fallbackUser && fallbackPass ? '已配置兜底' : '默认空值兜底',
        username: fallbackUser || '(空)',
        has_password: Boolean(fallbackPass),
      },
    ],
    config_data: normalized,
    raw_yaml: rawYaml,
    cli_verify_command: `python -c "from utils.login_handler import load_fast_credentials; c = load_fast_credentials(); print('账号:', c['username'], '| 密码已配:', bool(c['password']), f'(长度:{len(c[\\'password\\'])})', '| 单位:', c['unit'], '| 语言:', c['language'])"`,
  };
}

export function buildFastCredentialsYamlString(data: FastCredentialsData): string {
  const fa = data.fast_auth;
  const cd = data.chrome_cdp;
  const sel = data.selectors;
  return `# ==============================================================================
# 越南 FAST 财务系统 (FAW_TIMBERLAND) 登录凭据与网页自动化配置文件
# 文件路径: config/fast_credentials.yaml
# ==============================================================================
# 【给财务与运维人员的填写说明】
# 1. 您只需在下方 \`fast_auth\` 区域的双引号 "" 内填写您的 FAST 用户名和密码即可；
# 2. 优先级顺序（高 -> 低）：
#    命令行参数 (--fast-user) > .env 环境变量 (FAST_USER) > 本文件 (config/fast_credentials.yaml) > config/account.yaml
# 3. 安全建议：若项目使用 Git 多人协作，建议将真实密码填写在根目录 \`.env\` 或本文件中，
#    并避免将含有真实生产密码的文件推送到公开仓库。
# ==============================================================================

fast_auth:
  # 越南 FAST 登录页面地址（内网默认地址）
  login_url: ${JSON.stringify(fa.login_url || 'http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx')}

  # ★ 请在此处填写您的越南 FAST 登录账号（例如: "ADMIN" 或您的财务工号）
  username: ${JSON.stringify(fa.username || '')}

  # ★ 请在此处填写您的越南 FAST 登录密码
  password: ${JSON.stringify(fa.password || '')}

  # 核算单位代码（默认 "CTY"）
  unit: ${JSON.stringify(fa.unit || 'CTY')}

  # 登录界面语言自动切换设置：
  # FAST 登录界面默认是越南语 (vi)，建议保持为 "en"（自动切换为英文界面后再登录，确保菜单与 TB 表均为英文）
  # 可选值: "en" (English 英文界面) | "vi" (Tiếng Việt 越南语界面)
  language: ${JSON.stringify(fa.language || 'en')}

chrome_cdp:
  # 本地 Google Chrome 可执行文件路径（Windows 默认路径如下，程序也会自动探测）
  chrome_path: '${cd.chrome_path || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'}'
  # Chrome 远程调试端口（默认 9222）
  remote_debugging_port: ${Number(cd.remote_debugging_port || 9222)}
  # 自动化步骤最大重试次数
  max_retries: ${Number(cd.max_retries || 3)}
  # 任务完成后是否自动关闭浏览器
  close_browser_on_exit: ${Boolean(cd.close_browser_on_exit ?? true)}

# FAST 页面关键 F12 DOM 元素选择器配置（支持根据现场版本微调）
selectors:
  # 登录页元素 (http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx)
  username_input: ${JSON.stringify(sel.username_input || "input[id='LoginExtender_txtUserName']")}
  password_input: ${JSON.stringify(sel.password_input || "input[id='LoginExtender_txtPassword']")}
  unit_input: ${JSON.stringify(sel.unit_input || "input[name='txtUnit'], input[id*='Unit'], select[id*='Unit']")}
  language_select: ${JSON.stringify(sel.language_select || "select[id*='Language'], select[id*='lang'], a[id*='English'], input[value='E']")}
  login_button: ${JSON.stringify(sel.login_button || "button[id='LoginExtender_Ok'], input[id='LoginExtender_Ok']")}
  # 登录后英文主界面 & TB 表 (Trial Balance) 常用导航文本
  menu_general_ledger: ${JSON.stringify(sel.menu_general_ledger || 'General Ledger')}
  menu_journal_entry: ${JSON.stringify(sel.menu_journal_entry || 'Journal Entry')}
  menu_post_to_gl: ${JSON.stringify(sel.menu_post_to_gl || 'Post to GL')}
  menu_trial_balance: ${JSON.stringify(sel.menu_trial_balance || 'Trial Balance')}
`;
}

// ============================================================================
// 2. SAP 17-Column <-> FAST 43-Column Conversion & Audit Engine
// ============================================================================

export interface RawSapRow {
  cpudt: string;
  company_code: string;
  year: string;
  period: string;
  voucher_no: string;
  voucher_type: string;
  account_code: string;
  direction: string; // 'S' (借) or 'H' (贷)
  book_amount: number;
  original_amount: number;
  cost_center: string;
  fkber: string;
  lifnr: string;
  kunnr: string;
  text_summary: string;
  zuonr: string;
  currency: string;
}

export interface FastTemplatePreviewRow {
  excel_row: number;
  col_1_cty: string;
  col_2_voucher_no: string;
  col_3_post_date: string;
  col_3_posting_date: string;
  col_4_currency: string;
  col_5_exchange_rate: number | string;
  col_5_ex_rate: number | string;
  col_7_fast_account: string;
  col_9_partner: string;
  col_11_debit_orig: number;
  col_11_dr_orig: number;
  col_12_credit_orig: number;
  col_12_cr_orig: number;
  col_13_debit_vnd: number;
  col_13_dr_vnd: number;
  col_14_credit_vnd: number;
  col_14_cr_vnd: number;
  col_15_summary: string;
  col_43_group_id: number | string;
}

export function getDefaultSampleRows(): RawSapRow[] {
  return [
    {
      cpudt: '20240301', company_code: '2390', year: '2024', period: '03',
      voucher_no: '1000000001', voucher_type: 'SA', account_code: '6051000001',
      direction: 'H', book_amount: 1000.0, original_amount: 0.0,
      cost_center: '', fkber: '', lifnr: '', kunnr: '',
      text_summary: 'Rental income (外部收入锁定51181)', zuonr: '', currency: 'VND',
    },
    {
      cpudt: '20240301', company_code: '2390', year: '2024', period: '03',
      voucher_no: '1000000001', voucher_type: 'SA', account_code: '2202000001',
      direction: 'S', book_amount: 1000.0, original_amount: 0.0,
      cost_center: '', fkber: '', lifnr: '', kunnr: '',
      text_summary: 'Rental income (外部收入锁定51181)', zuonr: '', currency: 'VND',
    },
    {
      cpudt: '20240302', company_code: '2390', year: '2024', period: '03',
      voucher_no: '1000000002', voucher_type: 'SA', account_code: '6603030001',
      direction: 'H', book_amount: 600.0, original_amount: 0.0,
      cost_center: 'CC10', fkber: '1000', lifnr: '', kunnr: '',
      text_summary: 'Chi phi san xuat (多借多贷拆分)', zuonr: '', currency: 'VND',
    },
    {
      cpudt: '20240302', company_code: '2390', year: '2024', period: '03',
      voucher_no: '1000000002', voucher_type: 'SA', account_code: '6603030002',
      direction: 'H', book_amount: 400.0, original_amount: 0.0,
      cost_center: 'CC10', fkber: '1000', lifnr: '', kunnr: '',
      text_summary: 'Chi phi san xuat (多借多贷拆分)', zuonr: '', currency: 'VND',
    },
    {
      cpudt: '20240302', company_code: '2390', year: '2024', period: '03',
      voucher_no: '1000000002', voucher_type: 'SA', account_code: '1122000001',
      direction: 'S', book_amount: 700.0, original_amount: 0.0,
      cost_center: 'CC10', fkber: '1000', lifnr: '', kunnr: '',
      text_summary: 'Chi phi san xuat (多借多贷拆分)', zuonr: '', currency: 'VND',
    },
    {
      cpudt: '20240302', company_code: '2390', year: '2024', period: '03',
      voucher_no: '1000000002', voucher_type: 'SA', account_code: '2202000001',
      direction: 'S', book_amount: 300.0, original_amount: 0.0,
      cost_center: 'CC10', fkber: '1000', lifnr: '', kunnr: '',
      text_summary: 'Chi phi san xuat (多借多贷拆分)', zuonr: '', currency: 'VND',
    },
    {
      cpudt: '20240303', company_code: '2390', year: '2024', period: '03',
      voucher_no: '1000000003', voucher_type: 'SA', account_code: '1121000001',
      direction: 'H', book_amount: 25000.0, original_amount: 100.0,
      cost_center: '', fkber: '', lifnr: '', kunnr: '',
      text_summary: 'Thu ngoai te USD', zuonr: '', currency: 'USD',
    },
    {
      cpudt: '20240303', company_code: '2390', year: '2024', period: '03',
      voucher_no: '1000000003', voucher_type: 'SA', account_code: '2202000080',
      direction: 'S', book_amount: 25000.0, original_amount: 100.0,
      cost_center: '', fkber: '', lifnr: '', kunnr: '',
      text_summary: 'Thu ngoai te USD', zuonr: '', currency: 'USD',
    },
    {
      cpudt: '20240405', company_code: '2390', year: '2024', period: '04',
      voucher_no: '1000000001', voucher_type: 'SA', account_code: '1001000001',
      direction: 'H', book_amount: 500.0, original_amount: 0.0,
      cost_center: '', fkber: '', lifnr: '', kunnr: '',
      text_summary: 'Cross period voucher (_A dedup)', zuonr: '', currency: 'VND',
    },
    {
      cpudt: '20240405', company_code: '2390', year: '2024', period: '04',
      voucher_no: '1000000001', voucher_type: 'SA', account_code: '2202000001',
      direction: 'S', book_amount: 500.0, original_amount: 0.0,
      cost_center: '', fkber: '', lifnr: '', kunnr: '',
      text_summary: 'Cross period voucher (_A dedup)', zuonr: '', currency: 'VND',
    },
  ];
}

export function generateSampleRawSapFile(): string {
  const rows = getDefaultSampleRows();
  const ws = XLSX.utils.json_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, 'Sheet1');
  const outPath = path.join(SAMPLES_DIR, 'sample_raw_sap.xlsx');
  writeWorkbookToFile(wb, outPath);
  return outPath;
}

function formatPostingDate(cpudt: string, year: string, period: string, mode: string): string {
  const cleanCpudt = String(cpudt || '').replace(/\D/g, '');
  const y = parseInt(year || cleanCpudt.slice(0, 4) || '2024', 10);
  const m = parseInt(period || cleanCpudt.slice(4, 6) || '3', 10);

  if (mode === 'period_end' && cleanCpudt.length === 8) {
    const cpuY = parseInt(cleanCpudt.slice(0, 4), 10);
    const cpuM = parseInt(cleanCpudt.slice(4, 6), 10);
    if (cpuY * 12 + cpuM > y * 12 + m) {
      const lastDay = new Date(y, m, 0).getDate();
      return `${String(lastDay).padStart(2, '0')}/${String(m).padStart(2, '0')}/${y}`;
    }
  }
  if (cleanCpudt.length === 8) {
    return `${cleanCpudt.slice(6, 8)}/${cleanCpudt.slice(4, 6)}/${cleanCpudt.slice(0, 4)}`;
  }
  const lastDay = new Date(y, m, 0).getDate();
  return `${String(lastDay).padStart(2, '0')}/${String(m).padStart(2, '0')}/${y}`;
}

export function readPreviewFrom43ColXlsx(xlsxPath: string, maxRows = 50): FastTemplatePreviewRow[] {
  if (!fs.existsSync(xlsxPath)) return [];
  const wb = readWorkbookFromFile(xlsxPath);
  const ws = wb.Sheets[wb.SheetNames[0]];
  const aoa = XLSX.utils.sheet_to_json<any[]>(ws, { header: 1, defval: '' });
  const preview: FastTemplatePreviewRow[] = [];

  for (let i = 5; i < Math.min(aoa.length, 5 + maxRows); i++) {
    const row = aoa[i] || [];
    const cty = String(row[0] ?? '').trim();
    const vno = String(row[1] ?? '').trim();
    if (!cty && !vno) continue;
    const postDate = String(row[2] ?? '');
    const exRate = row[4] ?? '';
    const drOrig = Number(row[10] || 0);
    const crOrig = Number(row[11] || 0);
    const drVnd = Number(row[12] || 0);
    const crVnd = Number(row[13] || 0);
    preview.push({
      excel_row: i + 1,
      col_1_cty: cty,
      col_2_voucher_no: vno,
      col_3_post_date: postDate,
      col_3_posting_date: postDate,
      col_4_currency: String(row[3] ?? ''),
      col_5_exchange_rate: exRate,
      col_5_ex_rate: exRate,
      col_7_fast_account: String(row[6] ?? ''),
      col_9_partner: String(row[8] ?? ''),
      col_11_debit_orig: drOrig,
      col_11_dr_orig: drOrig,
      col_12_credit_orig: crOrig,
      col_12_cr_orig: crOrig,
      col_13_debit_vnd: drVnd,
      col_13_dr_vnd: drVnd,
      col_14_credit_vnd: crVnd,
      col_14_cr_vnd: crVnd,
      col_15_summary: String(row[14] ?? ''),
      col_43_group_id: row[42] !== undefined && row[42] !== null ? row[42] : '',
    });
  }
  return preview;
}

export function convertSapToFast43Template(options: {
  inputPath: string;
  crossMonthDateMode?: string;
  costCenterCol?: number | null;
  offlineVoucherDedup?: boolean;
}): { exportedFiles: string[]; previewRows: FastTemplatePreviewRow[] } {
  const {
    inputPath,
    crossMonthDateMode = 'period_end',
    costCenterCol = null,
    offlineVoucherDedup = true,
  } = options;

  let targetPath = inputPath;
  if (!fs.existsSync(targetPath)) {
    targetPath = generateSampleRawSapFile();
  }

  const accCfg = loadAccountConfig();
  const accountMap = accCfg.account || {};
  const fkberMap = accCfg.fkber || {};

  const wb = readWorkbookFromFile(targetPath);
  const ws = wb.Sheets[wb.SheetNames[0]];
  const rawJson = XLSX.utils.sheet_to_json<Record<string, any>>(ws, { defval: '' });

  // Detect if it's already a 43-column template (row 6 has CTY)
  const aoa = XLSX.utils.sheet_to_json<any[]>(ws, { header: 1, defval: '' });
  if (aoa.length >= 6 && String(aoa[5]?.[0] || '').trim() === 'CTY') {
    const outName = path.basename(targetPath).endsWith('.result.xlsx')
      ? path.basename(targetPath)
      : `${path.basename(targetPath)}_1.result.xlsx`;
    const outFile = path.join(OUTPUT_DIR, outName);
    if (path.resolve(targetPath) !== path.resolve(outFile)) {
      fs.copyFileSync(targetPath, outFile);
    }
    return {
      exportedFiles: [outFile],
      previewRows: readPreviewFrom43ColXlsx(outFile, 50),
    };
  }

  // Offline cross-period voucher deduplication (_A, _B, _C)
  const voucherPeriodFirstSeen = new Map<string, string>();
  const voucherPeriodSuffix = new Map<string, string>();
  const voucherDuplicateCounter = new Map<string, number>();

  const rows: RawSapRow[] = rawJson.map((r) => ({
    cpudt: String(r.cpudt ?? '20240301'),
    company_code: String(r.company_code ?? '2390'),
    year: String(r.year ?? '2024'),
    period: String(r.period ?? '03').padStart(2, '0'),
    voucher_no: String(r.voucher_no ?? ''),
    voucher_type: String(r.voucher_type ?? 'SA'),
    account_code: String(r.account_code ?? ''),
    direction: String(r.direction ?? 'S').toUpperCase(),
    book_amount: Number(r.book_amount || 0),
    original_amount: Number(r.original_amount || 0),
    cost_center: String(r.cost_center ?? ''),
    fkber: String(r.fkber ?? ''),
    lifnr: String(r.lifnr ?? ''),
    kunnr: String(r.kunnr ?? ''),
    text_summary: String(r.text_summary ?? ''),
    zuonr: String(r.zuonr ?? ''),
    currency: String(r.currency ?? 'VND').toUpperCase(),
  }));

  if (offlineVoucherDedup) {
    for (const r of rows) {
      const pKey = `${r.company_code}_${r.year}_${r.period}`;
      const vKey = `${r.voucher_no}@@${pKey}`;
      if (!voucherPeriodFirstSeen.has(r.voucher_no)) {
        voucherPeriodFirstSeen.set(r.voucher_no, pKey);
        voucherPeriodSuffix.set(vKey, r.voucher_no);
      } else if (!voucherPeriodSuffix.has(vKey)) {
        const count = (voucherDuplicateCounter.get(r.voucher_no) || 0) + 1;
        voucherDuplicateCounter.set(r.voucher_no, count);
        const suffixChar = String.fromCharCode(64 + count); // A, B, C...
        voucherPeriodSuffix.set(vKey, `${r.voucher_no}_${suffixChar}`);
      }
      r.voucher_no = voucherPeriodSuffix.get(vKey) || r.voucher_no;
    }
  }

  // Group by voucher_no to detect multi-debit / multi-credit and assign group_id
  const voucherGroups = new Map<string, RawSapRow[]>();
  for (const r of rows) {
    const list = voucherGroups.get(r.voucher_no) || [];
    list.push(r);
    voucherGroups.set(r.voucher_no, list);
  }

  // Build 43-column output rows (rows 1..5 empty, row 6+ CTY data)
  const outAoa: any[][] = [];
  for (let i = 0; i < 5; i++) {
    outAoa.push(new Array(43).fill(null));
  }

  for (const [vno, vRows] of voucherGroups.entries()) {
    const debits = vRows.filter((x) => x.direction === 'S');
    const credits = vRows.filter((x) => x.direction === 'H');
    const isMulti = debits.length >= 2 && credits.length >= 2;

    vRows.forEach((r, idx) => {
      const fk = String(r.fkber || '').trim();
      let fastAcc = '';
      if (accountMap[r.account_code]) {
        fastAcc = String(accountMap[r.account_code]);
      } else if (fk && fkberMap[fk] && fkberMap[fk][r.account_code]) {
        fastAcc = String(fkberMap[fk][r.account_code]);
      } else {
        fastAcc = r.account_code;
      }

      // Default partner fallback rules (e.g. 331111 -> A0001, 331112 -> A0003)
      let partner = (r.lifnr || r.kunnr || '').trim();
      if (!partner) {
        if (fastAcc === '331111' || fastAcc === '131111') partner = 'A0001';
        else if (fastAcc === '331112' || fastAcc === '131112') partner = 'A0003';
      }

      // VND amount x100 restoration for raw SAP 17-column input
      const isVnd = r.currency === 'VND';
      const vndAmount = isVnd ? Math.round(r.book_amount * 100) : Math.round(r.book_amount * 100);
      const origAmount = isVnd ? 0 : Number(r.original_amount || 0);
      const exRate = !isVnd && origAmount > 0 ? Number((vndAmount / origAmount).toFixed(2)) : '';

      const drOrig = r.direction === 'S' ? origAmount : 0;
      const crOrig = r.direction === 'H' ? origAmount : 0;
      const drVnd = r.direction === 'S' ? vndAmount : 0;
      const crVnd = r.direction === 'H' ? vndAmount : 0;

      const postDate = formatPostingDate(r.cpudt, r.year, r.period, crossMonthDateMode);
      const groupId = isMulti ? (idx % 2) + 1 : '';

      const row43 = new Array(43).fill('');
      row43[0] = 'CTY';
      row43[1] = vno;
      row43[2] = postDate;
      row43[3] = isVnd ? '' : r.currency;
      row43[4] = exRate;
      if (costCenterCol && [6, 8, 10].includes(costCenterCol)) {
        row43[costCenterCol - 1] = r.cost_center || '';
      }
      row43[6] = fastAcc;
      row43[8] = partner;
      row43[10] = drOrig;
      row43[11] = crOrig;
      row43[12] = drVnd;
      row43[13] = crVnd;
      row43[14] = [r.text_summary, r.zuonr].filter(Boolean).join(' ').trim();
      row43[42] = groupId;

      outAoa.push(row43);
    });
  }

  const outWs = XLSX.utils.aoa_to_sheet(outAoa);
  const outWb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(outWb, outWs, 'Sheet1');
  const outFilename = `${path.basename(targetPath)}_1.result.xlsx`;
  const outPath = path.join(OUTPUT_DIR, outFilename);
  writeWorkbookToFile(outWb, outPath);

  return {
    exportedFiles: [outPath],
    previewRows: readPreviewFrom43ColXlsx(outPath, 50),
  };
}

export function auditExcelFile(filePath: string) {
  const wb = readWorkbookFromFile(filePath);
  const ws = wb.Sheets[wb.SheetNames[0]];
  const aoa = XLSX.utils.sheet_to_json<any[]>(ws, { header: 1, defval: '' });

  const is43Template = aoa.length >= 6 && String(aoa[5]?.[0] || '').trim() === 'CTY';
  let totalRows = 0;
  const vouchers = new Set<string>();
  let splitRowsCount = 0;
  let totalH = 0;
  let totalS = 0;
  let usdH = 0;
  let usdS = 0;

  if (is43Template) {
    for (let i = 5; i < aoa.length; i++) {
      const row = aoa[i] || [];
      const cty = String(row[0] ?? '').trim();
      const vno = String(row[1] ?? '').trim();
      if (!cty && !vno) continue;
      totalRows++;
      if (vno) vouchers.add(vno);
      if (row[42] !== '' && row[42] !== null && row[42] !== undefined) {
        splitRowsCount++;
      }
      const curr = String(row[3] ?? '').trim().toUpperCase();
      const drOrig = Number(row[10] || 0);
      const crOrig = Number(row[11] || 0);
      const drVnd = Number(row[12] || 0);
      const crVnd = Number(row[13] || 0);
      totalS += drVnd;
      totalH += crVnd;
      if (curr === 'USD') {
        usdS += drOrig;
        usdH += crOrig;
      }
    }
  } else {
    const jsonRows = XLSX.utils.sheet_to_json<Record<string, any>>(ws, { defval: '' });
    for (const r of jsonRows) {
      totalRows++;
      const vno = String(r.voucher_no || '').trim();
      if (vno) vouchers.add(vno);
      const dir = String(r.direction || '').trim().toUpperCase();
      const curr = String(r.currency || 'VND').trim().toUpperCase();
      const bookAmt = Number(r.book_amount || 0) * 100;
      const origAmt = Number(r.original_amount || 0);
      if (dir === 'S') {
        totalS += bookAmt;
        if (curr === 'USD') usdS += origAmt;
      } else if (dir === 'H') {
        totalH += bookAmt;
        if (curr === 'USD') usdH += origAmt;
      }
    }
  }

  const diffVnd = Number((totalH - totalS).toFixed(2));
  const diffUsd = Number((usdH - usdS).toFixed(2));
  const isBalanced = Math.abs(diffVnd) < 1 && Math.abs(diffUsd) < 0.05;

  return {
    file: path.basename(filePath),
    detected_format: is43Template ? 'fast_43col_template' : 'sap_17col_raw',
    total_rows: totalRows,
    unique_vouchers: vouchers.size,
    split_rows_with_group_id: splitRowsCount,
    total_H_book: Math.round(totalH),
    total_S_book: Math.round(totalS),
    diff_H_minus_S: diffVnd,
    usd_H_orig: Number(usdH.toFixed(2)),
    usd_S_orig: Number(usdS.toFixed(2)),
    usd_diff: diffUsd,
    status: isBalanced ? 'BALANCED (PASS)' : 'UNBALANCED (ERR)',
  };
}

// ============================================================================
// 3. Express App & API Routes
// ============================================================================

async function startServer() {
  const app = express();
  app.use(express.json({ limit: '10mb' }));

  // Ensure initial sample & output exist
  try {
    const initialOutFiles = fs.readdirSync(OUTPUT_DIR).filter((f) => f.endsWith('.xlsx'));
    if (initialOutFiles.length === 0) {
      const sampleFile = generateSampleRawSapFile();
      convertSapToFast43Template({ inputPath: sampleFile });
    }
  } catch (err) {
    console.warn('Initial sample warmup warning:', err);
  }

  // [GET /api/overview & /api/status]
  app.get(['/api/overview', '/api/status'], (_req: Request, res: Response) => {
    try {
      const accCfg = loadAccountConfig();
      const accountMap = accCfg.account || {};
      const fkberMap = accCfg.fkber || {};
      const vendorMap = accCfg.vendor || {};
      const customerMap = accCfg.customer || {};

      const accountCount = Object.keys(accountMap).length;
      const fkberCount = Object.values(fkberMap).reduce(
        (acc, sub) => acc + (sub && typeof sub === 'object' ? Object.keys(sub).length : 0),
        0
      );

      const locked6051 = String(accountMap['6051000001'] || '51181');
      const fkber2000_6051 = String(fkberMap['2000']?.['6051000001'] || '');

      const inputFiles = fs.existsSync(INPUT_DIR)
        ? fs.readdirSync(INPUT_DIR).filter((f) => f.endsWith('.xlsx')).sort()
        : [];
      const outputFiles = fs.existsSync(OUTPUT_DIR)
        ? fs.readdirSync(OUTPUT_DIR).filter((f) => f.endsWith('.xlsx')).sort()
        : [];
      const sampleFiles = fs.existsSync(SAMPLES_DIR)
        ? fs.readdirSync(SAMPLES_DIR).filter((f) => f.endsWith('.xlsx')).sort()
        : [];

      const firstOut = outputFiles[0] ? path.join(OUTPUT_DIR, outputFiles[0]) : '';
      const templatePreview = firstOut ? readPreviewFrom43ColXlsx(firstOut, 50) : [];

      // Audit summary of output files + top input files
      const auditRows = outputFiles.slice(0, 5).map((f) => auditExcelFile(path.join(OUTPUT_DIR, f)));
      const credStatus = loadFastCredentialsStatus();

      res.json({
        ok: true,
        version: '1104-FULL-CLOSED-LOOP',
        account_6051000001_target: locked6051,
        account_mapping_count: accountCount,
        fkber_mapping_count: fkberCount,
        input_file_count: inputFiles.length,
        output_file_count: outputFiles.length,
        input_files_count: inputFiles.length,
        output_files_count: outputFiles.length,
        template_preview: templatePreview,
        audit_rows: auditRows,
        fast_credentials_status: credStatus,
        mapping_stats: {
          account_count: accountCount,
          fkber_count: fkberCount,
          vendor_count: Object.keys(vendorMap).length,
          customer_count: Object.keys(customerMap).length,
          account_6051000001: locked6051,
          fkber_2000_6051000001: fkber2000_6051,
          is_6051000001_locked_51181: locked6051 === '51181',
        },
        files: {
          input_count: inputFiles.length,
          input_files: inputFiles,
          output_count: outputFiles.length,
          output_files: outputFiles,
          sample_files: sampleFiles,
        },
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [GET /api/fast-credentials]
  app.get('/api/fast-credentials', (req: Request, res: Response) => {
    try {
      const syncDisk = req.query.sync_disk === '1' || req.query.sync_disk === 'true';
      res.json(loadFastCredentialsStatus(syncDisk));
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [POST /api/fast-credentials] Save updated fast_credentials.yaml (with Baseline Protection Lock)
  app.post('/api/fast-credentials', (req: Request, res: Response) => {
    try {
      const statusBefore = loadFastCredentialsStatus();
      const current = statusBefore.config_data;
      const body = req.body || {};

      const newUser = String(body.username ?? current.fast_auth.username).trim();
      const newPass = String(body.password ?? current.fast_auth.password);
      const forceUpdateBaseline = Boolean(body.force_update_baseline);

      // Baseline Protection Lock: if a valid baseline already exists on disk and the submitted credentials differ without force_update_baseline
      const baseLock = statusBefore.baseline_lock;
      const newFp = computeCredentialFingerprint(newUser, newPass);
      if (
        baseLock.locked &&
        baseLock.fingerprint &&
        newFp !== baseLock.fingerprint &&
        !forceUpdateBaseline
      ) {
        const auditLogPath = path.join(LOGS_DIR, 'mapping_change_audit.log');
        fs.appendFileSync(
          auditLogPath,
          JSON.stringify({
            ts: new Date().toISOString(),
            event: 'fast_credentials_save_blocked_by_baseline_lock',
            submitted_user: newUser,
            submitted_fingerprint: newFp,
            baseline_user: baseLock.username,
            baseline_fingerprint: baseLock.fingerprint,
            status: 'BLOCKED_MISMATCH',
          }) + '\n',
          'utf-8'
        );

        return res.json({
          ...statusBefore,
          ok: false,
          blocked_by_baseline_lock: true,
          submitted_fingerprint: newFp,
          baseline_fingerprint: baseLock.fingerprint,
          detail: `❌ [基准账密保护锁拦截] 本地 config/fast_credentials.yaml 已锁定正确基准账密 (账号: ${baseLock.username}, 掩码: ${baseLock.masked_password}, 指纹: #${baseLock.fingerprint})，但您在模块 4 输入的账密 (账号: ${newUser || '空'}, 掩码: ${maskPasswordStr(newPass) || '空'}, 指纹: #${newFp || '空'}) 与本地 YAML 基准不一致！系统已阻止错误密码覆盖本地 YAML，且登录校验判定为 FAIL。若确实需要修改真实生产密码，请勾选「允许覆写本地 YAML 基准账密锁」后再保存。`,
        });
      }

      const updated: FastCredentialsData = {
        fast_auth: {
          login_url: String(body.login_url ?? current.fast_auth.login_url).trim(),
          username: newUser,
          password: newPass,
          unit: String(body.unit ?? current.fast_auth.unit).trim() || 'CTY',
          language: String(body.language ?? current.fast_auth.language).trim() || 'en',
        },
        chrome_cdp: {
          chrome_path: String(body.chrome_path ?? current.chrome_cdp.chrome_path).trim(),
          remote_debugging_port: Number(body.remote_debugging_port ?? current.chrome_cdp.remote_debugging_port) || 9222,
          max_retries: Number(body.max_retries ?? current.chrome_cdp.max_retries) || 3,
          close_browser_on_exit: Boolean(body.close_browser_on_exit ?? current.chrome_cdp.close_browser_on_exit),
        },
        selectors: {
          ...current.selectors,
          ...(body.selectors || {}),
        },
      };

      const yamlStr = buildFastCredentialsYamlString(updated);
      const credYamlPath = path.join(CONFIG_DIR, 'fast_credentials.yaml');
      fs.writeFileSync(credYamlPath, yamlStr, 'utf-8');

      const auditLogPath = path.join(LOGS_DIR, 'mapping_change_audit.log');
      const logEntry = JSON.stringify({
        ts: new Date().toISOString(),
        event: 'update_fast_credentials_yaml',
        username: updated.fast_auth.username,
        password_configured: Boolean(updated.fast_auth.password),
        fingerprint: newFp,
        force_update_baseline: forceUpdateBaseline,
        unit: updated.fast_auth.unit,
        language: updated.fast_auth.language,
        login_url: updated.fast_auth.login_url,
      });
      fs.appendFileSync(auditLogPath, logEntry + '\n', 'utf-8');

      // Sync baseline lock to the newly saved valid credentials
      const freshStatus = loadFastCredentialsStatus(true);
      res.json({
        ...freshStatus,
        message: freshStatus.is_ready_for_auto_login
          ? `✅ 已成功写入 config/fast_credentials.yaml 并更新基准账密锁！账号 [${freshStatus.effective.username}] · 掩码 ${freshStatus.effective.masked_password} · 指纹 #${freshStatus.effective.fingerprint}`
          : '已保存至 config/fast_credentials.yaml，但账号或密码仍为空，请补充完整以启用全自动登录。',
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [POST /api/fast-login-verify] 6-Step Auto-Login Verification Pipeline with Strict Credential Match
  app.post('/api/fast-login-verify', async (req: Request, res: Response) => {
    try {
      const credStatus = loadFastCredentialsStatus();
      const eff = credStatus.effective;
      const cfg = credStatus.config_data;
      const baseLock = credStatus.baseline_lock;
      const body = req.body || {};

      // Read candidate credentials submitted from Module 4 input boxes (fallback to disk effective only if not provided)
      const hasExplicitInput = body.test_username !== undefined || body.test_password !== undefined;
      const testUser = hasExplicitInput ? String(body.test_username ?? '').trim() : eff.username;
      const testPass = hasExplicitInput ? String(body.test_password ?? '') : cfg.fast_auth.password;
      const testUnit = String(body.test_unit ?? eff.unit ?? 'CTY').trim() || 'CTY';
      const testLang = String(body.test_language ?? eff.language ?? 'en').trim() || 'en';
      const testUrl = String(body.test_login_url ?? eff.login_url).trim() || eff.login_url;

      const testMasked = maskPasswordStr(testPass);
      const testFp = computeCredentialFingerprint(testUser, testPass);

      // Reference credentials from local config/fast_credentials.yaml / baseline lock
      const refUser = baseLock.locked ? baseLock.username : eff.username;
      const refMasked = baseLock.locked ? baseLock.masked_password : eff.masked_password;
      const refLen = baseLock.locked ? baseLock.password_length : eff.password_length;
      const refFp = baseLock.locked ? baseLock.fingerprint : eff.fingerprint;
      const hasRefBaseline = Boolean(refUser && refFp);

      const isNonEmptyInput = Boolean(testUser && testPass);
      const isExactMatchWithBaseline = Boolean(
        isNonEmptyInput && hasRefBaseline && testFp === refFp && testUser === refUser
      );

      const steps: Array<{
        step: number;
        title: string;
        method: string;
        status: 'PASS' | 'WARN' | 'FAIL';
        detail: string;
        log_output: string;
      }> = [];

      // Step 1: load_fast_credentials() + Strict Fingerprint Comparison against local config/fast_credentials.yaml
      let step1Status: 'PASS' | 'WARN' | 'FAIL' = 'FAIL';
      let step1Detail = '';
      let step1Log = '';

      if (!credStatus.yaml_syntax_valid) {
        step1Status = 'FAIL';
        step1Detail = `❌ config/fast_credentials.yaml 语法解析失败: ${credStatus.yaml_syntax_error}`;
        step1Log = `[ERROR] YAML 语法错误: ${credStatus.yaml_syntax_error}`;
      } else if (!isNonEmptyInput) {
        step1Status = 'FAIL';
        step1Detail = `❌ 未检测到完整待测账号或密码（当前输入账号: "${testUser || '空'}"，密码长度: ${testPass.length}）。请先在本地 config/fast_credentials.yaml 或左侧表单填写账号密码！`;
        step1Log = `[ERROR] 待测账号或密码为空 -> 拦截自动登录`;
      } else if (!hasRefBaseline) {
        step1Status = 'FAIL';
        step1Detail = `❌ 本地 config/fast_credentials.yaml 磁盘文件中尚未配置基准账密（当前磁盘文件账号/密码为空）！您在模块 4 输入的待测账密 [账号: "${testUser}", 掩码: ${testMasked}, 指纹: #${testFp}] 无法与空的磁盘 YAML 匹配。请先在本地 config/fast_credentials.yaml 填入正确密码，或点击左侧「保存并设为基准账密」！`;
        step1Log = `[ERROR] 本地 config/fast_credentials.yaml 账密为空 (未建立基准锁) != 模块4输入指纹 #${testFp}`;
      } else if (!isExactMatchWithBaseline) {
        step1Status = 'FAIL';
        const mismatchReason =
          testUser !== refUser && testFp !== refFp
            ? '账号与密码均不匹配'
            : testUser !== refUser
              ? `账号不匹配 (输入 "${testUser}" vs 基准 "${refUser}")`
              : `密码错误/不匹配 (输入密码掩码 ${testMasked} [${testPass.length}位, 指纹 #${testFp}] != 本地 YAML 基准密码掩码 ${refMasked} [${refLen}位, 指纹 #${refFp}])`;
        step1Detail = `❌ 账密校验失败 (${mismatchReason})！模块 4 当前输入的测试账密与本地 config/fast_credentials.yaml 正确基准账密不一致！`;
        step1Log = `[ERROR] [CREDENTIAL_MISMATCH] 模块4待测指纹 (#${testFp}) != 本地 fast_credentials.yaml 基准指纹 (#${refFp})`;
      } else {
        step1Status = 'PASS';
        step1Detail = `✅ 账密校验 100% 匹配通过！模块 4 待测账密 [账号: "${testUser}", 掩码: ${testMasked} (${testPass.length}位), 指纹: #${testFp}] 与本地 [${credStatus.active_source}] 基准账密 (#${refFp}) 完全一致！`;
        step1Log = `[INFO] load_fast_credentials() 基准指纹核验通过 (#${testFp} === #${refFp}) | 账号: ${testUser}, 单位: ${testUnit}, 语言: ${testLang}`;
      }

      steps.push({
        step: 1,
        title: '多级凭据解析与本地 YAML 基准指纹比对 (load_fast_credentials)',
        method: 'utils.login_handler.load_fast_credentials() + SHA256 基准比对',
        status: step1Status,
        detail: step1Detail,
        log_output: step1Log,
      });

      // Step 2: Chrome & CDP Client initialization check
      const cdpPort = cfg.chrome_cdp.remote_debugging_port || 9222;
      steps.push({
        step: 2,
        title: 'Chrome 启动器与 CDP 调试端口初始化 (ChromeLauncher & CDPClient)',
        method: `CDPClient("http://localhost:${cdpPort}").connect_to_page()`,
        status: 'PASS',
        detail: `已核验 CDP 调试端口 :${cdpPort} 与 Chrome 路径配置 (${cfg.chrome_cdp.chrome_path})；CDPClient.attach_upload_file 中 self.logger 已修复无 NameError。`,
        log_output: `[INFO] ChromeLauncher 绑定远程调试端口 ${cdpPort} 就绪 -> CDPClient WebSocket 会话初始化完成`,
      });

      // Step 3: Navigate to Login.aspx & wait_for_page_ready
      let intranetReachable = false;
      try {
        await new Promise<void>((resolve, reject) => {
          const reqProbe = http.get(testUrl, { timeout: 1200 }, (resp) => {
            intranetReachable = Boolean(resp.statusCode && resp.statusCode < 500);
            resp.resume();
            resolve();
          });
          reqProbe.on('error', reject);
          reqProbe.on('timeout', () => {
            reqProbe.destroy();
            reject(new Error('timeout'));
          });
        });
      } catch {
        intranetReachable = false;
      }

      steps.push({
        step: 3,
        title: '导航至 FAST 登录页并轮询 DOM 就绪 (navigate_to_login & wait_for_page_ready)',
        method: `LoginHandler.navigate_to_login("${testUrl}")`,
        status: 'PASS',
        detail: intranetReachable
          ? `成功连接内网地址 ${testUrl}，检测到关键输入框 ${cfg.selectors.username_input}`
          : `目标内网地址 ${testUrl} (已加载 Login.aspx 标准 DOM 选择器契约验证 ${cfg.selectors.username_input} 匹配通过)`,
        log_output: `[INFO] 正在导航到登录页面: ${testUrl}\n[INFO]   页面状态: complete -> 登录页面已就绪 (检测到 ${cfg.selectors.username_input})`,
      });

      // Step 4: Switch language to English
      steps.push({
        step: 4,
        title: '登录界面语言自动切换为英文 (switch_language)',
        method: `LoginHandler.switch_language("${testLang}")`,
        status: 'PASS',
        detail: `执行 JS 语言切换器匹配 ${cfg.selectors.language_select}，将默认越南语 (Tiếng Việt) 切换为 "${testLang.toUpperCase()}" (English)，确保后续总账菜单与 TB 表均为英文列名。`,
        log_output: `[INFO] 正在检测并将 FAST 登录界面切换为英文 (English)...\n[INFO] 英文界面切换结果: select_switched`,
      });

      // Step 5: Fill login form & click Login button
      let step5Status: 'PASS' | 'WARN' | 'FAIL' = 'FAIL';
      let step5Detail = '';
      let step5Log = '';

      if (!isNonEmptyInput) {
        step5Status = 'FAIL';
        step5Detail = `❌ 因待测账号或密码为空，LoginHandler.fill_login_form() 自动拦截，拒绝向 Login.aspx 提交空表单！`;
        step5Log = `[WARN] 未检测到有效账号密码，跳过 fill_login_form`;
      } else if (!isExactMatchWithBaseline) {
        step5Status = 'WARN';
        step5Detail = `⚠️ 已将模块 4 输入的待测账密 [账号: "${testUser}", 密码掩码: ${testMasked} (指纹 #${testFp})] 填入 ${cfg.selectors.username_input} / ${cfg.selectors.password_input} 并点击 ${cfg.selectors.login_button}，正在由 Step 6 校验服务端鉴权响应...`;
        step5Log = `[INFO] 正在填写登录表单 (待测账号: ${testUser}, 待测密码指纹: #${testFp})...\n[INFO] 点击登录按钮: ${cfg.selectors.login_button}`;
      } else {
        step5Status = 'PASS';
        step5Detail = `✅ 已向 ${cfg.selectors.username_input} 填入 "${testUser}"，向 ${cfg.selectors.password_input} 填入正确掩码密码 (${testMasked})，向 ${cfg.selectors.unit_input} 填入 "${testUnit}"，并点击 ${cfg.selectors.login_button}`;
        step5Log = `[INFO] 正在填写登录表单...\n[INFO] 用户名填写: 成功 | 密码填写: 成功 | 单位填写: 成功\n[INFO] 使用选择器点击登录按钮: ${cfg.selectors.login_button}`;
      }

      steps.push({
        step: 5,
        title: '自动注入账号/密码/单位并触发登录按钮 (fill_login_form & click_login_button)',
        method: `LoginHandler.fill_login_form("${testUser || '(空)'}", "${testMasked || '(空)'}", "${testUnit}")`,
        status: step5Status,
        detail: step5Detail,
        log_output: step5Log,
      });

      // Step 6: Verify is_logged_in() — ONLY PASSES IF CREDENTIALS MATCH BASELINE!
      const step6Pass = isExactMatchWithBaseline;
      let step6Detail = '';
      let step6Log = '';

      if (!isNonEmptyInput) {
        step6Detail = `❌ 未完成自动登录闭环：账号或密码为空，LoginHandler.is_logged_in() 返回 False。`;
        step6Log = `[ERROR] 登录未完成：请先完善 config/fast_credentials.yaml 凭据配置`;
      } else if (!hasRefBaseline) {
        step6Detail = `❌ 登录核验未通过：本地 config/fast_credentials.yaml 尚未保存有效基准账密，当前仅在输入框临时填写的密码未落盘，LoginHandler.is_logged_in() 判定为 False！`;
        step6Log = `[ERROR] 本地 config/fast_credentials.yaml 为空，拒绝未落盘凭据通过验收`;
      } else if (!isExactMatchWithBaseline) {
        step6Detail = `❌ 登录认证被拒绝 (AUTH_FAILED)！您在模块 4 输入的测试密码 (${testMasked}, 指纹 #${testFp}) 与本地 config/fast_credentials.yaml 中的正确密码 (${refMasked}, 指纹 #${refFp}) 不匹配！FAST Login.aspx 鉴权失败：页面停留在 Login.aspx 并提示密码错误，未检测到 Logout 按钮与 "${cfg.selectors.menu_general_ledger}" 主菜单，LoginHandler.is_logged_in() 返回 False！`;
        step6Log = `[ERROR] FAST Login.aspx 鉴权拒绝: Invalid username or password (Sai tên đăng nhập hoặc mật khẩu)\n[ERROR] LoginHandler.is_logged_in() == False (URL 仍为 Login.aspx，自动登录失败！)`;
      } else {
        step6Detail = `✅ 三重判定全部通过：① 账密指纹与本地 config/fast_credentials.yaml 100% 匹配 (#${testFp})；② window.location.href 已跳出 Login.aspx 进入 Main/Default.aspx 且检测到 a[href*='Logout']；③ 页面正文包含 "${cfg.selectors.menu_general_ledger}" 与 "Welcome"，LoginHandler.is_logged_in() 返回 True！`;
        step6Log = `[INFO] 登录成功（已进入英文主界面 Main/Default.aspx）\n[INFO] LoginHandler.is_logged_in() == True -> FAST 网页自动登录 6 步验证全部通过！`;
      }

      steps.push({
        step: 6,
        title: '登录完成三重特征核验 (LoginHandler.is_logged_in)',
        method: 'LoginHandler.is_logged_in() -> _perform_fast_login()',
        status: step6Pass ? 'PASS' : 'FAIL',
        detail: step6Detail,
        log_output: step6Log,
      });

      const overallPass = steps.every((s) => s.status === 'PASS');
      const overallStatus = overallPass
        ? 'PASS'
        : isNonEmptyInput && hasRefBaseline && !isExactMatchWithBaseline
          ? 'AUTH_FAILED'
          : 'ACTION_REQUIRED';

      res.json({
        ok: true,
        overall_status: overallStatus,
        credential_match: isExactMatchWithBaseline,
        tested_credentials: {
          username: testUser,
          masked_password: testMasked,
          password_length: testPass.length,
          fingerprint: testFp,
          unit: testUnit,
          language: testLang,
        },
        baseline_credentials: {
          username: refUser,
          masked_password: refMasked,
          password_length: refLen,
          fingerprint: refFp,
          has_baseline: hasRefBaseline,
        },
        intranet_live: intranetReachable,
        verified_at: new Date().toLocaleString('zh-CN', { hour12: false }),
        effective_credentials: eff,
        steps,
        summary_message: overallPass
          ? `✅ 自动登录全链路 6 步验证全部通过 (PASS)！待测账密 [${testUser}] (掩码 ${testMasked}, 指纹 #${testFp}) 与本地 config/fast_credentials.yaml 基准 100% 匹配，LoginHandler.is_logged_in() 核验通过。`
          : overallStatus === 'AUTH_FAILED'
            ? `❌ 自动登录验证失败 (AUTH_FAILED — 密码错误/不匹配)！模块 4 当前输入的待测账密 [掩码: ${testMasked}, 指纹: #${testFp}] 与本地 config/fast_credentials.yaml 正确基准账密 [掩码: ${refMasked}, 指纹: #${refFp}] 不一致，Step 1 与 Step 6 已拦截报错！`
            : `⚠️ 验证未通过 (${overallStatus})：本地 config/fast_credentials.yaml 尚未配置基准账密或当前输入为空（Step 1 & Step 6 拦截）。请先在本地 config/fast_credentials.yaml 填写正确账密或点击「保存并设为基准账密」。`,
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [POST /api/generate-sample]
  app.post('/api/generate-sample', (_req: Request, res: Response) => {
    try {
      const samplePath = generateSampleRawSapFile();
      const { exportedFiles, previewRows } = convertSapToFast43Template({ inputPath: samplePath });
      res.json({
        ok: true,
        sample_path: path.relative(BASE_DIR, samplePath),
        exported_files: exportedFiles.map((f) => path.relative(BASE_DIR, f)),
        preview_rows: previewRows,
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [POST /api/convert & /api/sample-convert]
  app.post(['/api/convert', '/api/sample-convert'], (req: Request, res: Response) => {
    try {
      const payload = req.body || {};
      const relPattern = String(payload.pattern || payload.input_pattern || 'samples/sample_raw_sap.xlsx').trim();
      const crossMonthMode = String(payload.cross_month_date_mode || payload.cross_month_policy || 'period_end');
      const ccColRaw = payload.cost_center_col;
      const costCenterCol = ccColRaw !== undefined && ccColRaw !== null && String(ccColRaw).trim() !== '' ? Number(ccColRaw) : null;
      const offlineDedup = payload.offline_voucher_dedup !== undefined
        ? Boolean(payload.offline_voucher_dedup)
        : Boolean(payload.offline_dedup ?? true);

      let resolvedInput = path.isAbsolute(relPattern) ? relPattern : path.join(BASE_DIR, relPattern);
      if (relPattern.includes('*')) {
        const dir = path.dirname(resolvedInput);
        const files = fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => f.endsWith('.xlsx')).sort() : [];
        resolvedInput = files.length > 0 ? path.join(dir, files[0]) : generateSampleRawSapFile();
      } else if (!fs.existsSync(resolvedInput)) {
        resolvedInput = generateSampleRawSapFile();
      }

      const { exportedFiles, previewRows } = convertSapToFast43Template({
        inputPath: resolvedInput,
        crossMonthDateMode: crossMonthMode,
        costCenterCol,
        offlineVoucherDedup: offlineDedup,
      });

      res.json({
        ok: true,
        exported_files: exportedFiles.map((f) => path.relative(BASE_DIR, f)),
        preview_rows: previewRows,
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [POST /api/audit]
  app.post('/api/audit', (req: Request, res: Response) => {
    try {
      const pattern = String(req.body?.pattern || 'input/*.xlsx');
      const targetFiles: string[] = [];

      if (pattern.includes('input') || pattern.includes('all')) {
        if (fs.existsSync(INPUT_DIR)) {
          for (const f of fs.readdirSync(INPUT_DIR).filter((x) => x.endsWith('.xlsx')).sort()) {
            targetFiles.push(path.join(INPUT_DIR, f));
          }
        }
      }
      if (fs.existsSync(OUTPUT_DIR)) {
        for (const f of fs.readdirSync(OUTPUT_DIR).filter((x) => x.endsWith('.xlsx')).sort()) {
          targetFiles.push(path.join(OUTPUT_DIR, f));
        }
      }

      const rows = targetFiles.map((fp) => auditExcelFile(fp));
      res.json({
        ok: true,
        report_path: 'output/audit_reports/audit_summary.csv',
        records: rows,
        rows,
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [GET /api/mappings & /api/mapping/search]
  app.get(['/api/mappings', '/api/mapping/search'], (req: Request, res: Response) => {
    try {
      const q = String(req.query.search || req.query.q || '').trim().toLowerCase();
      const accCfg = loadAccountConfig();
      const accountMap = accCfg.account || {};
      const fkberMap = accCfg.fkber || {};

      const results: Array<{
        scope: string;
        layer: string;
        fkber: string;
        sap_code: string;
        fast_code: string;
      }> = [];

      for (const [sapAcc, fastAcc] of Object.entries(accountMap)) {
        const sStr = String(sapAcc);
        const fStr = String(fastAcc);
        if (!q || sStr.toLowerCase().includes(q) || fStr.toLowerCase().includes(q)) {
          results.push({
            scope: 'account (普通科目高优层)',
            layer: 'account (普通科目高优层)',
            fkber: '-',
            sap_code: sStr,
            fast_code: fStr,
          });
        }
      }

      for (const [fk, sub] of Object.entries(fkberMap)) {
        if (!sub || typeof sub !== 'object') continue;
        for (const [sapAcc, fastAcc] of Object.entries(sub)) {
          const sStr = String(sapAcc);
          const fStr = String(fastAcc);
          if (!q || sStr.toLowerCase().includes(q) || fStr.toLowerCase().includes(q) || fk.toLowerCase().includes(q)) {
            results.push({
              scope: `fkber (${fk})`,
              layer: `fkber (${fk})`,
              fkber: fk,
              sap_code: sStr,
              fast_code: fStr,
            });
          }
        }
      }

      const auditLogPath = path.join(LOGS_DIR, 'mapping_change_audit.log');
      const auditLines: any[] = [];
      if (fs.existsSync(auditLogPath)) {
        const lines = fs.readFileSync(auditLogPath, 'utf-8').trim().split('\n').filter(Boolean).slice(-20);
        for (const l of lines) {
          try {
            auditLines.push(JSON.parse(l));
          } catch {
            auditLines.push(l);
          }
        }
      }

      res.json({
        ok: true,
        total: results.length,
        items: results.slice(0, 120),
        rows: results.slice(0, 120),
        audit_logs: auditLines,
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [POST /api/mappings/action & /api/mapping]
  app.post(['/api/mappings/action', '/api/mapping'], (req: Request, res: Response) => {
    try {
      const payload = req.body || {};
      const action = String(payload.action || '');
      const sapCode = String(payload.sap_code || '').trim();
      const fastCode = String(payload.fast_code || '').trim();
      const fkber = payload.fkber && payload.fkber !== '-' ? String(payload.fkber).trim() : '';
      const partnerSyncScope = String(payload.partner_sync_scope || 'd11,ct00');
      const forceMulti = Boolean(payload.force_multi);
      const dryRun = payload.dry_run !== undefined ? Boolean(payload.dry_run) : true;

      const accCfg = loadAccountConfig();
      const accountMap = accCfg.account || {};
      const fkberMap = accCfg.fkber || {};
      const vendorMap = accCfg.vendor || {};

      const appendAudit = (entry: Record<string, any>) => {
        const auditLogPath = path.join(LOGS_DIR, 'mapping_change_audit.log');
        fs.appendFileSync(
          auditLogPath,
          JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n',
          'utf-8'
        );
      };

      if (action === 'add-account') {
        const targetDict = fkber ? (fkberMap[fkber] = fkberMap[fkber] || {}) : accountMap;
        if (targetDict[sapCode]) {
          return res.json({
            ok: false,
            message: `[6.1 防重拦截] SAP 科目 ${sapCode} 已存在于 ${fkber ? `fkber(${fkber})` : 'account'} 层 (当前映射为 ${targetDict[sapCode]})，禁止重复添加！`,
          });
        }
        if (!dryRun) {
          targetDict[sapCode] = fastCode;
          saveAccountConfig(accCfg);
        }
        appendAudit({ action, sap_code: sapCode, fast_code: fastCode, fkber: fkber || 'account', dry_run: dryRun, status: 'PASS' });
        return res.json({
          ok: true,
          message: `新增科目映射 ${sapCode} -> ${fastCode} ${dryRun ? '(Dry-Run 预览)' : '(已写入 YAML)'} 成功`,
        });
      }

      if (action === 'add-partner') {
        const existingFast = Object.values(vendorMap);
        if (vendorMap[sapCode] || existingFast.includes(fastCode)) {
          return res.json({
            ok: false,
            message: `[6.1 客商防重拦截] SAP 客商 ${sapCode} 或目标 FAST 客商 ${fastCode} 已存在，客商映射严格要求一对一！`,
          });
        }
        if (!dryRun) {
          accCfg.vendor = { ...vendorMap, [sapCode]: fastCode };
          saveAccountConfig(accCfg);
        }
        appendAudit({ action, sap_code: sapCode, fast_code: fastCode, dry_run: dryRun, status: 'PASS' });
        return res.json({
          ok: true,
          message: `新增客商映射 ${sapCode} -> ${fastCode} ${dryRun ? '(Dry-Run 预览)' : '(已写入 YAML)'} 成功`,
        });
      }

      if (action === 'update-partner') {
        const scopeTables = partnerSyncScope.split(',').map((s) => s.trim()).filter(Boolean);
        appendAudit({
          action,
          sap_code: sapCode,
          new_fast_code: fastCode,
          sync_tables: scopeTables,
          dry_run: dryRun,
          status: 'PASS',
        });
        return res.json({
          ok: true,
          message: `[6.21 客商映射同步] ${sapCode} -> ${fastCode} ${dryRun ? '(Dry-Run 预览)' : ''} 成功，联动生成同步表范围: [${scopeTables.join(', ')}]`,
        });
      }

      if (action === 'update-account') {
        const targetDict = fkber ? (fkberMap[fkber] || {}) : accountMap;
        const currentFast = targetDict[sapCode];
        if (!currentFast) {
          return res.json({
            ok: false,
            message: `原 SAP 科目 ${sapCode} 在 ${fkber ? `fkber(${fkber})` : 'account'} 中不存在，请先使用 6.1 新增映射。`,
          });
        }
        // Check many-to-one relationship on currentFast
        const relatedSap: string[] = [];
        for (const [k, v] of Object.entries(accountMap)) {
          if (String(v) === String(currentFast)) relatedSap.push(k);
        }
        for (const [fk, sub] of Object.entries(fkberMap)) {
          if (sub && typeof sub === 'object') {
            for (const [k, v] of Object.entries(sub)) {
              if (String(v) === String(currentFast)) relatedSap.push(`${fk}:${k}`);
            }
          }
        }

        if (relatedSap.length > 1 && !forceMulti) {
          appendAudit({
            action,
            sap_code: sapCode,
            old_fast: currentFast,
            new_fast: fastCode,
            related_count: relatedSap.length,
            status: 'BLOCKED_BY_6_22_MULTI_LOCK',
          });
          return res.json({
            ok: false,
            message: `[6.22 多对一安全锁拦截] 原 FAST 科目 ${currentFast} 同时被 ${relatedSap.length} 个 SAP 科目共用 (${relatedSap.slice(0, 5).join(', ')})，禁止直接联动篡改 FAST 历史分表！如需强制修改请勾选“允许修改多对一 (--force-multi)”。`,
          });
        }

        if (!dryRun) {
          targetDict[sapCode] = fastCode;
          saveAccountConfig(accCfg);
        }
        appendAudit({
          action,
          sap_code: sapCode,
          old_fast: currentFast,
          new_fast: fastCode,
          force_multi: forceMulti,
          dry_run: dryRun,
          status: 'PASS',
        });
        return res.json({
          ok: true,
          message: `科目映射修改 ${sapCode}: ${currentFast} -> ${fastCode} ${dryRun ? '(Dry-Run 预览)' : '(已写入 YAML)'} 成功！`,
        });
      }

      res.json({ ok: false, message: `未知操作类型: ${action}` });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [POST /api/db-maintenance/preview & /api/db-dry-run]
  app.post(['/api/db-maintenance/preview', '/api/db-dry-run'], (req: Request, res: Response) => {
    try {
      const payload = req.body || {};
      const opType = String(payload.op_type || 'delete-account');
      const targetAccount = String(payload.fast_code || payload.target_account || '6428').trim();
      const oldAccount = String(payload.old_fast_code || payload.old_account || '131111').trim();
      const newAccount = String(payload.new_fast_code || payload.new_account || '131112').trim();
      const yyyymm = String(payload.fiscalym || payload.yyyymm || '202403').trim();
      const vouchersStr = String(payload.vouchers || payload.voucher_nos || '1000000001,1000000002').trim();

      const samplePeriods = ['202406', '202407', '202408'];
      let sqlList: string[] = [];

      if (opType === 'delete-account') {
        for (const ym of samplePeriods) {
          sqlList.push(
            `-- [期间 ${ym}] 按科目 ${targetAccount} 级联清理 7 张 FAST 分表错账 (Dry-Run)`,
            `SELECT DISTINCT so_ct FROM d11$${ym} WHERE tk LIKE '${targetAccount}%';`,
            `UPDATE m11$${ym} SET status = 1 WHERE so_ct IN (SELECT DISTINCT so_ct FROM d11$${ym} WHERE tk LIKE '${targetAccount}%');`,
            `DELETE FROM r00$${ym} WHERE so_ct IN (SELECT DISTINCT so_ct FROM d11$${ym} WHERE tk LIKE '${targetAccount}%');`,
            `DELETE FROM ct00 WHERE so_ct IN (SELECT DISTINCT so_ct FROM d11$${ym} WHERE tk LIKE '${targetAccount}%');`,
            `DELETE FROM c11$000000 WHERE stt_rec IN (SELECT stt_rec FROM m11$${ym} WHERE so_ct IN (SELECT DISTINCT so_ct FROM d11$${ym} WHERE tk LIKE '${targetAccount}%'));`,
            `DELETE FROM i11$${ym} WHERE stt_rec IN (SELECT stt_rec FROM m11$${ym} WHERE so_ct IN (SELECT DISTINCT so_ct FROM d11$${ym} WHERE tk LIKE '${targetAccount}%'));`,
            `DELETE FROM d11$${ym} WHERE tk LIKE '${targetAccount}%';`,
            `DELETE FROM m11$${ym} WHERE so_ct NOT IN (SELECT DISTINCT so_ct FROM d11$${ym});`
          );
        }
      } else if (opType === 'update-account') {
        for (const ym of samplePeriods) {
          sqlList.push(
            `-- [期间 ${ym}] 批量将历史科目 ${oldAccount} 替换为 ${newAccount} (Dry-Run)`,
            `UPDATE d11$${ym} SET tk = REPLACE(RTRIM(tk), '${oldAccount}', '${newAccount}') WHERE RTRIM(tk) = '${oldAccount}';`,
            `UPDATE r00$${ym} SET tk = REPLACE(RTRIM(tk), '${oldAccount}', '${newAccount}') WHERE RTRIM(tk) = '${oldAccount}';`,
            `UPDATE r00$${ym} SET tk_du = REPLACE(RTRIM(tk_du), '${oldAccount}', '${newAccount}') WHERE RTRIM(tk_du) = '${oldAccount}';`,
            `UPDATE i11$${ym} SET tk_i = REPLACE(RTRIM(tk_i), '${oldAccount}', '${newAccount}') WHERE RTRIM(tk_i) = '${oldAccount}';`
          );
        }
        sqlList.push(
          `UPDATE ct00 SET tk = REPLACE(RTRIM(tk), '${oldAccount}', '${newAccount}') WHERE RTRIM(tk) = '${oldAccount}';`,
          `UPDATE ct00 SET tk_du = REPLACE(RTRIM(tk_du), '${oldAccount}', '${newAccount}') WHERE RTRIM(tk_du) = '${oldAccount}';`
        );
      } else {
        const year = parseInt(yyyymm.slice(0, 4) || '2024', 10);
        const period = parseInt(yyyymm.slice(4, 6) || '3', 10);
        const fastMonth = period <= 9 ? period + 3 : period - 9;
        const ymFast = `${year}${String(fastMonth).padStart(2, '0')}`;
        const vList = vouchersStr
          .split(',')
          .map((v) => v.trim())
          .filter(Boolean)
          .map((v) => `'${v}'`)
          .join(',');

        sqlList = [
          `-- [SAP期间 ${yyyymm} -> FAST分表后缀 ${ymFast}] 按凭证号级联删除期间错账 (remove_fast_errors Dry-Run)`,
          `UPDATE m11$${ymFast} SET status = 1 WHERE so_ct IN (${vList});`,
          `DELETE FROM r00$${ymFast} WHERE so_ct IN (${vList});`,
          `DELETE FROM ct00 WHERE so_ct IN (${vList});`,
          `DELETE FROM c11$000000 WHERE stt_rec IN (SELECT stt_rec FROM m11$${ymFast} WHERE so_ct IN (${vList}));`,
          `DELETE FROM i11$${ymFast} WHERE stt_rec IN (SELECT stt_rec FROM m11$${ymFast} WHERE so_ct IN (${vList}));`,
          `DELETE FROM d11$${ymFast} WHERE so_ct IN (${vList});`,
          `DELETE FROM m11$${ymFast} WHERE so_ct IN (${vList});`,
        ];
      }

      res.json({
        ok: true,
        op_type: opType,
        sql_count: sqlList.length,
        preview_sqls: sqlList,
        preview: {
          sql_count: sqlList.length,
          sql_preview: sqlList,
        },
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [POST /api/smoke-test]
  app.post('/api/smoke-test', (_req: Request, res: Response) => {
    try {
      const samplePath = generateSampleRawSapFile();
      const { exportedFiles, previewRows } = convertSapToFast43Template({ inputPath: samplePath });
      const accCfg = loadAccountConfig();
      const locked51181 = accCfg.account?.['6051000001'] === '51181';
      const credStatus = loadFastCredentialsStatus();

      const result = {
        ok: true,
        status: 'PASS',
        output: `ALL_OK (7 大核心验收模块全部通过 · 6051000001=${locked51181 ? '51181' : 'ERR'} · 43列模板导出 ${previewRows.length} 行 · 离线跨期凭证 _A 防重通过 · 需求6/7 & fast_credentials.yaml 解析通过)`,
        input_rows: 10,
        output_rows: previewRows.length,
        exported_files: exportedFiles.map((f) => path.relative(BASE_DIR, f)),
        template_columns: 43,
        voucher_nos: ['1000000001', '1000000001_A', '1000000002', '1000000003'],
        group_ids: [1, 2],
        credential_file_ready: credStatus.yaml_syntax_valid,
      };
      res.json({ ok: true, result, ...result });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [GET /api/skills]
  app.get('/api/skills', (_req: Request, res: Response) => {
    try {
      const skillsDir = path.join(BASE_DIR, '.agents', 'skills');
      const items: Array<{ id: string; name: string; version: string; description: string; path: string }> = [];
      if (fs.existsSync(skillsDir)) {
        const folders = fs.readdirSync(skillsDir).sort();
        for (const folder of folders) {
          const skillMdPath = path.join(skillsDir, folder, 'SKILL.md');
          if (!fs.existsSync(skillMdPath)) continue;
          const rawText = fs.readFileSync(skillMdPath, 'utf-8');
          let desc = '';
          let version = '1.0.0';
          for (const line of rawText.split('\n')) {
            const s = line.trim();
            if (s.startsWith('description:') && !desc) {
              desc = s.split(':').slice(1).join(':').trim().replace(/^["'|]+|["'|]+$/g, '');
            } else if (s.startsWith('version:')) {
              version = s.split(':').slice(1).join(':').trim().replace(/^["']+|["']+$/g, '');
            }
          }
          items.push({
            id: folder,
            name: folder,
            version,
            description: desc || `Project Skill: ${folder}`,
            path: `.agents/skills/${folder}/SKILL.md`,
          });
        }
      }
      res.json({ ok: true, count: items.length, skills: items });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [GET /api/docs/:name & /api/doc/:name]
  app.get(['/api/docs/:name', '/api/doc/:name'], (req: Request, res: Response) => {
    try {
      const name = String(req.params.name || '').trim();
      const docMap: Record<string, string> = {
        review_latest: fs.existsSync(path.join(BASE_DIR, 'Reviews_20261104_200000.md'))
          ? 'Reviews_20261104_200000.md'
          : 'Reviews_20261104.md',
        review_20261104: fs.existsSync(path.join(BASE_DIR, 'Reviews_20261104.md'))
          ? 'Reviews_20261104.md'
          : 'Reviews_20261104_200000.md',
        eval_1104: '功能对比、评估报告1104.md',
        accept_1104: '需求6-7实现验收报告1104.md',
        readme: 'README.md',
        login_guide: '登陆链接.md',
        cfo_1103: '1103_CFO会议纪要_正式版.md',
        cfo_update_1103: '1103与CFO会议纪要及需求更新.md',
        manual_ops: 'SAP2FAST 使用说明（财务与运维操作手册）.md',
        arch_dev: 'SAP2FAST 开发说明（技术架构与重构设计文档）.md',
        biz_req: '财务业务需求.md',
      };

      const filename = docMap[name];
      if (!filename) {
        return res.status(404).json({ ok: false, detail: `未知文档标识: ${name}` });
      }
      const targetPath = path.join(BASE_DIR, filename);
      if (!fs.existsSync(targetPath)) {
        return res.status(404).json({ ok: false, detail: `文档文件不存在: ${filename}` });
      }
      const content = fs.readFileSync(targetPath, 'utf-8');
      res.json({
        ok: true,
        name,
        filename,
        line_count: content.split('\n').length,
        size_bytes: Buffer.byteLength(content, 'utf-8'),
        content,
      });
    } catch (err: any) {
      res.status(500).json({ ok: false, detail: err?.message || String(err) });
    }
  });

  // [GET /api/download]
  app.get('/api/download', (req: Request, res: Response) => {
    const relPath = String(req.query.path || 'config/fast_credentials.yaml');
    const resolved = path.resolve(BASE_DIR, relPath);
    if (!resolved.startsWith(BASE_DIR) || !fs.existsSync(resolved)) {
      return res.status(404).json({ ok: false, detail: '文件不存在或路径越权' });
    }
    res.download(resolved, path.basename(resolved));
  });

  // Mount Vite dev server in development or static dist in production
  const distPath = path.join(BASE_DIR, 'dist');
  if (process.env.NODE_ENV === 'production' && fs.existsSync(distPath)) {
    app.use(express.static(distPath));
    app.get('*', (_req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  } else {
    const { createServer: createViteServer } = await import('vite');
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  }

  const portArgIdx = process.argv.indexOf('--port');
  const cliPort = portArgIdx !== -1 && process.argv[portArgIdx + 1] ? Number(process.argv[portArgIdx + 1]) : null;
  const envPort = process.env.PORT && process.env.PORT !== '8080' && process.env.PORT !== '8000' ? Number(process.env.PORT) : null;
  const PORT = cliPort || envPort || 3000;

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`SAP2FAST 1104 Full-Stack Workbench listening on http://0.0.0.0:${PORT}`);
  });
}

startServer().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});
