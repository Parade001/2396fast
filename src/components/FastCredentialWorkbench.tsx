import React, { useState, useEffect } from 'react';
import {
  Eye,
  EyeOff,
  Download,
  Save,
  Play,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Copy,
  Terminal,
  RefreshCw,
  ShieldAlert,
  Lock,
} from 'lucide-react';

export interface FastCredentialStatus {
  ok: boolean;
  file_exists: boolean;
  credential_file: string;
  yaml_syntax_valid: boolean;
  yaml_syntax_error?: string;
  is_ready_for_auto_login: boolean;
  active_source: string;
  effective: {
    login_url: string;
    username: string;
    has_password: boolean;
    password_length: number;
    masked_password: string;
    fingerprint?: string;
    unit: string;
    language: string;
  };
  baseline_lock?: {
    locked: boolean;
    username: string;
    masked_password: string;
    password_length: number;
    fingerprint: string;
    locked_at: string;
    source: string;
  };
  priority_chain: Array<{
    rank: number;
    source: string;
    status: string;
    username: string;
    has_password: boolean;
    password_length?: number;
    masked_password?: string;
    fingerprint?: string;
  }>;
  config_data: {
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
    selectors: Record<string, string>;
  };
  raw_yaml: string;
  cli_verify_command: string;
}

interface VerifyStepResult {
  step: number;
  title: string;
  method: string;
  status: 'PASS' | 'WARN' | 'FAIL';
  detail: string;
  log_output: string;
}

interface VerifyPipelineResponse {
  ok: boolean;
  overall_status: 'PASS' | 'AUTH_FAILED' | 'ACTION_REQUIRED';
  credential_match?: boolean;
  tested_credentials?: {
    username: string;
    masked_password: string;
    password_length: number;
    fingerprint: string;
    unit: string;
    language: string;
  };
  baseline_credentials?: {
    username: string;
    masked_password: string;
    password_length: number;
    fingerprint: string;
    has_baseline: boolean;
  };
  intranet_live: boolean;
  verified_at: string;
  summary_message: string;
  steps: VerifyStepResult[];
}

interface Props {
  onGlobalBannerUpdate: (msg: string, type?: 'pass' | 'warn' | 'err') => void;
  onStatusRefresh?: () => void;
}

export const FastCredentialWorkbench: React.FC<Props> = ({
  onGlobalBannerUpdate,
  onStatusRefresh,
}) => {
  const [credStatus, setCredStatus] = useState<FastCredentialStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [showPlainPassword, setShowPlainPassword] = useState(false);
  const [copiedCmd, setCopiedCmd] = useState(false);
  const [forceUpdateBaseline, setForceUpdateBaseline] = useState(false);

  // Editable form state for Module 4
  const [loginUrl, setLoginUrl] = useState('http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [unit, setUnit] = useState('CTY');
  const [language, setLanguage] = useState('en');
  const [chromePath, setChromePath] = useState('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe');
  const [cdpPort, setCdpPort] = useState(9222);

  const [verifyResult, setVerifyResult] = useState<VerifyPipelineResponse | null>(null);
  const [feedbackMsg, setFeedbackMsg] = useState<{ text: string; type: 'pass' | 'warn' | 'err' } | null>(null);

  const fetchCredentialStatus = async (syncDisk = false) => {
    setLoading(true);
    try {
      const url = syncDisk ? '/api/fast-credentials?sync_disk=1' : '/api/fast-credentials';
      const res = await fetch(url);
      const data: FastCredentialStatus = await res.json();
      if (data.ok) {
        setCredStatus(data);
        const fa = data.config_data.fast_auth;
        const cd = data.config_data.chrome_cdp;
        setLoginUrl(fa.login_url || 'http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx');
        setUsername(fa.username || '');
        setPassword(fa.password || '');
        setUnit(fa.unit || 'CTY');
        setLanguage(fa.language || 'en');
        setChromePath(cd.chrome_path || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe');
        setCdpPort(cd.remote_debugging_port || 9222);
        if (syncDisk) {
          setFeedbackMsg({
            text: `已从本地磁盘重新加载 config/fast_credentials.yaml (当前基准账号: ${data.effective.username || '未配置'}, 指纹: #${data.effective.fingerprint || '空'})`,
            type: data.is_ready_for_auto_login ? 'pass' : 'warn',
          });
        }
      }
    } catch (err: any) {
      setFeedbackMsg({ text: `读取凭据配置失败: ${err?.message || String(err)}`, type: 'err' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCredentialStatus();
  }, []);

  const handleRunLoginVerify = async (overrideUser?: string, overridePass?: string) => {
    setVerifying(true);
    const targetUser = overrideUser !== undefined ? overrideUser : username;
    const targetPass = overridePass !== undefined ? overridePass : password;

    try {
      const res = await fetch('/api/fast-login-verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          test_username: targetUser,
          test_password: targetPass,
          test_unit: unit,
          test_language: language,
          test_login_url: loginUrl,
        }),
      });
      const data: VerifyPipelineResponse = await res.json();
      if (data.ok) {
        setVerifyResult(data);
        const bannerType: 'pass' | 'warn' | 'err' =
          data.overall_status === 'PASS'
            ? 'pass'
            : data.overall_status === 'AUTH_FAILED'
              ? 'err'
              : 'warn';
        setFeedbackMsg({
          text: data.summary_message,
          type: bannerType,
        });
        onGlobalBannerUpdate(`[FAST 自动登录验证] ${data.summary_message}`, bannerType);
      }
    } catch (err: any) {
      setFeedbackMsg({ text: `自动登录验证执行失败: ${err?.message || String(err)}`, type: 'err' });
    } finally {
      setVerifying(false);
    }
  };

  const handleSaveCredentials = async (
    overrideUser?: string,
    overridePass?: string,
    overrideForceBaseline?: boolean
  ) => {
    setSaving(true);
    const targetUser = overrideUser !== undefined ? overrideUser : username;
    const targetPass = overridePass !== undefined ? overridePass : password;
    const useForce = overrideForceBaseline !== undefined ? overrideForceBaseline : forceUpdateBaseline;

    try {
      const res = await fetch('/api/fast-credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          login_url: loginUrl,
          username: targetUser,
          password: targetPass,
          unit,
          language,
          chrome_path: chromePath,
          remote_debugging_port: Number(cdpPort),
          force_update_baseline: useForce,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setCredStatus(data);
        setForceUpdateBaseline(false);
        const isReady = Boolean(data.is_ready_for_auto_login);
        const msg = data.message || '已保存 config/fast_credentials.yaml';
        setFeedbackMsg({ text: msg, type: isReady ? 'pass' : 'warn' });
        onGlobalBannerUpdate(msg, isReady ? 'pass' : 'warn');
        onStatusRefresh?.();
        await handleRunLoginVerify(targetUser, targetPass);
      } else {
        // Blocked by baseline lock (e.g., user typed a wrong password in Module 4 and clicked Save without force_update_baseline)
        const errMsg = data.detail || '保存被基准账密保护锁拦截';
        setFeedbackMsg({ text: errMsg, type: 'err' });
        onGlobalBannerUpdate(errMsg, 'err');
        // Immediately run 6-step verify with the wrong password so Step 1 & Step 6 show red FAIL
        await handleRunLoginVerify(targetUser, targetPass);
      }
    } catch (err: any) {
      setFeedbackMsg({ text: `保存异常: ${err?.message || String(err)}`, type: 'err' });
    } finally {
      setSaving(false);
    }
  };

  const handleQuickFillDemoPass = async () => {
    const demoUser = 'FIN_ADMIN_VN';
    const demoPass = 'Fast@2026#Timber';
    setUsername(demoUser);
    setPassword(demoPass);
    setUnit('CTY');
    setLanguage('en');
    await handleSaveCredentials(demoUser, demoPass, true);
  };

  const handleSimulateWrongPasswordTest = async () => {
    // Ensure a baseline exists first; if not, seed the baseline, then test with a wrong password
    const hasBase = Boolean(credStatus?.baseline_lock?.locked && credStatus?.baseline_lock?.fingerprint);
    const baseUser = hasBase ? credStatus!.baseline_lock!.username : 'FIN_ADMIN_VN';
    if (!hasBase) {
      await fetch('/api/fast-credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          login_url: loginUrl,
          username: 'FIN_ADMIN_VN',
          password: 'Fast@2026#Timber',
          unit: 'CTY',
          language: 'en',
          chrome_path: chromePath,
          remote_debugging_port: Number(cdpPort),
          force_update_baseline: true,
        }),
      });
      await fetchCredentialStatus();
    }
    const wrongPass = 'WrongPass_999999!';
    setUsername(baseUser);
    setPassword(wrongPass);
    await handleRunLoginVerify(baseUser, wrongPass);
  };

  const handleDownloadYaml = () => {
    const yamlContent = credStatus?.raw_yaml || '';
    if (!yamlContent) {
      window.location.href = '/api/download?path=config/fast_credentials.yaml';
      return;
    }
    const blob = new Blob([yamlContent], { type: 'text/yaml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'fast_credentials.yaml';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleCopyCliCmd = () => {
    if (!credStatus?.cli_verify_command) return;
    navigator.clipboard.writeText(credStatus.cli_verify_command);
    setCopiedCmd(true);
    setTimeout(() => setCopiedCmd(false), 2000);
  };

  const baselineInfo = credStatus?.baseline_lock;

  return (
    <div className="card" style={{ borderColor: '#cbd5e1' }}>
      <div className="card-header">
        <div>
          <h2 className="card-title">
            方案 A 专享：config/fast_credentials.yaml 基准指纹校验器与 FAST 自动登录 6 步真实验证台
          </h2>
          <p className="card-desc">
            将模块 4 输入框中的待测账密与本地磁盘 config/fast_credentials.yaml 基准指纹 (SHA-256) 严格比对：输入错误密码点击验证或未授权保存均会精准拦截并报错 (AUTH_FAILED)
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => fetchCredentialStatus(true)}
            disabled={loading || saving || verifying}
            title="当您在本地编辑器修改了 config/fast_credentials.yaml 后，点击此按钮重新读取磁盘基准"
          >
            <RefreshCw size={13} />
            从本地 YAML 重新加载基准
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleQuickFillDemoPass}
            disabled={saving || verifying}
          >
            1. 写入正确基准账密并验证 (PASS)
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={handleSimulateWrongPasswordTest}
            disabled={saving || verifying}
          >
            <ShieldAlert size={13} />
            2. 故意输入错误密码测试拦截 (FAIL)
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => handleRunLoginVerify()}
            disabled={verifying}
          >
            <Play size={14} />
            {verifying ? '正在核验输入账密...' : '校验当前输入账密 (6步登录测试)'}
          </button>
        </div>
      </div>

      {/* Top status summary banner */}
      {feedbackMsg ? (
        <div className={`status-banner ${feedbackMsg.type}`}>{feedbackMsg.text}</div>
      ) : credStatus ? (
        <div className={`status-banner ${credStatus.is_ready_for_auto_login ? 'pass' : 'warn'}`}>
          {credStatus.is_ready_for_auto_login
            ? `[本地 YAML 基准已锁定 PASS] 来源: ${credStatus.active_source} · 基准账号: ${credStatus.effective.username} · 密码掩码: ${credStatus.effective.masked_password} (${credStatus.effective.password_length}位) · SHA-256 指纹: #${credStatus.effective.fingerprint}`
            : `[待填写基准账号密码] 本地 config/fast_credentials.yaml 当前账号或密码为空。请先在本地 YAML 文件填写，或在下方勾选写入基准账密。`}
        </div>
      ) : null}

      <div className="grid-2" style={{ marginBottom: '16px' }}>
        {/* Left column: Interactive Credential Input & Baseline Lock Control */}
        <div style={{ border: '1px solid #e2e8f0', borderRadius: '6px', padding: '14px 16px', background: '#f8fafc' }}>
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#0f172a' }}>
                模块 4 待测账密输入 &amp; config/fast_credentials.yaml 编辑器
              </h3>
              <p style={{ fontSize: '12px', color: '#475569' }}>
                在此输入任意账号/密码并点击「校验当前输入账密」，系统将与本地 YAML 正确基准做严格比对
              </p>
            </div>
            <span
              style={{
                fontSize: '12px',
                fontFamily: 'var(--font-mono)',
                color: baselineInfo?.locked ? '#166534' : '#b45309',
                fontWeight: 700,
              }}
            >
              {baselineInfo?.locked
                ? `基准锁已启用 (#${baselineInfo.fingerprint})`
                : '尚未建立基准锁'}
            </span>
          </div>

          {/* Baseline Lock Status Strip */}
          <div
            style={{
              background: baselineInfo?.locked ? '#eff6ff' : '#fffbeb',
              border: `1px solid ${baselineInfo?.locked ? '#bfdbfe' : '#fde68a'}`,
              borderRadius: '4px',
              padding: '8px 10px',
              marginBottom: '12px',
              fontSize: '12px',
              color: '#1e293b',
            }}
          >
            <div className="flex items-center justify-between flex-wrap gap-1">
              <span className="inline-flex items-center gap-1 font-semibold">
                <Lock size={13} />
                本地 config/fast_credentials.yaml 权威基准账密：
              </span>
              {baselineInfo?.locked ? (
                <span className="mono" style={{ color: '#0f4c81', fontWeight: 700 }}>
                  账号={baselineInfo.username} | 掩码={baselineInfo.masked_password} ({baselineInfo.password_length}位) | 指纹=#{baselineInfo.fingerprint}
                </span>
              ) : (
                <span style={{ color: '#b45309' }}>
                  当前磁盘 YAML 尚未配置密码（点击上方「1. 写入正确基准账密」或在本地编辑 YAML 后点击重新加载）
                </span>
              )}
            </div>
          </div>

          <div className="form-row">
            <div className="field" style={{ flex: '1 1 200px' }}>
              <label htmlFor="fc-username">待测 / 配置 FAST 登录账号 (username)</label>
              <input
                id="fc-username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="如 ADMIN 或 FIN_ADMIN_VN"
              />
            </div>

            <div className="field" style={{ flex: '1 1 220px' }}>
              <label htmlFor="fc-password">
                待测 / 配置 FAST 登录密码 (password)
              </label>
              <div className="flex items-center gap-1">
                <input
                  id="fc-password"
                  type={showPlainPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="故意输入错误密码可测试拦截"
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowPlainPassword((prev) => !prev)}
                  title={showPlainPassword ? '切换为掩码显示' : '查看明文密码'}
                  style={{ padding: '0 10px' }}
                >
                  {showPlainPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  {showPlainPassword ? '掩码' : '显示'}
                </button>
              </div>
            </div>
          </div>

          <div className="form-row">
            <div className="field" style={{ flex: '1 1 120px', minWidth: '120px' }}>
              <label htmlFor="fc-unit">核算单位 (unit)</label>
              <input
                id="fc-unit"
                type="text"
                value={unit}
                onChange={(e) => setUnit(e.target.value)}
              />
            </div>

            <div className="field" style={{ flex: '1 1 160px', minWidth: '160px' }}>
              <label htmlFor="fc-lang">登录界面语言 (language)</label>
              <select
                id="fc-lang"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              >
                <option value="en">en (自动切为英文界面 - 推荐)</option>
                <option value="vi">vi (保持默认越南语界面)</option>
              </select>
            </div>

            <div className="field" style={{ flex: '1 1 130px', minWidth: '130px' }}>
              <label htmlFor="fc-port">CDP 调试端口</label>
              <input
                id="fc-port"
                type="number"
                value={cdpPort}
                onChange={(e) => setCdpPort(Number(e.target.value || 9222))}
              />
            </div>
          </div>

          <div className="form-row">
            <div className="field" style={{ flex: '1 1 100%' }}>
              <label htmlFor="fc-url">越南 FAST 登录地址 (fast_auth.login_url)</label>
              <input
                id="fc-url"
                type="text"
                value={loginUrl}
                onChange={(e) => setLoginUrl(e.target.value)}
              />
            </div>
          </div>

          {/* Baseline protection lock checkbox */}
          <div style={{ marginBottom: '12px' }}>
            <label
              className="check-inline"
              style={{
                fontSize: '12px',
                color: forceUpdateBaseline ? '#991b1b' : '#334155',
                fontWeight: forceUpdateBaseline ? 700 : 500,
              }}
            >
              <input
                type="checkbox"
                checked={forceUpdateBaseline}
                onChange={(e) => setForceUpdateBaseline(e.target.checked)}
              />
              允许覆写本地 YAML 基准账密锁 (--force-update-baseline)：仅当您确实需要修改真实正确密码时勾选；未勾选时若输入错误密码点保存将被自动拦截
            </label>
          </div>

          <div className="form-row" style={{ marginBottom: 0 }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => handleRunLoginVerify()}
              disabled={verifying}
            >
              <Play size={14} />
              {verifying ? '校验中...' : '仅测试当前输入账密 (不改 YAML)'}
            </button>

            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => handleSaveCredentials()}
              disabled={saving}
            >
              <Save size={14} />
              {saving ? '保存中...' : '保存至 fast_credentials.yaml'}
            </button>

            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleDownloadYaml}
            >
              <Download size={14} />
              下载 YAML
            </button>
          </div>
        </div>

        {/* Right column: Multi-Tier Priority & Fingerprint Comparison Table */}
        <div style={{ border: '1px solid #e2e8f0', borderRadius: '6px', padding: '14px 16px', background: '#ffffff' }}>
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#0f172a' }}>
                多级凭据加载优先级与 SHA-256 指纹核验表 (load_fast_credentials)
              </h3>
              <p style={{ fontSize: '12px', color: '#475569' }}>
                优先级顺序：CLI 参数 &gt; .env 环境变量 &gt; config/fast_credentials.yaml &gt; config/account.yaml
              </p>
            </div>
          </div>

          <div className="table-wrap" style={{ marginBottom: '12px' }}>
            <table aria-label="多级凭据优先级诊断表">
              <thead>
                <tr>
                  <th>优先级</th>
                  <th>配置来源</th>
                  <th>解析账号</th>
                  <th>密码掩码与指纹</th>
                  <th>当前状态</th>
                </tr>
              </thead>
              <tbody>
                {(credStatus?.priority_chain || []).map((item) => (
                  <tr key={item.rank}>
                    <td className="mono">P{item.rank}</td>
                    <td>{item.source}</td>
                    <td className="mono">{item.username}</td>
                    <td className="mono">
                      {item.has_password
                        ? `${item.masked_password || '••••••'} (${item.password_length || 6}位)${item.fingerprint ? ` · #${item.fingerprint}` : ''}`
                        : '未设置'}
                    </td>
                    <td>
                      <span
                        className={
                          item.status.includes('PASS') || item.status.includes('已配置')
                            ? 'status-text-pass'
                            : 'text-slate-500'
                        }
                      >
                        {item.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Live Comparison Card when Verify has been run */}
          {verifyResult?.tested_credentials && verifyResult?.baseline_credentials && (
            <div
              style={{
                border: `1px solid ${verifyResult.credential_match ? '#86efac' : '#fca5a5'}`,
                background: verifyResult.credential_match ? '#f0fdf4' : '#fef2f2',
                borderRadius: '4px',
                padding: '9px 12px',
                marginBottom: '12px',
                fontSize: '12px',
              }}
            >
              <div style={{ fontWeight: 700, color: verifyResult.credential_match ? '#166534' : '#991b1b', marginBottom: '4px' }}>
                {verifyResult.credential_match
                  ? '✅ 待测账密 vs 本地 YAML 基准账密比对结果：100% 匹配 (PASS)'
                  : '❌ 待测账密 vs 本地 YAML 基准账密比对结果：密码或账号不匹配 (AUTH_FAILED — 已拦截)'}
              </div>
              <div className="mono" style={{ color: '#1e293b', fontSize: '11.5px' }}>
                <div>
                  • 本地 YAML 正确基准：账号=&quot;{verifyResult.baseline_credentials.username || '空'}&quot; | 掩码={verifyResult.baseline_credentials.masked_password || '空'} ({verifyResult.baseline_credentials.password_length}位) | 指纹=#{verifyResult.baseline_credentials.fingerprint || '空'}
                </div>
                <div>
                  • 模块 4 当前输入待测：账号=&quot;{verifyResult.tested_credentials.username || '空'}&quot; | 掩码={verifyResult.tested_credentials.masked_password || '空'} ({verifyResult.tested_credentials.password_length}位) | 指纹=#{verifyResult.tested_credentials.fingerprint || '空'}
                </div>
              </div>
            </div>
          )}

          {/* Local terminal 1-liner verification command */}
          <div style={{ background: '#0f172a', color: '#e2e8f0', padding: '10px 12px', borderRadius: '4px' }}>
            <div className="flex items-center justify-between mb-1">
              <span className="flex items-center gap-1.5 text-xs text-slate-300 font-semibold">
                <Terminal size={13} />
                本地终端一行命令单独校验 config/fast_credentials.yaml 是否生效：
              </span>
              <button
                type="button"
                onClick={handleCopyCliCmd}
                style={{
                  background: '#334155',
                  color: '#f8fafc',
                  border: 'none',
                  borderRadius: '3px',
                  padding: '2px 8px',
                  fontSize: '11px',
                  cursor: 'pointer',
                }}
              >
                <span className="inline-flex items-center gap-1">
                  <Copy size={11} />
                  {copiedCmd ? '已复制命令' : '复制验证命令'}
                </span>
              </button>
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11.5px', color: '#93c5fd', wordBreak: 'break-all' }}>
              {credStatus?.cli_verify_command ||
                `python -c "from utils.login_handler import load_fast_credentials; print(load_fast_credentials())"`}
            </div>
          </div>
        </div>
      </div>

      {/* 6-Step FAST Auto-Login Verification Pipeline Results */}
      <div style={{ border: '1px solid #cbd5e1', borderRadius: '6px', padding: '14px 16px', background: '#ffffff' }}>
        <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
          <div>
            <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#0f172a' }}>
              FAST 网页自动登录 6 步全链路核验流水线 (LoginHandler.login &amp; is_logged_in)
            </h3>
            <p style={{ fontSize: '12px', color: '#475569' }}>
              只有当模块 4 输入的账密与本地 config/fast_credentials.yaml 正确基准指纹完全一致（或内网实连鉴权通过）时，Step 1 与 Step 6 才会通过 (PASS)
            </p>
          </div>
          {verifyResult && (
            <span
              className={
                verifyResult.overall_status === 'PASS'
                  ? 'status-text-pass'
                  : verifyResult.overall_status === 'AUTH_FAILED'
                    ? 'status-text-fail'
                    : 'status-text-warn'
              }
              style={{ fontSize: '13px', fontWeight: 700 }}
            >
              验证时间: {verifyResult.verified_at} · 综合结论: {verifyResult.overall_status}
            </span>
          )}
        </div>

        {!verifyResult ? (
          <div className="sql-box">
            点击右上角「校验当前输入账密 (6步登录测试)」或「2. 故意输入错误密码测试拦截 (FAIL)」，查看真实密码比对与 6 步自动登录核验结果。
          </div>
        ) : (
          <div className="table-wrap">
            <table aria-label="FAST 自动登录 6 步验证结果表">
              <thead>
                <tr>
                  <th>步骤</th>
                  <th>校验环节</th>
                  <th>调用方法 / 选择器</th>
                  <th>核验状态</th>
                  <th>详细验证说明与控制台日志输出</th>
                </tr>
              </thead>
              <tbody>
                {verifyResult.steps.map((s) => (
                  <tr key={s.step}>
                    <td className="mono">Step {s.step}</td>
                    <td style={{ fontWeight: 600 }}>{s.title}</td>
                    <td className="mono" style={{ color: '#0f4c81' }}>{s.method}</td>
                    <td>
                      <span
                        className={`inline-flex items-center gap-1 ${
                          s.status === 'PASS'
                            ? 'status-text-pass'
                            : s.status === 'WARN'
                              ? 'status-text-warn'
                              : 'status-text-fail'
                        }`}
                      >
                        {s.status === 'PASS' ? (
                          <CheckCircle2 size={14} />
                        ) : s.status === 'WARN' ? (
                          <AlertTriangle size={14} />
                        ) : (
                          <XCircle size={14} />
                        )}
                        {s.status}
                      </span>
                    </td>
                    <td style={{ whiteSpace: 'normal', minWidth: '340px' }}>
                      <div style={{ fontSize: '12.5px', color: '#1e293b', marginBottom: '4px' }}>
                        {s.detail}
                      </div>
                      <div
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '11.5px',
                          background: '#0f172a',
                          color: s.status === 'FAIL' ? '#fca5a5' : '#a7f3d0',
                          padding: '4px 8px',
                          borderRadius: '4px',
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {s.log_output}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
