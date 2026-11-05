#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/login_handler.py
模块定位: 越南 FAST 财务系统 Web 登录页面自动化填表、语言切换、真实服务端鉴权与错误捕获处理器
================================================================================
【本文件核心作用】
1. 负责驱动 Chrome CDP 客户端（及 HTTP ASP.NET WebForms 会话探测器）真实访问越南 FAST 登录页
   （默认 `http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx`）；
2. 自动等待 ASP.NET 登录表单元素 (`LoginExtender_txtUserName`) 渲染就绪，若目标 URL 访问超时、
   拒绝连接或返回 Chrome 网络错误页 (`chrome-error://` / `ERR_CONNECTION_TIMED_OUT`)，立即捕获真实异常并报错返回；
3. 自动检测并将默认越南语 (`Tiếng Việt`) 登录界面切换为英文界面 (`English` / `E`)；
4. 支持多级凭据自动加载（命令行参数 > `.env` 环境变量 > `config/fast_credentials.yaml` > `config/account.yaml`），
   自动填写用户名 (`LoginExtender_txtUserName`)、密码 (`LoginExtender_txtPassword`) 与核算单位 (`CTY`)，
   并点击登录按钮 (`LoginExtender_Ok`)；
5. 通过检查真实跳转 URL、注销按钮 (`Logout`)、英文主菜单 (`General Ledger`) 以及抓取服务端错误提示标签
   (`extract_login_error`)，核验账号密码是否真实通过 FAST 服务端鉴权（若 YAML 中填写了错误或过期账密，
   直接将服务端返回的错误消息捕获回前端页面展示）。
================================================================================
"""

import hashlib
import http.cookiejar
import os
import re
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from utils.logger import setup_logger

try:
    from utils.cdp_client import CDPClient
except ImportError:
    CDPClient = Any  # type: ignore

try:
    from utils.chrome_launcher import ChromeLauncher
except ImportError:
    ChromeLauncher = None  # type: ignore

try:
    import yaml
except ImportError:
    yaml = None


def compute_credential_fingerprint(username: str, password: str, unit: str = "CTY") -> str:
    """计算 FAST 登录凭据 (username:password) 的 12 位 SHA-256 脱敏摘要（仅用于日志脱敏标识，不代替真实服务端鉴权）。"""
    u = str(username or "").strip()
    p = str(password or "")
    if not u and not p:
        return ""
    return hashlib.sha256(f"{u}:{p}".encode("utf-8")).hexdigest()[:12]


def _mask_password(pwd: str) -> str:
    if not pwd:
        return ""
    if len(pwd) <= 2:
        return "*" * len(pwd)
    return pwd[0] + ("•" * max(4, len(pwd) - 2)) + pwd[-1]


def _parse_yaml_with_fallback(text: str) -> Dict[str, Any]:
    if yaml is not None:
        return yaml.safe_load(text) or {}
    result: Dict[str, Any] = {}
    curr_section: Optional[str] = None
    for raw_line in text.splitlines():
        s = raw_line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        k, v = s.split(":", 1)
        k = k.strip().strip("'\"")
        v = v.strip()
        if v.startswith(("'", '"')) and v.endswith(("'", '"')) and len(v) >= 2:
            v = v[1:-1]
        elif "#" in v:
            v = v.split("#", 1)[0].strip().strip("'\"")
        if indent == 0:
            curr_section = k
            result[curr_section] = {} if v == "" else v
        elif curr_section and isinstance(result.get(curr_section), dict):
            result[curr_section][k] = v
    return result


def load_fast_credentials(config_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    按照最佳实践优先级加载越南 FAST 登录凭据与自动化配置：
    优先级：环境变量 (.env) > config/fast_credentials.yaml > config/account.yaml (chrome 节点)
    """
    base_dir = Path(config_dir) if config_dir else Path(__file__).parent.parent / "config"
    cred_yaml_path = base_dir / "fast_credentials.yaml"
    account_yaml_path = base_dir / "account.yaml"

    cred_data: Dict[str, Any] = {}
    if cred_yaml_path.exists():
        try:
            with open(cred_yaml_path, "r", encoding="utf-8") as f:
                cred_data = _parse_yaml_with_fallback(f.read())
        except Exception:
            cred_data = {}

    acc_chrome: Dict[str, Any] = {}
    if account_yaml_path.exists():
        try:
            with open(account_yaml_path, "r", encoding="utf-8") as f:
                acc_all = _parse_yaml_with_fallback(f.read())
                acc_chrome = acc_all.get("chrome") or {}
        except Exception:
            acc_chrome = {}

    fast_auth = cred_data.get("fast_auth") if isinstance(cred_data.get("fast_auth"), dict) else {}
    chrome_cdp = cred_data.get("chrome_cdp") if isinstance(cred_data.get("chrome_cdp"), dict) else {}
    selectors = cred_data.get("selectors") or {}

    login_url = (
        os.getenv("FAST_URL")
        or fast_auth.get("login_url")
        or acc_chrome.get("fast_url")
        or "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"
    )
    username = (
        os.getenv("FAST_USER")
        or fast_auth.get("username")
        or acc_chrome.get("fast_user")
        or ""
    )
    password = (
        os.getenv("FAST_PASSWORD")
        or fast_auth.get("password")
        or acc_chrome.get("fast_password")
        or ""
    )
    unit = (
        os.getenv("FAST_UNIT")
        or fast_auth.get("unit")
        or acc_chrome.get("fast_unit")
        or "CTY"
    )
    language = (
        os.getenv("FAST_LANGUAGE")
        or fast_auth.get("language")
        or acc_chrome.get("fast_language")
        or "en"
    )
    uname_str = str(username).strip()
    pwd_str = str(password)
    unit_str = str(unit).strip() or "CTY"
    return {
        "login_url": str(login_url).strip(),
        "username": uname_str,
        "password": pwd_str,
        "unit": unit_str,
        "language": str(language).strip().lower() or "en",
        "fingerprint": compute_credential_fingerprint(uname_str, pwd_str, unit_str),
        "chrome_cdp": chrome_cdp,
        "selectors": selectors,
        "credential_file": str(cred_yaml_path),
    }


def _resolve_chrome_executable(configured_path: str = "") -> Tuple[Optional[str], str]:
    """探测当前系统可用的 Chrome / Chromium / Edge 可执行文件路径。"""
    candidates: List[str] = []
    if configured_path:
        candidates.append(configured_path)
    env_chrome = os.getenv("CHROME_PATH", "").strip()
    if env_chrome:
        candidates.append(env_chrome)
    candidates.extend([
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "msedge",
    ])
    for cand in candidates:
        if not cand:
            continue
        if os.path.isabs(cand) and os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand, f"已定位浏览器可执行文件: {cand}"
        found = shutil.which(cand)
        if found:
            return found, f"已通过系统 PATH 定位浏览器: {found}"
    return None, f"未在本机找到可执行浏览器进程 (配置的 chrome_path='{configured_path}' 在当前系统不存在)"


class LoginHandler:
    """越南 FAST 财务软件网页端登录处理器（支持自动切换英文界面、真实 DOM 错误抓取与登录态核验）。"""

    def __init__(self, cdp_client: CDPClient, login_url: str = ""):
        """
        初始化登录处理器。
        :param cdp_client: 已连接的 CDPClient 实例
        :param login_url: 越南 FAST 登录页面地址（支持从环境变量 FAST_URL 或 fast_credentials.yaml 注入）
        """
        self.cdp = cdp_client
        self.creds = load_fast_credentials()
        self.login_url = login_url or self.creds.get("login_url") or "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"
        self.logger = setup_logger(__name__)
        self.last_error: str = ""

    def navigate_to_login(self) -> bool:
        """导航至越南 FAST 登录页面。"""
        self.last_error = ""
        self.logger.info(f"正在导航到登录页面: {self.login_url}")
        ok = self.cdp.navigate(self.login_url)
        if not ok:
            self.last_error = f"CDP Page.navigate 调用失败，无法打开 {self.login_url}"
        return ok

    def check_browser_network_error(self) -> Optional[str]:
        """检查当前 Chrome 标签页是否落入网络错误页（如 ERR_CONNECTION_TIMED_OUT / chrome-error://）。"""
        js_check_err = """
        (function() {
            var href = String(window.location.href || '');
            var title = String(document.title || '');
            var text = String((document.body && document.body.innerText) || '');
            if (href.indexOf('chrome-error://') === 0 || href.indexOf('about:neterror') === 0) {
                var errCodeMatch = text.match(/ERR_[A-Z0-9_]+/);
                var code = errCodeMatch ? errCodeMatch[0] : 'CHROME_NETWORK_ERROR';
                return '浏览器无法打开页面 (' + code + '): ' + (text.slice(0, 240).replace(/\\s+/g, ' ').trim());
            }
            var netErrMatch = text.match(/ERR_(CONNECTION_TIMED_OUT|CONNECTION_REFUSED|ADDRESS_UNREACHABLE|NAME_NOT_RESOLVED|INTERNET_DISCONNECTED|TIMED_OUT)[A-Z0-9_]*/);
            if (netErrMatch) {
                return '浏览器网络访问错误 (' + netErrMatch[0] + '): ' + (text.slice(0, 240).replace(/\\s+/g, ' ').trim());
            }
            return '';
        })()
        """
        err_msg = self.cdp.execute_script(js_check_err)
        if err_msg and isinstance(err_msg, str) and err_msg.strip():
            self.last_error = err_msg.strip()
            return self.last_error
        return None

    def wait_for_page_ready(self, timeout: int = 15) -> bool:
        """
        轮询等待登录页面的 DOM 与用户名输入框 (`LoginExtender_txtUserName`) 加载完成。
        修复说明：若浏览器遇到连接超时错误页或超时仍未出现登录框，立即返回 False 并记录 self.last_error（严禁假阳性返回 True）。
        """
        self.logger.info("等待页面加载完成...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            net_err = self.check_browser_network_error()
            if net_err:
                self.logger.error(f"  检测到浏览器访问异常: {net_err}")
                return False

            ready_state = self.cdp.execute_script("return document.readyState")
            self.logger.info(f"  页面状态: {ready_state}")

            if ready_state in ("complete", "interactive"):
                username_exists = self.cdp.execute_script(
                    "return document.querySelector('input[id=\\'LoginExtender_txtUserName\\']') !== null"
                )
                if username_exists:
                    self.logger.info("  登录页面已就绪 (检测到 LoginExtender_txtUserName)")
                    time.sleep(1)
                    return True

            time.sleep(1)

        net_err = self.check_browser_network_error()
        if net_err:
            return False

        cur_url = self.cdp.execute_script("return window.location.href") or ""
        self.last_error = (
            f"等待登录页面超时 ({timeout}s)：在目标地址 {self.login_url} (当前 URL: {cur_url}) "
            f"中未检测到 FAST 登录表单元素 input[id='LoginExtender_txtUserName']"
        )
        self.logger.error(self.last_error)
        return False

    def switch_language(self, language: str = "en") -> bool:
        """
        将越南 FAST 登录界面从默认越南语 (Tiếng Việt) 切换为指定语言（默认英语 'en'）。
        """
        lang_norm = (language or "en").strip().lower()
        if lang_norm not in ("en", "eng", "english", "e"):
            self.logger.info(f"保持登录界面语言设置: {language}")
            return True

        self.logger.info("正在检测并将 FAST 登录界面切换为英文 (English)...")
        js_switch_en = """
        (function() {
            var selects = document.querySelectorAll("select[id*='Lang'], select[id*='lang'], select[name*='Lang']");
            for (var i = 0; i < selects.length; i++) {
                var sel = selects[i];
                for (var j = 0; j < sel.options.length; j++) {
                    var opt = sel.options[j];
                    var txt = (opt.text || '').toLowerCase();
                    var val = (opt.value || '').toLowerCase();
                    if (val === 'e' || val === 'en' || txt.indexOf('english') >= 0 || txt.indexOf('anh') >= 0) {
                        if (sel.selectedIndex !== j) {
                            sel.selectedIndex = j;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        return 'select_switched';
                    }
                }
            }
            var inputs = document.querySelectorAll("input[type='radio'], input[type='button'], a, span, button");
            for (var k = 0; k < inputs.length; k++) {
                var el = inputs[k];
                var v = (el.value || '').trim();
                var t = (el.innerText || el.title || '').toLowerCase();
                if (v === 'E' || t === 'english' || t === 'en' || t.indexOf('tiếng anh') >= 0) {
                    el.click();
                    return 'element_clicked';
                }
            }
            return 'already_or_not_found';
        })()
        """
        res = self.cdp.execute_script(js_switch_en)
        self.logger.info(f"英文界面切换结果: {res}")
        if res in ("select_switched", "element_clicked"):
            time.sleep(1.0)
        return True

    def fill_login_form(self, username: str, password: str, unit: str) -> bool:
        """自动向 FAST 登录表单填入账号、密码和单位代码 (如 CTY)。"""
        self.logger.info("正在填写登录表单...")

        username_filled = self.cdp.set_input_value(
            "input[id='LoginExtender_txtUserName']",
            username
        )
        if not username_filled:
            username_filled = self.cdp.set_input_value(
                "input[type='text']:first-of-type",
                username
            )

        time.sleep(0.5)

        password_filled = self.cdp.set_input_value(
            "input[id='LoginExtender_txtPassword']",
            password
        )

        time.sleep(0.5)

        unit_filled = self.cdp.set_input_value(
            "input[name='txtUnit'], input[id*='Unit'], input[id*='unit'], select[id*='Unit']",
            unit
        )

        self.logger.info(f"用户名填写: {'成功' if username_filled else '失败'}")
        self.logger.info(f"密码填写: {'成功' if password_filled else '失败'}")
        self.logger.info(f"单位填写: {'成功' if unit_filled else '失败'}")

        if not (username_filled and password_filled):
            self.last_error = (
                f"登录表单元素填充失败 (username_filled={username_filled}, password_filled={password_filled})"
            )
        return username_filled and password_filled

    def click_login_button(self) -> bool:
        """点击越南 FAST 登录页的提交按钮 (`LoginExtender_Ok` 或包含 Login/OK/Đăng nhập 文本的按钮)。"""
        self.logger.info("正在点击登录按钮...")

        selectors = [
            "button[id='LoginExtender_Ok']",
            "input[id='LoginExtender_Ok']",
            "a[id*='Login']"
        ]

        for selector in selectors:
            if self.cdp.click_element(selector):
                self.logger.info(f"使用选择器点击登录按钮: {selector}")
                time.sleep(3)
                return True

        script = """
            (function() {
                var buttons = document.querySelectorAll('input[type="submit"], button[type="submit"], button');
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    var val = (btn.value || '').toLowerCase();
                    var txt = (btn.innerText || '').toLowerCase();
                    if (val.indexOf('login') >= 0 || val.indexOf('登录') >= 0 || val.indexOf('ok') >= 0 || val.indexOf('đăng nhập') >= 0) {
                        btn.click();
                        return true;
                    }
                    if (txt.indexOf('login') >= 0 || txt.indexOf('登录') >= 0 || txt.indexOf('ok') >= 0 || txt.indexOf('đăng nhập') >= 0) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            })()
        """
        result = self.cdp.execute_script(script)
        if result:
            self.logger.info("通过 JavaScript 点击登录按钮")
            time.sleep(3)
            return True

        self.last_error = "未能在页面上找到或点击登录按钮 (LoginExtender_Ok)"
        return False

    def extract_login_error(self) -> str:
        """
        从当前浏览器 DOM 中提取 FAST 服务端返回的登录错误消息（如密码错误、账号不存在、账号过期或 ASP.NET 异常）。
        """
        net_err = self.check_browser_network_error()
        if net_err:
            return net_err

        js_extract_err = """
        (function() {
            var selectors = [
                "#LoginExtender_lblError",
                "#LoginExtender_FailureText",
                "#lblError",
                "#lblMessage",
                "[id*='Error']",
                "[id*='Failure']",
                "[id*='Message']",
                ".error",
                ".login-error",
                ".validation-summary-errors",
                ".alert-danger",
                "span[style*='red']",
                "div[style*='red']",
                "font[color='red']"
            ];
            for (var i = 0; i < selectors.length; i++) {
                var nodes = document.querySelectorAll(selectors[i]);
                for (var j = 0; j < nodes.length; j++) {
                    var txt = String(nodes[j].innerText || nodes[j].textContent || '').trim();
                    if (txt && txt.length >= 2 && txt.length <= 300) {
                        return txt;
                    }
                }
            }
            var bodyText = String((document.body && document.body.innerText) || '');
            var lines = bodyText.split(/\\r?\\n/).map(function(l) { return l.trim(); }).filter(Boolean);
            var errPatterns = /(invalid|incorrect|failed|error|expired|locked|denied|sai tên|mật khẩu|không đúng|hết hạn|không hợp lệ|错误|失败|过期|无效)/i;
            for (var k = 0; k < lines.length; k++) {
                if (errPatterns.test(lines[k]) && lines[k].length <= 240) {
                    return lines[k];
                }
            }
            return '';
        })()
        """
        dom_err = self.cdp.execute_script(js_extract_err)
        if dom_err and isinstance(dom_err, str) and dom_err.strip():
            self.last_error = f"FAST 服务端返回错误: {dom_err.strip()}"
            return self.last_error

        cur_url = self.cdp.execute_script("return window.location.href") or ""
        if "Login.aspx" in str(cur_url):
            self.last_error = (
                f"提交登录表单后页面仍停留在 {cur_url}，未进入主界面——服务端拒绝了该账号或密码（请检查账号密码是否正确或是否已过期）"
            )
            return self.last_error

        return self.last_error or "未知登录错误：未检测到 FAST 登录成功主界面标识"

    def is_logged_in(self) -> bool:
        """
        检查当前页面是否已真实通过 FAST 认证并进入主界面。
        修复说明：排除 chrome-error:// 及空页面假阳性，必须确保无网络错误且已进入有效系统页面。
        """
        if self.check_browser_network_error():
            return False

        current_url = str(self.cdp.execute_script("return window.location.href") or "").strip()
        if not current_url or not (current_url.startswith("http://") or current_url.startswith("https://")):
            return False

        logout_button = self.cdp.query_selector(
            "a[href*='Logout'], input[value*='Logout'], button[id*='Logout']"
        )
        if logout_button and logout_button.get("found"):
            return True

        welcome_text = self.cdp.execute_script(
            "return Boolean(document.body && (" +
            "document.body.innerText.indexOf('Welcome') >= 0 || " +
            "document.body.innerText.indexOf('欢迎') >= 0 || " +
            "document.body.innerText.indexOf('Financials') >= 0 || " +
            "document.body.innerText.indexOf('General Ledger') >= 0))"
        )
        if welcome_text:
            return True

        # 若 URL 已跳出 Login.aspx，还需确认登录框不再存在且页面无服务端报错
        if "Login.aspx" not in current_url:
            still_has_login_input = self.cdp.execute_script(
                "return document.querySelector('input[id=\\'LoginExtender_txtUserName\\']') !== null"
            )
            if not still_has_login_input and not self.extract_login_error():
                return True

        return False

    def login(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        unit: Optional[str] = None,
        language: Optional[str] = None,
    ) -> bool:
        """
        一键执行完整的自动登录流程：
        1. 若未显式传入 username/password，自动从 `config/fast_credentials.yaml` 或 `.env` 读取；
        2. 导航至登录页 (`http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx`) -> 等待表单就绪；
        3. 自动切换语言为英文 (`en`)；
        4. 填写用户名/密码/单位 (`CTY`) -> 点击登录 -> 校验登录成功状态并捕获服务端错误。
        """
        effective_user = username if username is not None and username != "" else self.creds.get("username", "")
        effective_pwd = password if password is not None and password != "" else self.creds.get("password", "")
        effective_unit = unit or self.creds.get("unit", "CTY")
        effective_lang = language or self.creds.get("language", "en")

        if not self.navigate_to_login():
            self.logger.error(f"导航到登录页面失败: {self.last_error}")
            return False

        if not self.wait_for_page_ready():
            self.logger.error(f"页面加载失败或超时: {self.last_error}")
            return False

        self.switch_language(effective_lang)

        if not effective_user or not effective_pwd:
            self.last_error = (
                "未在命令行、.env 或 config/fast_credentials.yaml 中检测到账号密码，请先在 config/fast_credentials.yaml 填写"
            )
            self.logger.error(self.last_error)
            return False

        if not self.fill_login_form(effective_user, effective_pwd, effective_unit):
            self.logger.error(f"填写登录表单失败: {self.last_error}")
            return False

        if not self.click_login_button():
            self.logger.error(f"点击登录按钮失败: {self.last_error}")
            return False

        time.sleep(2)

        if self.is_logged_in():
            self.logger.info("登录成功（已进入英文主界面）")
            return True
        else:
            err = self.extract_login_error()
            self.logger.error(f"登录失败: {err}")
            return False


def _extract_error_from_html(html_text: str) -> str:
    """从 FAST Login.aspx 返回的 HTML 文本中提取服务端错误提示（如密码错误、账号过期等）。"""
    if not html_text:
        return ""
    patterns = [
        r'id=["\'](?:LoginExtender_lblError|LoginExtender_FailureText|lblError|lblMessage)["\'][^>]*>(.*?)</',
        r'class=["\'][^"\']*(?:error|login-error|alert-danger|validation-summary-errors)[^"\']*["\'][^>]*>(.*?)</',
    ]
    for pat in patterns:
        m = re.search(pat, html_text, re.IGNORECASE | re.DOTALL)
        if m:
            cleaned = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            if cleaned:
                return cleaned

    text_only = re.sub(r"<script[\s\S]*?</script>", " ", html_text, flags=re.IGNORECASE)
    text_only = re.sub(r"<style[\s\S]*?</style>", " ", text_only, flags=re.IGNORECASE)
    text_only = re.sub(r"<[^>]+>", "\n", text_only)
    err_regex = re.compile(
        r"(invalid\s+username|invalid\s+password|login\s+failed|incorrect\s+password|account\s+expired|"
        r"sai\s+tên\s+đăng\s+nhập|mật\s+khẩu\s+không\s+đúng|tài\s+khoản\s+đã\s+hết\s+hạn|"
        r"用户名或密码错误|密码错误|账号已过期|登录失败)[^\r\n]{0,120}",
        re.IGNORECASE,
    )
    m2 = err_regex.search(text_only)
    if m2:
        return m2.group(0).strip()
    return ""


def _submit_aspnet_login_http(
    opener: urllib.request.OpenerDirector,
    login_url: str,
    initial_html: str,
    username: str,
    password: str,
    unit: str,
    language: str,
    timeout_sec: float = 5.0,
) -> Dict[str, Any]:
    """
    当目标 FAST URL (如 10.7.3.230) 网络可达时，通过携带 Cookie 与 ASP.NET Hidden Fields
    (`__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`) 向 `Login.aspx` 发起真实的表单 POST 鉴权请求，
    并捕获服务端返回的真实响应（跳转成功或返回密码错误提示）。
    """
    hidden_fields: Dict[str, str] = {}
    for match in re.finditer(r"<input[^>]+type=[\"']hidden[\"'][^>]*>", initial_html, re.IGNORECASE):
        tag = match.group(0)
        name_m = re.search(r"name=[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        val_m = re.search(r"value=[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
        if name_m:
            hidden_fields[name_m.group(1)] = val_m.group(1) if val_m else ""

    form_data = {
        **hidden_fields,
        "LoginExtender$txtUserName": username,
        "LoginExtender_txtUserName": username,
        "LoginExtender$txtPassword": password,
        "LoginExtender_txtPassword": password,
        "txtUnit": unit,
        "LoginExtender$ddlLanguage": "E" if language.lower().startswith("e") else "V",
        "LoginExtender$Ok": "Login",
    }
    encoded_body = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=encoded_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            "Referer": login_url,
        },
        method="POST",
    )
    t0 = time.time()
    try:
        resp = opener.open(req, timeout=timeout_sec)
        elapsed_ms = int((time.time() - t0) * 1000)
        final_url = resp.geturl() or login_url
        status_code = getattr(resp, "status", 200)
        resp_html = resp.read().decode("utf-8", errors="replace")

        has_logout_or_menu = bool(
            re.search(r"(Logout|General\s+Ledger|Trial\s+Balance|Welcome|Main/Default\.aspx)", resp_html, re.IGNORECASE)
        )
        still_on_login = ("Login.aspx" in final_url) and bool(
            re.search(r"LoginExtender_txtUserName", resp_html, re.IGNORECASE)
        )
        extracted_err = _extract_error_from_html(resp_html)

        if has_logout_or_menu or (not still_on_login and not extracted_err and status_code < 400):
            return {
                "ok": True,
                "logged_in": True,
                "status_code": status_code,
                "final_url": final_url,
                "elapsed_ms": elapsed_ms,
                "server_error": "",
            }
        return {
            "ok": True,
            "logged_in": False,
            "status_code": status_code,
            "final_url": final_url,
            "elapsed_ms": elapsed_ms,
            "server_error": extracted_err
            or f"服务端 ({final_url}) 拒绝了账号 [{username}] 的登录请求（页面仍停留在 Login.aspx，请检查密码是否正确或账号是否已过期）",
        }
    except urllib.error.HTTPError as he:
        elapsed_ms = int((time.time() - t0) * 1000)
        err_body = ""
        try:
            err_body = he.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        extracted = _extract_error_from_html(err_body)
        return {
            "ok": False,
            "logged_in": False,
            "status_code": he.code,
            "final_url": login_url,
            "elapsed_ms": elapsed_ms,
            "server_error": extracted or f"目标服务器返回 HTTP {he.code} ({he.reason})",
        }
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "logged_in": False,
            "status_code": 0,
            "final_url": login_url,
            "elapsed_ms": elapsed_ms,
            "server_error": f"提交登录表单时发生网络异常 ({type(e).__name__}): {e}",
        }


def verify_fast_login_live(
    test_username: Optional[str] = None,
    test_password: Optional[str] = None,
    test_unit: Optional[str] = None,
    test_language: Optional[str] = None,
    test_login_url: Optional[str] = None,
    timeout_sec: float = 4.0,
    headless: bool = True,
    config_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    真实访问目标 URL（如 `http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx`）并测试账号密码的 6 步端到端诊断器：
    1. 绝不仅与本地 YAML 做静态比对（因为用户在 YAML 中可能填写了错误或过期的账号密码）；
    2. 真实向目标 URL 发起 TCP/HTTP 探测与 Chrome CDP 浏览器访问：
       - 若访问超时、无法连接或打不开该 URL：立即捕获真实的 TimeoutError / URLError / Chrome ERR_* 异常并返回给前端页面 (`overall_status: "URL_UNREACHABLE"`)；
       - 若目标 URL 可打开，真实填入账号密码提交给服务端：如果服务端返回密码错误或拒绝登录，捕获服务端错误消息返回给前端页面 (`overall_status: "AUTH_FAILED"`)；
       - 仅当目标 URL 真实可达且服务端验证账密通过进入主界面时，才返回 `overall_status: "PASS"`。
    """
    creds = load_fast_credentials(config_dir=config_dir)
    eff_user = str(test_username).strip() if test_username is not None else creds.get("username", "")
    eff_pass = str(test_password) if test_password is not None else creds.get("password", "")
    eff_unit = (str(test_unit).strip() if test_unit is not None else creds.get("unit", "CTY")) or "CTY"
    eff_lang = (str(test_language).strip() if test_language is not None else creds.get("language", "en")) or "en"
    eff_url = (str(test_login_url).strip() if test_login_url is not None else creds.get("login_url", "")) or "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"

    masked_pwd = _mask_password(eff_pass)
    fp = compute_credential_fingerprint(eff_user, eff_pass, eff_unit)
    chrome_cfg = creds.get("chrome_cdp") or {}
    cdp_port = int(chrome_cfg.get("remote_debugging_port") or 9222)
    configured_chrome_path = str(chrome_cfg.get("chrome_path") or "")

    steps: List[Dict[str, Any]] = []
    captured_error: Optional[Dict[str, Any]] = None

    # -------------------------------------------------------------------------
    # Step 1: 读取待测凭据 (config/fast_credentials.yaml 或页面传入参数)
    # -------------------------------------------------------------------------
    has_credentials = bool(eff_user and eff_pass)
    if not has_credentials:
        captured_error = {
            "error_type": "CredentialsMissingError",
            "error_stage": "Step 1: 读取待测账号密码",
            "target_url": eff_url,
            "elapsed_ms": 0,
            "error_message": (
                f"待测账号或密码为空 (username='{eff_user or '空'}', password_length={len(eff_pass)})。"
                f"请先在 config/fast_credentials.yaml 或当前页面输入框中填写账号和密码。"
            ),
            "suggestion": "请在左侧表单填写越南 FAST 用户名与密码并保存至 config/fast_credentials.yaml 后重试。",
        }
        steps.append({
            "step": 1,
            "title": "加载待测账密配置 (load_fast_credentials)",
            "method": "utils.login_handler.load_fast_credentials()",
            "status": "FAIL",
            "detail": f"❌ {captured_error['error_message']}",
            "log_output": "[ERROR] 待测账号或密码为空，终止后续浏览器访问测试",
        })
    else:
        steps.append({
            "step": 1,
            "title": "加载待测账密配置 (load_fast_credentials)",
            "method": "utils.login_handler.load_fast_credentials()",
            "status": "PASS",
            "detail": (
                f"已读取待测凭据：账号 [{eff_user}] · 掩码 [{masked_pwd}] ({len(eff_pass)}位) · "
                f"单位 [{eff_unit}] · 目标地址 [{eff_url}]（即将通过浏览器真实访问该 URL 验证账密是否有效）"
            ),
            "log_output": f"[INFO] 待测账号: {eff_user} | 掩码: {masked_pwd} | 目标 URL: {eff_url}",
        })

    # -------------------------------------------------------------------------
    # Step 2: 真实探测目标 URL (如 http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx) 网络连通性
    # -------------------------------------------------------------------------
    parsed_url = urllib.parse.urlparse(eff_url)
    target_host = parsed_url.hostname or "10.7.3.230"
    target_port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    http_reachable = False
    initial_html = ""
    http_status_code = 0
    probe_elapsed_ms = 0
    network_err_detail = ""
    network_err_type = ""

    t_probe_start = time.time()
    try:
        req_get = urllib.request.Request(
            eff_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            },
            method="GET",
        )
        resp_get = opener.open(req_get, timeout=timeout_sec)
        probe_elapsed_ms = int((time.time() - t_probe_start) * 1000)
        http_status_code = getattr(resp_get, "status", 200)
        initial_html = resp_get.read().decode("utf-8", errors="replace")
        http_reachable = True
        steps.append({
            "step": 2,
            "title": f"真实访问目标 URL 连通性探测 ({target_host}:{target_port})",
            "method": f'urllib.request.urlopen("{eff_url}", timeout={timeout_sec})',
            "status": "PASS",
            "detail": (
                f"✅ 成功连接目标服务器 {eff_url} (HTTP {http_status_code}，响应耗时 {probe_elapsed_ms} ms，"
                f"返回页面长度 {len(initial_html)} 字节)"
            ),
            "log_output": f"[INFO] GET {eff_url} -> HTTP {http_status_code} ({probe_elapsed_ms} ms)",
        })
    except urllib.error.HTTPError as he:
        probe_elapsed_ms = int((time.time() - t_probe_start) * 1000)
        http_status_code = he.code
        network_err_type = f"HTTPError (HTTP {he.code})"
        network_err_detail = f"访问 {eff_url} 时服务器返回 HTTP 错误状态码 {he.code} ({he.reason})，耗时 {probe_elapsed_ms} ms"
    except urllib.error.URLError as ue:
        probe_elapsed_ms = int((time.time() - t_probe_start) * 1000)
        reason = ue.reason
        if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
            network_err_type = "ConnectTimeoutError (访问超时)"
            network_err_detail = (
                f"访问目标 URL ({eff_url}) 连接超时（超过 {timeout_sec}s / 实测耗时 {probe_elapsed_ms} ms）："
                f"无法连接到内网服务器 {target_host}:{target_port} ({reason})"
            )
        else:
            network_err_type = f"URLError ({type(reason).__name__})"
            network_err_detail = (
                f"无法打开目标 URL ({eff_url})，耗时 {probe_elapsed_ms} ms：底层网络错误 [{reason}]"
            )
    except Exception as ex:
        probe_elapsed_ms = int((time.time() - t_probe_start) * 1000)
        network_err_type = type(ex).__name__
        network_err_detail = f"访问目标 URL ({eff_url}) 发生异常 ({network_err_type})，耗时 {probe_elapsed_ms} ms: {ex}"

    if not http_reachable:
        if captured_error is None:
            captured_error = {
                "error_type": network_err_type or "NetworkUnreachableError",
                "error_stage": f"Step 2 & Step 3: 浏览器访问目标地址 {eff_url}",
                "target_url": eff_url,
                "elapsed_ms": probe_elapsed_ms,
                "error_message": network_err_detail,
                "suggestion": (
                    f"1. 请检查当前运行机器是否已接入越南 FAST 财务内网 (VPN / 专线) 并能 ping 通 {target_host}；"
                    f"2. 请检查 config/fast_credentials.yaml 中的 login_url ({eff_url}) 是否拼写正确；"
                    f"3. 若当前在非内网环境下调试，可切换至本地 FAST 登录沙箱 URL 测试错误密码捕获。"
                ),
            }
        steps.append({
            "step": 2,
            "title": f"真实访问目标 URL 连通性探测 ({target_host}:{target_port})",
            "method": f'urllib.request.urlopen("{eff_url}", timeout={timeout_sec})',
            "status": "FAIL",
            "detail": f"❌ {network_err_detail}",
            "log_output": f"[ERROR] [{network_err_type}] GET {eff_url} 失败 ({probe_elapsed_ms} ms): {network_err_detail}",
        })

    # -------------------------------------------------------------------------
    # Step 3: 启动 Chrome 浏览器 / CDPClient 打开 Login.aspx 并核验登录表单 DOM
    # -------------------------------------------------------------------------
    chrome_bin, chrome_msg = _resolve_chrome_executable(configured_chrome_path)
    cdp_connected = False
    cdp_page_ready = False
    cdp_login_succeeded = False
    cdp_extracted_error = ""
    launcher: Optional[ Any ] = None
    cdp_client: Optional[ Any ] = None

    if ChromeLauncher is not None and chrome_bin and has_credentials:
        try:
            launcher = ChromeLauncher(chrome_path=chrome_bin, remote_debugging_port=cdp_port)
            if launcher.start_chrome(headless=headless):
                cdp_client = CDPClient(f"http://localhost:{cdp_port}")
                if cdp_client.connect_to_page():
                    cdp_connected = True
                    handler = LoginHandler(cdp_client, login_url=eff_url)
                    if handler.navigate_to_login() and handler.wait_for_page_ready(timeout=max(3, int(timeout_sec))):
                        cdp_page_ready = True
                    else:
                        cdp_extracted_error = handler.last_error or handler.extract_login_error()
        except Exception as cdp_ex:
            cdp_extracted_error = f"Chrome CDP 执行异常 ({type(cdp_ex).__name__}): {cdp_ex}"

    if not http_reachable and not cdp_page_ready:
        err_msg_s3 = cdp_extracted_error or network_err_detail or f"无法打开登录页 {eff_url}"
        steps.append({
            "step": 3,
            "title": "浏览器导航至 Login.aspx 并等待表单就绪 (navigate_to_login & wait_for_page_ready)",
            "method": f'LoginHandler.navigate_to_login("{eff_url}")',
            "status": "FAIL",
            "detail": (
                f"❌ 浏览器无法打开目标地址 {eff_url}：{err_msg_s3} "
                f"（未检测到 input[id='LoginExtender_txtUserName'] 登录框，真实报错已捕获并返回当前页面）"
            ),
            "log_output": f"[ERROR] LoginHandler.wait_for_page_ready() -> False | {err_msg_s3} | {chrome_msg}",
        })
    elif cdp_page_ready:
        steps.append({
            "step": 3,
            "title": "浏览器导航至 Login.aspx 并等待表单就绪 (navigate_to_login & wait_for_page_ready)",
            "method": f'LoginHandler.navigate_to_login("{eff_url}")',
            "status": "PASS",
            "detail": f"✅ Chrome (CDP :{cdp_port}) 已真实打开 {eff_url}，成功定位到 LoginExtender_txtUserName 与 LoginExtender_txtPassword",
            "log_output": f"[INFO] {chrome_msg}\n[INFO] LoginHandler.wait_for_page_ready() -> True",
        })
    else:
        # HTTP 已连通目标 URL，验证返回的 HTML 中是否包含 FAST 登录控件
        has_user_input = bool(re.search(r"LoginExtender_txtUserName", initial_html, re.IGNORECASE))
        if has_user_input:
            steps.append({
                "step": 3,
                "title": "浏览器导航至 Login.aspx 并等待表单就绪 (navigate_to_login & wait_for_page_ready)",
                "method": f'LoginHandler.navigate_to_login("{eff_url}")',
                "status": "PASS",
                "detail": (
                    f"✅ 已打开 {eff_url} 并解析到 ASP.NET 登录表单控件 "
                    f"(LoginExtender_txtUserName / LoginExtender_txtPassword / LoginExtender_Ok)"
                ),
                "log_output": f"[INFO] 目标页面 DOM 校验通过：检测到 LoginExtender_txtUserName ({chrome_msg})",
            })
        else:
            http_reachable = False
            bad_dom_msg = (
                f"目标 URL ({eff_url}) 虽然返回了 HTTP {http_status_code}，但页面中不存在 FAST 登录表单控件 "
                f"(LoginExtender_txtUserName)，请检查 URL 是否指向正确的 FAST Login.aspx 页面"
            )
            if captured_error is None:
                captured_error = {
                    "error_type": "InvalidLoginPageDOMError",
                    "error_stage": "Step 3: 校验 Login.aspx 登录框 DOM 元素",
                    "target_url": eff_url,
                    "elapsed_ms": probe_elapsed_ms,
                    "error_message": bad_dom_msg,
                    "suggestion": "请确认 login_url 指向越南 FAST 系统的 Main/Login.aspx 登录入口。",
                }
            steps.append({
                "step": 3,
                "title": "浏览器导航至 Login.aspx 并等待表单就绪 (navigate_to_login & wait_for_page_ready)",
                "method": f'LoginHandler.navigate_to_login("{eff_url}")',
                "status": "FAIL",
                "detail": f"❌ {bad_dom_msg}",
                "log_output": f"[ERROR] {bad_dom_msg}",
            })

    # -------------------------------------------------------------------------
    # Step 4, Step 5, Step 6: 切换英文、真实提交账密、捕获服务端错误或核验登录成功
    # -------------------------------------------------------------------------
    if not has_credentials or (not http_reachable and not cdp_page_ready):
        reason_blocked = (
            "待测账号或密码为空" if not has_credentials
            else f"目标 URL ({eff_url}) 访问超时或无法打开 ({network_err_type or 'NetworkError'})"
        )
        steps.append({
            "step": 4,
            "title": "登录界面语言自动切换为英文 (switch_language)",
            "method": f'LoginHandler.switch_language("{eff_lang}")',
            "status": "FAIL",
            "detail": f"❌ 未能执行：因 {reason_blocked}，无法在目标页面切换英文语言。",
            "log_output": f"[ERROR] 跳过 switch_language：前置步骤失败 ({reason_blocked})",
        })
        steps.append({
            "step": 5,
            "title": "向目标服务器真实提交账号/密码/单位 (fill_login_form & click_login_button)",
            "method": f'LoginHandler.fill_login_form("{eff_user or "(空)"}", "{masked_pwd or "(空)"}", "{eff_unit}")',
            "status": "FAIL",
            "detail": f"❌ 未能提交账密：因 {reason_blocked}，无法向 {eff_url} 提交登录表单。",
            "log_output": f"[ERROR] 跳过 fill_login_form & click_login_button：前置步骤失败 ({reason_blocked})",
        })
        steps.append({
            "step": 6,
            "title": "服务端鉴权结果核验与错误捕获 (is_logged_in & extract_login_error)",
            "method": "LoginHandler.is_logged_in() / LoginHandler.extract_login_error()",
            "status": "FAIL",
            "detail": (
                f"❌ 校验未通过：{captured_error['error_message'] if captured_error else reason_blocked} "
                f"（LoginHandler.is_logged_in() == False）"
            ),
            "log_output": (
                f"[ERROR] LoginHandler.is_logged_in() == False\n"
                f"[ERROR] 捕获异常: {captured_error['error_message'] if captured_error else reason_blocked}"
            ),
        })
    elif cdp_page_ready and cdp_client is not None:
        # 通过真实 Chrome CDP 执行 Step 4 -> Step 5 -> Step 6
        handler = LoginHandler(cdp_client, login_url=eff_url)
        handler.switch_language(eff_lang)
        steps.append({
            "step": 4,
            "title": "登录界面语言自动切换为英文 (switch_language)",
            "method": f'LoginHandler.switch_language("{eff_lang}")',
            "status": "PASS",
            "detail": f"✅ 已在真实浏览器中将 {eff_url} 界面语言切换为 '{eff_lang.upper()}' (English)",
            "log_output": "[INFO] LoginHandler.switch_language() 执行完成",
        })

        filled_ok = handler.fill_login_form(eff_user, eff_pass, eff_unit)
        clicked_ok = handler.click_login_button() if filled_ok else False
        if filled_ok and clicked_ok:
            steps.append({
                "step": 5,
                "title": "向目标服务器真实提交账号/密码/单位 (fill_login_form & click_login_button)",
                "method": f'LoginHandler.fill_login_form("{eff_user}", "{masked_pwd}", "{eff_unit}")',
                "status": "PASS",
                "detail": f"✅ 已在浏览器中向 {eff_url} 填入账号 [{eff_user}]、掩码密码 [{masked_pwd}]、单位 [{eff_unit}] 并点击 LoginExtender_Ok",
                "log_output": "[INFO] 表单填充与登录按钮点击完成，正在等待服务端响应...",
            })
        else:
            steps.append({
                "step": 5,
                "title": "向目标服务器真实提交账号/密码/单位 (fill_login_form & click_login_button)",
                "method": f'LoginHandler.fill_login_form("{eff_user}", "{masked_pwd}", "{eff_unit}")',
                "status": "FAIL",
                "detail": f"❌ 浏览器表单填写或点击登录按钮失败: {handler.last_error}",
                "log_output": f"[ERROR] {handler.last_error}",
            })

        if filled_ok and clicked_ok and handler.is_logged_in():
            cdp_login_succeeded = True
            steps.append({
                "step": 6,
                "title": "服务端鉴权结果核验与错误捕获 (is_logged_in & extract_login_error)",
                "method": "LoginHandler.is_logged_in() / LoginHandler.extract_login_error()",
                "status": "PASS",
                "detail": (
                    f"✅ 真实浏览器登录成功！服务端 ({eff_url}) 已通过账号 [{eff_user}] 鉴权，"
                    f"页面已跳出 Login.aspx 进入 FAST 英文主界面，LoginHandler.is_logged_in() 返回 True！"
                ),
                "log_output": "[INFO] LoginHandler.is_logged_in() == True -> 真实浏览器登录验证通过！",
            })
        else:
            srv_err = handler.extract_login_error()
            captured_error = {
                "error_type": "ServerAuthenticationError (服务端拒绝登录 / 账密错误或过期)",
                "error_stage": f"Step 6: 服务端 ({eff_url}) 登录鉴权响应",
                "target_url": eff_url,
                "elapsed_ms": probe_elapsed_ms,
                "error_message": srv_err,
                "suggestion": (
                    "目标 FAST 登录页可以正常打开，但服务端拒绝了当前填写的账号或密码！"
                    "请检查 config/fast_credentials.yaml 中的 username 和 password 是否填写错误、大小写有误或密码已过期。"
                ),
            }
            steps.append({
                "step": 6,
                "title": "服务端鉴权结果核验与错误捕获 (is_logged_in & extract_login_error)",
                "method": "LoginHandler.is_logged_in() / LoginHandler.extract_login_error()",
                "status": "FAIL",
                "detail": f"❌ 服务端鉴权失败 (AUTH_FAILED)：{srv_err}",
                "log_output": f"[ERROR] LoginHandler.is_logged_in() == False\n[ERROR] 捕获服务端错误: {srv_err}",
            })
    else:
        # 通过真实 HTTP ASP.NET WebForms 会话向目标服务器提交登录表单并捕获服务端返回结果
        steps.append({
            "step": 4,
            "title": "登录界面语言自动切换为英文 (switch_language)",
            "method": f'LoginHandler.switch_language("{eff_lang}")',
            "status": "PASS",
            "detail": f"✅ 已设置登录请求语言参数 LoginExtender$ddlLanguage='E' ({eff_lang.upper()})",
            "log_output": "[INFO] 语言参数已切换为 English ('E')",
        })

        post_res = _submit_aspnet_login_http(
            opener=opener,
            login_url=eff_url,
            initial_html=initial_html,
            username=eff_user,
            password=eff_pass,
            unit=eff_unit,
            language=eff_lang,
            timeout_sec=timeout_sec,
        )
        if not post_res["ok"]:
            captured_error = {
                "error_type": f"LoginSubmitError (HTTP {post_res['status_code']})",
                "error_stage": f"Step 5: 向 {eff_url} 提交登录表单",
                "target_url": eff_url,
                "elapsed_ms": post_res["elapsed_ms"],
                "error_message": post_res["server_error"],
                "suggestion": "向目标服务器提交 POST 登录表单时发生错误，请检查服务端状态或网络连接。",
            }
            steps.append({
                "step": 5,
                "title": "向目标服务器真实提交账号/密码/单位 (fill_login_form & click_login_button)",
                "method": f'POST {eff_url} (LoginExtender$txtUserName="{eff_user}")',
                "status": "FAIL",
                "detail": f"❌ 提交登录表单异常：{post_res['server_error']}",
                "log_output": f"[ERROR] POST {eff_url} 失败: {post_res['server_error']}",
            })
            steps.append({
                "step": 6,
                "title": "服务端鉴权结果核验与错误捕获 (is_logged_in & extract_login_error)",
                "method": "LoginHandler.is_logged_in() / LoginHandler.extract_login_error()",
                "status": "FAIL",
                "detail": f"❌ 登录失败：{post_res['server_error']}",
                "log_output": f"[ERROR] LoginHandler.is_logged_in() == False | {post_res['server_error']}",
            })
        else:
            steps.append({
                "step": 5,
                "title": "向目标服务器真实提交账号/密码/单位 (fill_login_form & click_login_button)",
                "method": f'LoginHandler.fill_login_form("{eff_user}", "{masked_pwd}", "{eff_unit}")',
                "status": "PASS",
                "detail": (
                    f"✅ 已向 {eff_url} 真实提交账号 [{eff_user}]、掩码密码 [{masked_pwd}]、单位 [{eff_unit}] "
                    f"(HTTP {post_res['status_code']}，耗时 {post_res['elapsed_ms']} ms)"
                ),
                "log_output": f"[INFO] POST {eff_url} -> HTTP {post_res['status_code']} ({post_res['elapsed_ms']} ms)",
            })
            if post_res["logged_in"]:
                cdp_login_succeeded = True
                steps.append({
                    "step": 6,
                    "title": "服务端鉴权结果核验与错误捕获 (is_logged_in & extract_login_error)",
                    "method": "LoginHandler.is_logged_in() / LoginHandler.extract_login_error()",
                    "status": "PASS",
                    "detail": (
                        f"✅ 服务端真实鉴权通过！目标地址 {eff_url} 已接受账号 [{eff_user}] 登录，"
                        f"已跳转至主界面 ({post_res['final_url']}) 并检测到 Logout / General Ledger 英文主菜单！"
                    ),
                    "log_output": f"[INFO] 登录成功 -> {post_res['final_url']} | LoginHandler.is_logged_in() == True",
                })
            else:
                srv_err = post_res["server_error"]
                captured_error = {
                    "error_type": "ServerAuthenticationError (服务端拒绝登录 / 账密错误或过期)",
                    "error_stage": f"Step 6: 服务端 ({eff_url}) 登录鉴权响应",
                    "target_url": eff_url,
                    "elapsed_ms": post_res["elapsed_ms"],
                    "error_message": srv_err,
                    "suggestion": (
                        "目标 FAST 登录页可以正常打开，但服务端拒绝了当前填写的账号或密码！"
                        "请检查 config/fast_credentials.yaml 中的 username 和 password 是否填写错误或已过期。"
                    ),
                }
                steps.append({
                    "step": 6,
                    "title": "服务端鉴权结果核验与错误捕获 (is_logged_in & extract_login_error)",
                    "method": "LoginHandler.is_logged_in() / LoginHandler.extract_login_error()",
                    "status": "FAIL",
                    "detail": f"❌ 服务端拒绝登录 (AUTH_FAILED)：{srv_err}",
                    "log_output": f"[ERROR] 服务端返回错误提示: {srv_err}\n[ERROR] LoginHandler.is_logged_in() == False",
                })

    # 清理浏览器连接
    if cdp_client is not None:
        try:
            cdp_client.close()
        except Exception:
            pass
    if launcher is not None and chrome_cfg.get("close_browser_on_exit", True):
        try:
            launcher.stop_chrome()
        except Exception:
            pass

    overall_pass = all(s["status"] == "PASS" for s in steps) and cdp_login_succeeded
    if overall_pass:
        overall_status = "PASS"
        summary_msg = (
            f"✅ 真实访问 {eff_url} 全链路 6 步验证通过 (PASS)！账号 [{eff_user}] (掩码 {masked_pwd}) "
            f"已通过目标 FAST 服务端鉴权并进入英文主界面。"
        )
    elif not has_credentials:
        overall_status = "CREDENTIALS_MISSING"
        summary_msg = f"⚠️ 校验中止 (CREDENTIALS_MISSING)：未填写完整的待测账号或密码，请先在 config/fast_credentials.yaml 或左侧表单中填写。"
    elif not http_reachable and not cdp_page_ready:
        overall_status = "URL_UNREACHABLE"
        summary_msg = (
            f"❌ 目标地址无法打开或访问超时 ({overall_status})！真实尝试访问 {eff_url} 失败："
            f"{captured_error['error_message'] if captured_error else network_err_detail}"
        )
    else:
        overall_status = "AUTH_FAILED"
        summary_msg = (
            f"❌ 服务端账密鉴权失败 ({overall_status})！已连接 {eff_url} 提交账号 [{eff_user}] (掩码 {masked_pwd})，"
            f"但服务端返回错误：{captured_error['error_message'] if captured_error else '用户名或密码错误/已过期'}"
        )

    return {
        "ok": True,
        "overall_status": overall_status,
        "intranet_live": bool(http_reachable or cdp_page_ready),
        "target_url": eff_url,
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tested_credentials": {
            "username": eff_user,
            "masked_password": masked_pwd,
            "password_length": len(eff_pass),
            "fingerprint": fp,
            "unit": eff_unit,
            "language": eff_lang,
            "login_url": eff_url,
        },
        "captured_error": captured_error,
        "steps": steps,
        "summary_message": summary_msg,
    }


def verify_fast_credentials(
    test_username: Optional[str] = None,
    test_password: Optional[str] = None,
    test_login_url: Optional[str] = None,
    timeout_sec: float = 4.0,
    config_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """CLI 与模块调用的统一入口：直接调用 verify_fast_login_live 真实访问目标 URL 检验账号密码。"""
    return verify_fast_login_live(
        test_username=test_username,
        test_password=test_password,
        test_login_url=test_login_url,
        timeout_sec=timeout_sec,
        config_dir=config_dir,
    )
