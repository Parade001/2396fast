#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/login_handler.py
模块定位: 越南 FAST 财务系统 Web 登录页面自动化填表、语言切换与登录态核验处理器
================================================================================
【本文件核心作用】
1. 负责驱动 Chrome CDP 客户端导航至越南 FAST 登录页（默认 `http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx`）；
2. 自动等待 ASP.NET 登录表单元素 (`LoginExtender_txtUserName`) 渲染就绪；
3. 自动检测并将默认越南语 (`Tiếng Việt`) 登录界面切换为英文界面 (`English` / `E`)，
   确保进入系统后的总账菜单 (`General Ledger` / `Journal Entry` / `Post to GL`) 与科目余额表 (`Trial Balance` TB表) 均为英文界面；
4. 支持多级凭据自动加载（命令行参数 > `.env` 环境变量 > `config/fast_credentials.yaml` > `config/account.yaml`），
   自动填写用户名 (`LoginExtender_txtUserName`)、密码 (`LoginExtender_txtPassword`) 与核算单位 (`CTY`)，
   并点击登录按钮 (`LoginExtender_Ok`)；
5. 通过检查 URL 跳转、注销按钮 (`Logout`) 或欢迎文本自动核验是否已完成登录 (`is_logged_in`)。

【1104 版本关键修改与修复说明】
1. 在 `__init__` 中显式初始化了 `self.logger = setup_logger(__name__)`，并支持自动从 `config/fast_credentials.yaml` 读取配置；
2. 新增 `switch_language(language="en")` 方法，针对 `http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx` 默认越南语界面自动切换为英语；
3. 该类的 `login(username, password, unit, language)` 完整闭环方法已在 1104 版中被正式接入到：
   - `sap2fast_full_fix.py` 的 `_perform_fast_login()` 辅助函数；
   - `modules/upload_to_gl.py` 的 `upload_to_fast()`；
   - `modules/post_to_gl.py` 的 `postToGl()`；
   从而实现了 CFO 需求 7 要求的“自动切英文 -> 自动填表登录 -> 循环上传 / 批量过账”全自动无人值守流水线。
================================================================================
"""

import os
import time
from pathlib import Path
from typing import Dict, Any, Optional
from utils import CDPClient, setup_logger

try:
    import yaml
except ImportError:
    yaml = None


def load_fast_credentials(config_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    按照最佳实践优先级加载越南 FAST 登录凭据与自动化配置：
    优先级：环境变量 (.env) > config/fast_credentials.yaml > config/account.yaml (chrome 节点)
    """
    base_dir = Path(config_dir) if config_dir else Path(__file__).parent.parent / "config"
    cred_yaml_path = base_dir / "fast_credentials.yaml"
    account_yaml_path = base_dir / "account.yaml"

    cred_data: Dict[str, Any] = {}
    if cred_yaml_path.exists() and yaml is not None:
        try:
            with open(cred_yaml_path, "r", encoding="utf-8") as f:
                cred_data = yaml.safe_load(f) or {}
        except Exception:
            cred_data = {}

    acc_chrome: Dict[str, Any] = {}
    if account_yaml_path.exists() and yaml is not None:
        try:
            with open(account_yaml_path, "r", encoding="utf-8") as f:
                acc_all = yaml.safe_load(f) or {}
                acc_chrome = acc_all.get("chrome") or {}
        except Exception:
            acc_chrome = {}

    fast_auth = cred_data.get("fast_auth") or {}
    chrome_cdp = cred_data.get("chrome_cdp") or {}
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
    return {
        "login_url": str(login_url).strip(),
        "username": str(username).strip(),
        "password": str(password),
        "unit": str(unit).strip() or "CTY",
        "language": str(language).strip().lower() or "en",
        "chrome_cdp": chrome_cdp,
        "selectors": selectors,
        "credential_file": str(cred_yaml_path),
    }


class LoginHandler:
    """越南 FAST 财务软件网页端登录处理器（支持自动切换英文界面与多源凭据读取）。"""

    def __init__(self, cdp_client: CDPClient, login_url: str = ""):
        """
        初始化登录处理器。
        :param cdp_client: 已连接的 CDPClient 实例
        :param login_url: 越南 FAST 登录页面地址（支持从环境变量 FAST_URL 或 fast_credentials.yaml 注入）
        """
        self.cdp = cdp_client
        self.creds = load_fast_credentials()
        # 默认指向越南内网 FAST 登录页，同时支持通过参数或配置文件覆盖
        self.login_url = login_url or self.creds.get("login_url") or "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"
        # 初始化日志记录器
        self.logger = setup_logger(__name__)

    def navigate_to_login(self) -> bool:
        """导航至越南 FAST 登录页面。"""
        self.logger.info(f"正在导航到登录页面: {self.login_url}")
        return self.cdp.navigate(self.login_url)

    def wait_for_page_ready(self, timeout: int = 30) -> bool:
        """轮询等待登录页面的 DOM 与用户名输入框 (`LoginExtender_txtUserName`) 加载完成。"""
        self.logger.info("等待页面加载完成...")
        start_time = time.time()
        while time.time() - start_time < timeout:
            ready_state = self.cdp.execute_script(
                "return document.readyState"
            )
            self.logger.info(f"  页面状态: {ready_state}")

            if ready_state == "complete" or ready_state == "interactive":
                username_exists = self.cdp.execute_script(
                    "return document.querySelector('input[id=\\'LoginExtender_txtUserName\\']') !== null"
                )
                if username_exists:
                    self.logger.info("  登录页面已就绪")
                    time.sleep(2)
                    return True

            time.sleep(1)

        self.logger.info("页面加载超时，但继续尝试")
        return True

    def switch_language(self, language: str = "en") -> bool:
        """
        将越南 FAST 登录界面从默认越南语 (Tiếng Việt) 切换为指定语言（默认英语 'en'）。
        兼容下拉框 (`LoginExtender_ddlLanguage` / `select[id*='Lang']`)、单选框 (`input[value='E']`)
        以及英文切换链接/国旗按钮 (`English` / `Tiếng Anh`)。
        """
        lang_norm = (language or "en").strip().lower()
        if lang_norm not in ("en", "eng", "english", "e"):
            self.logger.info(f"保持登录界面语言设置: {language}")
            return True

        self.logger.info("正在检测并将 FAST 登录界面切换为英文 (English)...")
        js_switch_en = """
        (function() {
            // 1. 尝试匹配语言下拉框 (ASP.NET LoginExtender 常见下拉或 select)
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
            // 2. 尝试匹配单选按钮或按钮 (value='E' 或文本含 English)
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

        # 1. 填写用户名（优先匹配标准 ID LoginExtender_txtUserName，回退首个文本框）
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

        # 2. 填写密码 (LoginExtender_txtPassword)
        password_filled = self.cdp.set_input_value(
            "input[id='LoginExtender_txtPassword']",
            password
        )

        time.sleep(0.5)

        # 3. 填写核算单位（如 CTY）
        unit_filled = self.cdp.set_input_value(
            "input[name='txtUnit'], input[id*='Unit'], input[id*='unit'], select[id*='Unit']",
            unit
        )

        self.logger.info(f"用户名填写: {'成功' if username_filled else '失败'}")
        self.logger.info(f"密码填写: {'成功' if password_filled else '失败'}")
        self.logger.info(f"单位填写: {'成功' if unit_filled else '失败'}")

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

        return False

    def is_logged_in(self) -> bool:
        """检查当前页面是否已成功跳出 Login.aspx 并进入 FAST 主界面。"""
        current_url = self.cdp.execute_script("return window.location.href")
        if current_url and "Login.aspx" not in current_url:
            return True

        logout_button = self.cdp.query_selector(
            "a[href*='Logout'], input[value*='Logout'], button[id*='Logout']"
        )
        if logout_button and logout_button.get("found"):
            return True

        welcome_text = self.cdp.execute_script(
            "return document.body.innerText.indexOf('Welcome') >= 0 || " +
            "document.body.innerText.indexOf('欢迎') >= 0 || " +
            "document.body.innerText.indexOf('Financials') >= 0 || " +
            "document.body.innerText.indexOf('General Ledger') >= 0"
        )
        if welcome_text:
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
        4. 填写用户名/密码/单位 (`CTY`) -> 点击登录 -> 校验登录成功状态。
        """
        effective_user = username if username is not None and username != "" else self.creds.get("username", "")
        effective_pwd = password if password is not None and password != "" else self.creds.get("password", "")
        effective_unit = unit or self.creds.get("unit", "CTY")
        effective_lang = language or self.creds.get("language", "en")

        if not self.navigate_to_login():
            self.logger.info("导航到登录页面失败")
            return False

        if not self.wait_for_page_ready():
            self.logger.info("页面加载超时")
            return False

        # 先将默认越南语界面切换为英文界面
        self.switch_language(effective_lang)

        if not effective_user or not effective_pwd:
            self.logger.info(
                "未在命令行、.env 或 config/fast_credentials.yaml 中检测到账号密码，请先在 config/fast_credentials.yaml 填写"
            )
            return False

        if not self.fill_login_form(effective_user, effective_pwd, effective_unit):
            self.logger.info("填写登录表单失败")
            return False

        if not self.click_login_button():
            self.logger.info("点击登录按钮失败")
            return False

        time.sleep(3)

        if self.is_logged_in():
            self.logger.info("登录成功（已进入英文主界面）")
            return True
        else:
            self.logger.info("登录可能失败，请检查账号密码或网络连接")
            return False
