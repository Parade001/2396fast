import time
from typing import Dict, Any
from utils import CDPClient, setup_logger


class LoginHandler:
    def __init__(self, cdp_client: CDPClient, login_url: str = ""):
        self.cdp = cdp_client
        self.login_url = login_url or "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx"
        self.logger = setup_logger(__name__)

    def navigate_to_login(self) -> bool:
        self.logger.info(f"正在导航到登录页面: {self.login_url}")
        return self.cdp.navigate(self.login_url)

    def wait_for_page_ready(self, timeout: int = 30) -> bool:
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

    def fill_login_form(self, username: str, password: str, unit: str) -> bool:
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

        return username_filled and password_filled

    def click_login_button(self) -> bool:
        self.logger.info("正在点击登录按钮...")

        selectors = [

            "button[id='LoginExtender_Ok']",
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
                    if (btn.value && (btn.value.toLowerCase().indexOf('login') >= 0 || 
                                     btn.value.toLowerCase().indexOf('登录') >= 0 ||
                                     btn.value.toLowerCase().indexOf('ok') >= 0)) {
                        btn.click();
                        return true;
                    }
                    if (btn.innerText && (btn.innerText.toLowerCase().indexOf('login') >= 0 || 
                                         btn.innerText.toLowerCase().indexOf('登录') >= 0 ||
                                         btn.innerText.toLowerCase().indexOf('ok') >= 0)) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            })()
        """
        result = self.cdp.execute_script(script)
        if result:
            self.logger.info("通过 JavaScript 点击登录按钮 ")
            time.sleep(3)
            return True

        return False

    def is_logged_in(self) -> bool:
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
            "document.body.innerText.indexOf('欢迎') >= 0"
        )
        if welcome_text:
            return True

        return False

    def login(self, username: str, password: str, unit: str) -> bool:
        if not self.navigate_to_login():
            self.logger.info("导航到登录页面失败")
            return False

        if not self.wait_for_page_ready():
            self.logger.info("页面加载超时")
            return False

        if not self.fill_login_form(username, password, unit):
            self.logger.info("填写登录表单失败")
            return False

        if not self.click_login_button():
            self.logger.info("点击登录按钮失败")
            return False

        time.sleep(3)

        if self.is_logged_in():
            self.logger.info("登录成功")
            return True
        else:
            self.logger.info("登录可能失败，请检查")
            return False
