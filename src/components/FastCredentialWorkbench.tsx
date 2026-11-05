import React, { useState, useEffect } from 'react';

export interface PriorityChainItem {
  rank: number;
  source: string;
  status: string;
  username: string;
  has_password: boolean;
  password_length?: number;
  masked_password?: string;
  fingerprint?: string;
}

export interface FastCredentialsResponse {
  ok: boolean;
  file_exists: boolean;
  credential_file: string;
  yaml_syntax_valid: boolean;
  yaml_syntax_error: string;
  is_ready_for_auto_login: boolean;
  active_source: string;
  effective: {
    login_url: string;
    username: string;
    has_password: boolean;
    password_length: number;
    masked_password: string;
    fingerprint: string;
    unit: string;
    language: string;
  };
  priority_chain: PriorityChainItem[];
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
  };
  raw_yaml: string;
  cli_verify_command: string;
  message?: string;
}

export interface VerifyStepItem {
  step: number;
  title: string;
  method: string;
  status: 'PASS' | 'WARN' | 'FAIL';
  detail: string;
  log_output: string;
}

export interface CapturedErrorInfo {
  error_type: string;
  error_stage: string;
  target_url: string;
  elapsed_ms: number;
  error_message: string;
  suggestion: string;
}

export interface FastLoginVerifyResponse {
  ok: boolean;
  overall_status: 'PASS' | 'AUTH_FAILED' | 'URL_UNREACHABLE' | 'CREDENTIALS_MISSING' | string;
  intranet_live: boolean;
  target_url: string;
  verified_at: string;
  tested_credentials?: {
    username: string;
    masked_password: string;
    password_length: number;
    fingerprint: string;
    unit: string;
    language: string;
    login_url: string;
  };
  captured_error?: CapturedErrorInfo | null;
  steps: VerifyStepItem[];
  summary_message: string;
}

interface FastCredentialWorkbenchProps {
  onStatusRefresh?: () => Promise<void> | void;
  onGlobalBannerUpdate?: (text: string, type?: 'err' | 'warn' | 'pass') => void;
}

export const FastCredentialWorkbench: React.FC<FastCredentialWorkbenchProps> = ({
  onStatusRefresh,
  onGlobalBannerUpdate,
}) => {
  const [credStatus, setCredStatus] = useState<FastCredentialsResponse | null>(null);
  const [verifyResult, setVerifyResult] = useState<FastLoginVerifyResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [showPlainPassword, setShowPlainPassword] = useState(false);
  const [feedbackMsg, setFeedbackMsg] = useState<{ type: 'ok' | 'err' | 'warn'; text: string } | null>(null);

  // Editable form fields
  const [loginUrl, setLoginUrl] = useState('http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [unit, setUnit] = useState('CTY');
  const [language, setLanguage] = useState('en');
  const [chromePath, setChromePath] = useState('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe');
  const [cdpPort, setCdpPort] = useState(9222);
  const [timeoutSec, setTimeoutSec] = useState(3.5);

  const fetchCredentials = async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/fast-credentials');
      const data: FastCredentialsResponse = await res.json();
      setCredStatus(data);
      if (data.config_data) {
        setLoginUrl(data.config_data.fast_auth.login_url || 'http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx');
        setUsername(data.config_data.fast_auth.username || '');
        setPassword(data.config_data.fast_auth.password || '');
        setUnit(data.config_data.fast_auth.unit || 'CTY');
        setLanguage(data.config_data.fast_auth.language || 'en');
        setChromePath(data.config_data.chrome_cdp.chrome_path || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe');
        setCdpPort(data.config_data.chrome_cdp.remote_debugging_port || 9222);
      }
      return data;
    } catch (err: any) {
      setFeedbackMsg({ type: 'err', text: `读取凭据配置失败: ${err?.message || String(err)}` });
      return null;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCredentials();
  }, []);

  const handleRunLoginVerify = async (
    overrideUser?: string,
    overridePass?: string,
    overrideUrl?: string
  ) => {
    setVerifying(true);
    const targetUser = overrideUser !== undefined ? overrideUser : username;
    const targetPass = overridePass !== undefined ? overridePass : password;
    const targetUrl = overrideUrl !== undefined ? overrideUrl : loginUrl;

    try {
      const res = await fetch('/api/fast-login-verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          test_username: targetUser,
          test_password: targetPass,
          test_unit: unit,
          test_language: language,
          test_login_url: targetUrl,
          timeout_sec: timeoutSec,
        }),
      });
      const data: FastLoginVerifyResponse = await res.json();
      setVerifyResult(data);
      if (data.overall_status === 'PASS') {
        setFeedbackMsg({ type: 'ok', text: data.summary_message });
        onGlobalBannerUpdate?.(data.summary_message, 'pass');
      } else if (data.overall_status === 'URL_UNREACHABLE' || data.overall_status === 'AUTH_FAILED') {
        setFeedbackMsg({ type: 'err', text: data.summary_message });
        onGlobalBannerUpdate?.(data.summary_message, 'err');
      } else {
        setFeedbackMsg({ type: 'warn', text: data.summary_message });
        onGlobalBannerUpdate?.(data.summary_message, 'warn');
      }
    } catch (err: any) {
      setFeedbackMsg({ type: 'err', text: `执行真实浏览器登录验证失败: ${err?.message || String(err)}` });
    } finally {
      setVerifying(false);
    }
  };

  const handleSaveAndTestCredentials = async (
    overrideUser?: string,
    overridePass?: string,
    overrideUrl?: string
  ) => {
    setSaving(true);
    setFeedbackMsg(null);
    const targetUser = overrideUser !== undefined ? overrideUser : username;
    const targetPass = overridePass !== undefined ? overridePass : password;
    const targetUrl = overrideUrl !== undefined ? overrideUrl : loginUrl;

    try {
      const res = await fetch('/api/fast-credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          login_url: targetUrl,
          username: targetUser,
          password: targetPass,
          unit,
          language,
          chrome_path: chromePath,
          remote_debugging_port: Number(cdpPort) || 9222,
        }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        setFeedbackMsg({ type: 'err', text: data.detail || '保存失败' });
      } else {
        setCredStatus(data);
        if (onStatusRefresh) onStatusRefresh();
        // 保存至 YAML 后，立即真实访问目标 URL 测试该账密是否有效
        await handleRunLoginVerify(targetUser, targetPass, targetUrl);
      }
    } catch (err: any) {
      setFeedbackMsg({ type: 'err', text: `请求异常: ${err?.message || String(err)}` });
    } finally {
      setSaving(false);
    }
  };

  // 场景 1：真实访问 10.7.3.230 内网地址（若未连内网则捕获真实访问超时/无法打开错误；若在越南内网则向 10.7.3.230 实测账密）
  const handleTestRealIntranet1073230 = async () => {
    const realUrl = 'http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx';
    const u = username || 'FIN_ADMIN_VN';
    const p = password || 'Fast@2026#Timber';
    setLoginUrl(realUrl);
    setUsername(u);
    setPassword(p);
    await handleRunLoginVerify(u, p, realUrl);
  };

  // 场景 2：当 YAML 中填写了错误或过期密码时，真实访问 FAST Login.aspx 服务端并捕获服务端返回的错误提示
  const handleTestWrongOrExpiredPasswordOnServer = async () => {
    const sandboxUrl = `${window.location.origin}/fast-test-server/FAW_TIMBERLAND/Main/Login.aspx`;
    const u = username || 'FIN_ADMIN_VN';
    const wrongOrExpiredPass = 'WrongOrExpired_2023!';
    setLoginUrl(sandboxUrl);
    setUsername(u);
    setPassword(wrongOrExpiredPass);
    await handleRunLoginVerify(u, wrongOrExpiredPass, sandboxUrl);
  };

  // 场景 3：使用正确账密真实访问 FAST Login.aspx 服务端并验证跳出 Login.aspx 进入 General Ledger 主界面
  const handleTestValidPasswordOnServer = async () => {
    const sandboxUrl = `${window.location.origin}/fast-test-server/FAW_TIMBERLAND/Main/Login.aspx`;
    const u = 'FIN_ADMIN_VN';
    const validPass = 'Fast@2026#Timber';
    setLoginUrl(sandboxUrl);
    setUsername(u);
    setPassword(validPass);
    await handleRunLoginVerify(u, validPass, sandboxUrl);
  };

  const handleDownloadYaml = () => {
    const raw = credStatus?.raw_yaml || '';
    const blob = new Blob([raw], { type: 'text/yaml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'fast_credentials.yaml';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const eff = credStatus?.effective;
  const overallStatus = verifyResult?.overall_status;

  return (
    <div className="card" style={{ marginBottom: '20px', border: '2px solid #2563eb' }}>
      <div className="card-header" style={{ background: '#eff6ff', borderBottom: '1px solid #bfdbfe' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <span className="badge badge-pass" style={{ background: '#1d4ed8', color: '#fff' }}>
              真实浏览器 / HTTP 服务端鉴权与错误捕获
            </span>
            <h2 className="card-title" style={{ margin: 0 }}>
              <code>config/fast_credentials.yaml</code> 真实访问 <code>10.7.3.230</code> 登录校验与异常捕获工作台
            </h2>
          </div>
          <p className="card-desc" style={{ marginTop: '6px' }}>
            直接调用 <code>utils/login_handler.py</code> 的 <code>verify_fast_login_live()</code> 真实连接目标登录地址（如 <code>http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx</code>）：
            <strong> ① 若目标 URL 访问超时或无法打开，立即捕获 Timeout / URLError 返回当前页面；② 若 YAML 中填写了错误或过期账密，真实提交至服务端并将服务端报错捕获回当前页面。</strong>
          </p>
        </div>
        <div className="btn-group" style={{ flexWrap: 'wrap', gap: '6px' }}>
          <button
            className="btn btn-primary"
            type="button"
            disabled={verifying}
            onClick={handleTestRealIntranet1073230}
            title="真实连接 http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx（若未接内网将真实捕获超时错误，若已接内网则真实测试账密）"
          >
            {verifying ? '正在访问目标 URL...' : '🌐 1. 实测访问 10.7.3.230 (捕获超时或内网鉴权)'}
          </button>
          <button
            className="btn btn-secondary"
            type="button"
            disabled={verifying}
            onClick={handleTestWrongOrExpiredPasswordOnServer}
            style={{ borderColor: '#dc2626', color: '#b91c1c', background: '#fef2f2' }}
            title="模拟用户在 YAML 中填错密码或密码已过期，真实请求 FAST Login.aspx 并捕获服务端返回的错误提示"
          >
            🚫 2. 实测错密/过期服务端报错捕获 (AUTH_FAILED)
          </button>
          <button
            className="btn btn-secondary"
            type="button"
            disabled={verifying}
            onClick={handleTestValidPasswordOnServer}
            style={{ borderColor: '#16a34a', color: '#15803d', background: '#f0fdf4' }}
            title="向可达的 FAST Login.aspx 提交正确账密，验证跳出 Login.aspx 进入英文主界面 PASS"
          >
            ✅ 3. 实测服务端账密通过 (PASS)
          </button>
          <button
            className="btn btn-secondary"
            type="button"
            onClick={handleDownloadYaml}
            disabled={!credStatus?.raw_yaml}
          >
            下载 YAML
          </button>
        </div>
      </div>

      {/* 状态指示灯条 */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
          gap: '12px',
          padding: '14px 16px',
          background: '#f8fafc',
          borderBottom: '1px solid #e2e8f0',
        }}
      >
        <div style={{ background: '#fff', padding: '10px 12px', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
          <div style={{ fontSize: '12px', color: '#64748b' }}>1. YAML 语法与本地文件状态</div>
          <div style={{ marginTop: '4px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '6px' }}>
            {credStatus?.yaml_syntax_valid ? (
              <span className="badge badge-pass">YAML 语法合法</span>
            ) : (
              <span className="badge badge-fail">YAML 语法错误</span>
            )}
            <span className="mono" style={{ fontSize: '12px' }}>config/fast_credentials.yaml</span>
          </div>
        </div>

        <div style={{ background: '#fff', padding: '10px 12px', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
          <div style={{ fontSize: '12px', color: '#64748b' }}>2. 当前待测目标 URL</div>
          <div
            className="mono"
            style={{
              marginTop: '4px',
              fontWeight: 700,
              fontSize: '12px',
              color: '#0f172a',
              wordBreak: 'break-all',
            }}
          >
            {loginUrl || eff?.login_url || 'http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx'}
          </div>
        </div>

        <div style={{ background: '#fff', padding: '10px 12px', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
          <div style={{ fontSize: '12px', color: '#64748b' }}>3. 本地 YAML 已填账号与掩码</div>
          <div style={{ marginTop: '4px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            {eff?.username && eff?.has_password ? (
              <>
                <span className="mono" style={{ fontSize: '13px', color: '#0f172a' }}>{eff.username}</span>
                <span className="mono" style={{ fontSize: '13px', color: '#0284c7' }}>
                  {eff.masked_password} ({eff.password_length}位)
                </span>
              </>
            ) : (
              <span className="badge badge-warn">未填写账号或密码</span>
            )}
          </div>
        </div>

        <div style={{ background: '#fff', padding: '10px 12px', borderRadius: '6px', border: '1px solid #e2e8f0' }}>
          <div style={{ fontSize: '12px', color: '#64748b' }}>4. 最近一次真实访问 URL 校验结果</div>
          <div style={{ marginTop: '4px', fontWeight: 700 }}>
            {!verifyResult ? (
              <span className="badge badge-warn">尚未点击实测 (点击下方按钮访问目标 URL)</span>
            ) : overallStatus === 'PASS' ? (
              <span className="badge badge-pass">PASS (服务端鉴权通过)</span>
            ) : overallStatus === 'URL_UNREACHABLE' ? (
              <span className="badge badge-fail" style={{ background: '#dc2626', color: '#fff' }}>
                URL_UNREACHABLE (访问超时 / 打不开 URL)
              </span>
            ) : overallStatus === 'AUTH_FAILED' ? (
              <span className="badge badge-fail" style={{ background: '#dc2626', color: '#fff' }}>
                AUTH_FAILED (服务端拒绝：密码错误或过期)
              </span>
            ) : (
              <span className="badge badge-fail">{overallStatus}</span>
            )}
          </div>
        </div>
      </div>

      {feedbackMsg && (
        <div
          className={`status-banner ${
            feedbackMsg.type === 'ok' ? 'pass' : feedbackMsg.type === 'err' ? 'fail' : 'warn'
          }`}
          style={{ margin: '12px 16px 0 16px' }}
        >
          {feedbackMsg.text}
        </div>
      )}

      {/* 🚨 真实访问异常 / 服务端报错捕获展示面板 */}
      {verifyResult?.captured_error && (
        <div
          style={{
            margin: '14px 16px 0 16px',
            padding: '14px 16px',
            borderRadius: '8px',
            background: '#fef2f2',
            border: '2px solid #dc2626',
            color: '#7f1d1d',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
            <div style={{ fontWeight: 800, fontSize: '14px', color: '#991b1b' }}>
              🚨 真实浏览器 / HTTP 访问异常捕获报告 (Captured Error from Target URL)
            </div>
            <span className="badge badge-fail" style={{ background: '#b91c1c', color: '#fff', fontSize: '12px' }}>
              {verifyResult.captured_error.error_type} · 耗时 {verifyResult.captured_error.elapsed_ms} ms
            </span>
          </div>
          <div style={{ marginTop: '8px', fontSize: '13px', lineHeight: 1.6 }}>
            <div>
              <strong>发生阶段：</strong>
              <span className="mono">{verifyResult.captured_error.error_stage}</span>
            </div>
            <div>
              <strong>目标地址：</strong>
              <span className="mono">{verifyResult.captured_error.target_url}</span>
            </div>
            <div
              style={{
                marginTop: '6px',
                padding: '8px 10px',
                background: '#fff',
                border: '1px solid #fca5a5',
                borderRadius: '6px',
                fontFamily: 'monospace',
                fontSize: '12.5px',
                color: '#b91c1c',
                fontWeight: 700,
              }}
            >
              {verifyResult.captured_error.error_message}
            </div>
            <div style={{ marginTop: '6px', fontSize: '12px', color: '#991b1b' }}>
              <strong>排查建议：</strong> {verifyResult.captured_error.suggestion}
            </div>
          </div>
        </div>
      )}

      <div className="grid-2" style={{ padding: '16px', gap: '16px' }}>
        {/* 左侧：配置表单与真实访问测试按钮 */}
        <div style={{ border: '1px solid #e2e8f0', borderRadius: '8px', padding: '14px', background: '#fff' }}>
          <h3 style={{ margin: '0 0 10px 0', fontSize: '15px', color: '#0f172a' }}>
            在线编辑 <code>config/fast_credentials.yaml</code> 并真实访问目标 URL 测试
          </h3>

          <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            <div className="form-group" style={{ gridColumn: '1 / -1' }}>
              <label>
                越南 FAST 登录地址 (<code>fast_auth.login_url</code>)
              </label>
              <div style={{ display: 'flex', gap: '6px' }}>
                <input
                  type="text"
                  value={loginUrl}
                  onChange={(e) => setLoginUrl(e.target.value)}
                  placeholder="http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ padding: '4px 8px', fontSize: '12px', whiteSpace: 'nowrap' }}
                  onClick={() => setLoginUrl('http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx')}
                >
                  设为 10.7.3.230
                </button>
              </div>
            </div>

            <div className="form-group">
              <label>
                FAST 登录账号 (<code>fast_auth.username</code>)
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="例如: FIN_ADMIN_VN 或 ADMIN"
              />
            </div>

            <div className="form-group">
              <label style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>
                  FAST 登录密码 (<code>fast_auth.password</code>)
                </span>
                <button
                  type="button"
                  onClick={() => setShowPlainPassword(!showPlainPassword)}
                  style={{
                    border: 'none',
                    background: 'none',
                    color: '#2563eb',
                    cursor: 'pointer',
                    fontSize: '12px',
                    padding: 0,
                  }}
                >
                  {showPlainPassword ? '🙈 隐藏明文' : '👁️ 显示明文'}
                </button>
              </label>
              <input
                type={showPlainPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="在此填写待测密码"
              />
            </div>

            <div className="form-group">
              <label>
                核算单位 (<code>fast_auth.unit</code>)
              </label>
              <input
                type="text"
                value={unit}
                onChange={(e) => setUnit(e.target.value)}
                placeholder="CTY"
              />
            </div>

            <div className="form-group">
              <label>
                访问超时阈值 (秒) & 语言 (<code>language</code>)
              </label>
              <div style={{ display: 'flex', gap: '6px' }}>
                <input
                  type="number"
                  step="0.5"
                  min="1.5"
                  max="15"
                  value={timeoutSec}
                  onChange={(e) => setTimeoutSec(Number(e.target.value) || 3.5)}
                  style={{ width: '80px' }}
                  title="访问 10.7.3.230 的连接超时秒数"
                />
                <select value={language} onChange={(e) => setLanguage(e.target.value)} style={{ flex: 1 }}>
                  <option value="en">en — 自动切换英文 (English)</option>
                  <option value="vi">vi — 保持默认越南语 (Tiếng Việt)</option>
                </select>
              </div>
            </div>

            <div className="form-group">
              <label>
                Chrome 路径 (<code>chrome_cdp.chrome_path</code>)
              </label>
              <input
                type="text"
                value={chromePath}
                onChange={(e) => setChromePath(e.target.value)}
              />
            </div>

            <div className="form-group">
              <label>
                CDP 远程调试端口 (<code>remote_debugging_port</code>)
              </label>
              <input
                type="number"
                value={cdpPort}
                onChange={(e) => setCdpPort(Number(e.target.value) || 9222)}
              />
            </div>
          </div>

          <div style={{ marginTop: '12px', display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <button
              className="btn btn-primary"
              type="button"
              disabled={verifying || loading}
              onClick={() => handleRunLoginVerify()}
            >
              {verifying ? '正在真实访问目标 URL...' : '🌐 真实访问目标 URL 测试账密'}
            </button>
            <button
              className="btn btn-secondary"
              type="button"
              disabled={saving || loading || verifying}
              onClick={() => handleSaveAndTestCredentials()}
            >
              {saving ? '正在保存并访问测试...' : '💾 保存至 fast_credentials.yaml 并实测访问'}
            </button>
          </div>

          {/* 4 级凭据加载优先级矩阵表 */}
          <div style={{ marginTop: '14px' }}>
            <div style={{ fontSize: '12px', fontWeight: 700, color: '#475569', marginBottom: '6px' }}>
              四级凭据读取优先级链（注：本地文件读取成功后，必须通过右侧 6 步真实访问服务端才能确认密码未过期/无误）：
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>优先级</th>
                    <th>配置来源</th>
                    <th>账号</th>
                    <th>本地文件读取状态</th>
                  </tr>
                </thead>
                <tbody>
                  {(credStatus?.priority_chain || []).map((item) => (
                    <tr key={item.rank}>
                      <td className="mono">P{item.rank}</td>
                      <td>{item.source}</td>
                      <td className="mono">
                        {item.username}
                        {item.masked_password ? ` (${item.masked_password})` : ''}
                      </td>
                      <td>
                        <span
                          className={`badge ${
                            item.has_password ? 'badge-warn' : 'badge-info'
                          }`}
                        >
                          {item.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* 右侧：6 步真实访问 10.7.3.230 & 服务端鉴权核验流水线 */}
        <div style={{ border: '1px solid #e2e8f0', borderRadius: '8px', padding: '14px', background: '#fff' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
            <h3 style={{ margin: 0, fontSize: '15px', color: '#0f172a' }}>
              FAST 自动登录 6 步真实浏览器 / HTTP 诊断流水线
            </h3>
            {verifyResult && (
              <span
                className={`badge ${
                  verifyResult.overall_status === 'PASS' ? 'badge-pass' : 'badge-fail'
                }`}
              >
                {verifyResult.overall_status === 'PASS'
                  ? 'ALL 6 STEPS PASS'
                  : verifyResult.overall_status === 'URL_UNREACHABLE'
                    ? 'URL_UNREACHABLE (访问超时 / 打不开)'
                    : verifyResult.overall_status === 'AUTH_FAILED'
                      ? 'AUTH_FAILED (服务端拒绝账密)'
                      : verifyResult.overall_status}
              </span>
            )}
          </div>

          {!verifyResult ? (
            <div
              style={{
                padding: '28px 16px',
                textAlign: 'center',
                background: '#f8fafc',
                borderRadius: '6px',
                border: '1px dashed #cbd5e1',
                color: '#475569',
                fontSize: '13px',
              }}
            >
              请点击左侧 <strong>「🌐 真实访问目标 URL 测试账密」</strong> 或顶部快捷测试按钮，
              后端将真实访问目标 URL（如 <code>http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx</code>），
              并将<strong>访问超时 / 打不开 URL</strong> 或 <strong>服务端返回的密码错误/过期异常</strong> 实时捕获到当前页面。
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {verifyResult.steps.map((st) => (
                <div
                  key={st.step}
                  style={{
                    padding: '9px 12px',
                    borderRadius: '6px',
                    border:
                      st.status === 'PASS'
                        ? '1px solid #bbf7d0'
                        : st.status === 'WARN'
                          ? '1px solid #fde68a'
                          : '2px solid #f87171',
                    background:
                      st.status === 'PASS'
                        ? '#f0fdf4'
                        : st.status === 'WARN'
                          ? '#fffbeb'
                          : '#fef2f2',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
                    <div style={{ fontWeight: 700, fontSize: '13px', color: '#0f172a' }}>
                      Step {st.step}. {st.title}
                    </div>
                    <span
                      className={`badge ${
                        st.status === 'PASS'
                          ? 'badge-pass'
                          : st.status === 'WARN'
                            ? 'badge-warn'
                            : 'badge-fail'
                      }`}
                    >
                      {st.status}
                    </span>
                  </div>
                  <div className="mono" style={{ fontSize: '11px', color: '#475569', marginTop: '3px' }}>
                    调用接口: {st.method}
                  </div>
                  <div style={{ fontSize: '12px', color: '#1e293b', marginTop: '4px' }}>{st.detail}</div>
                  <pre
                    className="code-box"
                    style={{
                      margin: '6px 0 0 0',
                      padding: '6px 8px',
                      fontSize: '11px',
                      maxHeight: '72px',
                      overflowY: 'auto',
                    }}
                  >
                    {st.log_output}
                  </pre>
                </div>
              ))}
            </div>
          )}

          {/* 命令行独立验证指引 */}
          <div style={{ marginTop: '12px', paddingTop: '10px', borderTop: '1px solid #e2e8f0' }}>
            <div style={{ fontSize: '12px', fontWeight: 700, color: '#334155', marginBottom: '4px' }}>
              本地终端一键实测命令（真实连接 <code>config/fast_credentials.yaml</code> 中的 URL 测试账密）：
            </div>
            <pre className="code-box" style={{ margin: 0, padding: '8px 10px', fontSize: '11px' }}>
              {credStatus?.cli_verify_command ||
                'python -c "from utils.login_handler import verify_fast_login_live; import json; print(json.dumps(verify_fast_login_live(), ensure_ascii=False, indent=2))"'}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
};
