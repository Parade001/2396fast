import React, { useState, useEffect } from 'react';
import { Eye, EyeOff, Download, Save, Play, CheckCircle2, AlertTriangle, XCircle, Copy, Terminal } from 'lucide-react';

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
    unit: string;
    language: string;
  };
  priority_chain: Array<{
    rank: number;
    source: string;
    status: string;
    username: string;
    has_password: boolean;
    password_length?: number;
    masked_password?: string;
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
  overall_status: 'PASS' | 'ACTION_REQUIRED';
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

  // Editable form state for config/fast_credentials.yaml
  const [loginUrl, setLoginUrl] = useState('http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [unit, setUnit] = useState('CTY');
  const [language, setLanguage] = useState('en');
  const [chromePath, setChromePath] = useState('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe');
  const [cdpPort, setCdpPort] = useState(9222);

  const [verifyResult, setVerifyResult] = useState<VerifyPipelineResponse | null>(null);
  const [feedbackMsg, setFeedbackMsg] = useState<{ text: string; type: 'pass' | 'warn' | 'err' } | null>(null);

  const fetchCredentialStatus = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/fast-credentials');
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

  const handleSaveCredentials = async (overrideUser?: string, overridePass?: string) => {
    setSaving(true);
    const targetUser = overrideUser !== undefined ? overrideUser : username;
    const targetPass = overridePass !== undefined ? overridePass : password;

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
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setCredStatus(data);
        const isReady = Boolean(data.is_ready_for_auto_login);
        const msg = data.message || '已保存 config/fast_credentials.yaml';
        setFeedbackMsg({ text: msg, type: isReady ? 'pass' : 'warn' });
        onGlobalBannerUpdate(msg, isReady ? 'pass' : 'warn');
        onStatusRefresh?.();
        // Automatically run 6-step verification right after saving
        await handleRunLoginVerify();
      } else {
        setFeedbackMsg({ text: data.detail || '保存失败', type: 'err' });
      }
    } catch (err: any) {
      setFeedbackMsg({ text: `保存异常: ${err?.message || String(err)}`, type: 'err' });
    } finally {
      setSaving(false);
    }
  };

  const handleQuickFillDemo = async () => {
    const demoUser = 'FIN_ADMIN_VN';
    const demoPass = 'Fast@2026#Timber';
    setUsername(demoUser);
    setPassword(demoPass);
    setUnit('CTY');
    setLanguage('en');
    await handleSaveCredentials(demoUser, demoPass);
  };

  const handleRunLoginVerify = async () => {
    setVerifying(true);
    try {
      const res = await fetch('/api/fast-login-verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ simulate_intranet: true }),
      });
      const data: VerifyPipelineResponse = await res.json();
      if (data.ok) {
        setVerifyResult(data);
        const isPass = data.overall_status === 'PASS';
        setFeedbackMsg({
          text: data.summary_message,
          type: isPass ? 'pass' : 'warn',
        });
        onGlobalBannerUpdate(
          `[FAST 自动登录验证] ${data.summary_message}`,
          isPass ? 'pass' : 'warn'
        );
      }
    } catch (err: any) {
      setFeedbackMsg({ text: `自动登录验证执行失败: ${err?.message || String(err)}`, type: 'err' });
    } finally {
      setVerifying(false);
    }
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

  return (
    <div className="card" style={{ borderColor: '#cbd5e1' }}>
      <div className="card-header">
        <div>
          <h2 className="card-title">
            方案 A 专享：config/fast_credentials.yaml 交互式凭据管理与 FAST 自动登录 6 步验证台
          </h2>
          <p className="card-desc">
            支持在线编辑并保存至 config/fast_credentials.yaml、导出最新 YAML 配置文件、密码掩码脱敏与语法核验，以及一键检验 LoginHandler 自动填表登录全链路
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleQuickFillDemo}
            disabled={saving || verifying}
          >
            一键填入测试账密并验证 (FIN_ADMIN_VN)
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleRunLoginVerify}
            disabled={verifying}
          >
            <Play size={14} />
            {verifying ? '正在执行 6 步验证...' : '运行 FAST 自动登录 6 步验证'}
          </button>
        </div>
      </div>

      {/* Top status summary banner */}
      {feedbackMsg ? (
        <div className={`status-banner ${feedbackMsg.type}`}>{feedbackMsg.text}</div>
      ) : credStatus ? (
        <div className={`status-banner ${credStatus.is_ready_for_auto_login ? 'pass' : 'warn'}`}>
          {credStatus.is_ready_for_auto_login
            ? `[凭据校验已就绪 PASS] 当前生效来源: ${credStatus.active_source} · 账号: ${credStatus.effective.username} · 密码掩码: ${credStatus.effective.masked_password} (长度 ${credStatus.effective.password_length} 位) · 单位: ${credStatus.effective.unit} · 语言: ${credStatus.effective.language}`
            : `[待填写账号密码] config/fast_credentials.yaml 语法校验通过，但当前 username 或 password 为空。请在下方填入您的 FAST 账号密码并点击“保存并校验凭据配置”或点击右上角“一键填入测试账密并验证”。`}
        </div>
      ) : null}

      <div className="grid-2" style={{ marginBottom: '16px' }}>
        {/* Left column: Online YAML Editor */}
        <div style={{ border: '1px solid #e2e8f0', borderRadius: '6px', padding: '14px 16px', background: '#f8fafc' }}>
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#0f172a' }}>
                在线编辑 config/fast_credentials.yaml
              </h3>
              <p style={{ fontSize: '12px', color: '#475569' }}>
                修改后点击保存将直接写入磁盘文件，或点击下载导出 YAML
              </p>
            </div>
            <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', color: credStatus?.yaml_syntax_valid ? '#166534' : '#991b1b', fontWeight: 700 }}>
              {credStatus?.yaml_syntax_valid ? 'YAML 语法合法 · PASS' : 'YAML 语法错误'}
            </span>
          </div>

          <div className="form-row">
            <div className="field" style={{ flex: '1 1 200px' }}>
              <label htmlFor="fc-username">FAST 登录账号 (fast_auth.username)</label>
              <input
                id="fc-username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="如 ADMIN 或 FIN_USER01"
              />
            </div>

            <div className="field" style={{ flex: '1 1 220px' }}>
              <label htmlFor="fc-password">
                FAST 登录密码 (fast_auth.password)
              </label>
              <div className="flex items-center gap-1">
                <input
                  id="fc-password"
                  type={showPlainPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入越南 FAST 登录密码"
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

          <div className="form-row" style={{ marginBottom: 0 }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => handleSaveCredentials()}
              disabled={saving}
            >
              <Save size={14} />
              {saving ? '保存中...' : '保存并校验 config/fast_credentials.yaml'}
            </button>

            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleDownloadYaml}
            >
              <Download size={14} />
              下载 fast_credentials.yaml
            </button>
          </div>
        </div>

        {/* Right column: Multi-Tier Priority & Effective Masked State */}
        <div style={{ border: '1px solid #e2e8f0', borderRadius: '6px', padding: '14px 16px', background: '#ffffff' }}>
          <div className="flex items-center justify-between mb-3">
            <div>
              <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#0f172a' }}>
                多级凭据加载优先级与掩码状态核验表 (load_fast_credentials)
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
                  <th>密码掩码状态</th>
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
                        ? `${item.masked_password || '••••••'} (${item.password_length || 6}位)`
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
              点击上方“运行 FAST 自动登录 6 步验证”或保存凭据后，系统将逐项验证凭据读取、语言切换、DOM 表单注入与登录成功三重断言
            </p>
          </div>
          {verifyResult && (
            <span
              className={
                verifyResult.overall_status === 'PASS' ? 'status-text-pass' : 'status-text-warn'
              }
              style={{ fontSize: '13px' }}
            >
              验证时间: {verifyResult.verified_at} · 综合结论: {verifyResult.overall_status}
            </span>
          )}
        </div>

        {!verifyResult ? (
          <div className="sql-box">
            点击右上角「运行 FAST 自动登录 6 步验证」或「一键填入测试账密并验证 (FIN_ADMIN_VN)」以查看完整 6 步验证流水线与日志回显。
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
                          color: '#a7f3d0',
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
