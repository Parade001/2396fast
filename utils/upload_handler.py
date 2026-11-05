#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/upload_handler.py
模块定位: 越南 FAST 财务系统网页端 General Voucher 凭证模板导入上传处理器
================================================================================
【本文件核心作用】
1. 导航至越南 FAST 系统的凭证上传页面：`General` -> `General Voucher` (`navigate_to_upload_page`)；
2. 点击工具栏上的导入按钮 (`#ctl00_FastBusiness_MainReport_ToolbarButton_ImportData`) 弹出上传对话框；
3. 检测上传弹窗是否就绪 (`find_upload_window`)；
4. 调用 `attach_upload_file_cdp(excel_file)`，通过 Chrome CDP 原生协议 `DOM.setFileInputFiles`
   将 43 列 FAST 导入模板文件绑定至 `<input type="file">`，随后点击弹窗确定按钮 (`#..._updateDlgOk`)
   并轮询等待弹窗消失以确认上传完成 (`process_posting`)。

【1104 版本关键修改与修复说明】
- 对可选桌面 GUI 库 `pyperclip` 和 `pyautogui` 增加了 `try...except Exception` 导入守卫：
  因为实际生产上传 `upload_file()` 走的是纯 CDP 协议方法 `attach_upload_file_cdp()`，并不需要
  X11/桌面环境；增加守卫后，无论在 Windows 锁屏后台还是 Linux 容器无头环境中导入本模块均不会报错。
================================================================================
"""

import time
import logging
from utils import CDPClient

# 1104 修复：对可选桌面 GUI 辅助库增加异常捕获守卫，确保无头环境与服务器环境导入不崩溃
try:
    import pyperclip
except Exception:
    pyperclip = None

try:
    import pyautogui as pg
except Exception:
    pg = None


class UploadHandler:
    """越南 FAST 网页端 General Voucher 43 列模板文件自动上传处理器。"""

    def __init__(self, cdp_client: CDPClient):
        """
        初始化上传处理器。
        :param cdp_client: 已连接的 CDPClient 实例
        """
        self.cdp = cdp_client
        self.logger = logging.getLogger(__name__)

    def navigate_to_upload_page(self) -> bool:
        """依次点击 General 菜单与 General Voucher 子菜单，进入凭证导入页面。"""
        self.logger.info("正在导航到上传页面...")

        general_clicked = self.cdp.click_element(
            "a[id*='General'], a[href*='General'], span:contains('General'), " +
            "a:contains('General'), li:contains('General')"
        )

        if not general_clicked:
            script = """
                (function() {
                    var links = document.querySelectorAll('a, span, li');
                    for (var i = 0; i < links.length; i++) {
                        if (links[i].innerText && links[i].innerText.indexOf('General') >= 0 ) {
                            links[i].click();
                            return true;
                        }
                    }
                    return false;
                })()
            """
            general_clicked = self.cdp.execute_script(script)

        if general_clicked:
            print("已点击 General 菜单")
            time.sleep(2)

        general_voucher_clicked = self.cdp.click_element(
            "a:contains('General Voucher')"
        )

        if not general_voucher_clicked:
            script = """
                (function() {
                    var links = document.querySelectorAll('a, button');
                    for (var i = 0; i < links.length; i++) {
                        if (links[i].innerText && links[i].innerText.indexOf('General Voucher') >= 0 ) {
                            links[i].click();
                            return true;
                        }
                    }
                    return false;
                })()
            """
            general_voucher_clicked = self.cdp.execute_script(script)

        if general_voucher_clicked:
            print("已点击 General Voucher 按钮")
            time.sleep(3)
            return True

        print("导航到上传页面失败")
        return False

    def enter_import_dialog(self) -> None:
        """点击工具栏上的 Import Data 按钮打开 Excel 文件上传弹窗。"""
        self.logger.info("进入上传界面...")

        script = """
            (function() {
                var div = document.getElementById('ctl00_FastBusiness_MainReport_ToolbarButton_ImportData');
                if (div) {
                    div.click();
                    return 'clicked_import_data';
                }
                return null;
            })()
        """
        try:
            self.cdp.execute_script(script)
        except Exception as e:
            self.logger.error(f"点击 Import Data 按钮失败: {e}")

    def find_upload_window(self) -> bool:
        """检测上传模态弹窗 (`#ctl00_FastBusiness_MainReport_formExtenderGLMasterImport_updateDlgTitleText`) 是否处于打开状态。"""
        script = """
            (function() {
                var div = document.querySelector("div#ctl00_FastBusiness_MainReport_formExtenderGLMasterImport_updateDlgTitleText");
                return div !== null;
            })()
        """
        result = self.cdp.execute_script(script)
        print(f'查找上传窗口结果: {result}')

        while result is None:
            # 若 WebSocket 瞬断则自动重连页面
            self.cdp.connect_to_page()
            time.sleep(2)
            result = self.cdp.execute_script(script)

        return bool(result)

    def attach_upload_file(self, excel_file: str) -> bool:
        """备用传统桌面 GUI 方式上传文件（仅在桌面有界面且安装了 pyautogui 时使用）。"""
        if pg is None or pyperclip is None:
            raise RuntimeError("当前环境未安装 pyautogui/pyperclip，请使用 attach_upload_file_cdp 纯协议上传方法")
        if not self.find_upload_window():
            self.logger.error("未找到上传窗口")
            raise RuntimeError("未找到上传窗口")

        self.logger.info(f"正在上传文件: {excel_file}")
        pos = pg.locateCenterOnScreen('select.png', confidence=0.8)
        if pos is None:
            self.logger.error("未找到文件上传控件 (Select...")
            raise Exception("未找到文件上传控件")
        pg.click(pos)
        self.logger.info("已点击文件上传控件，等待对话框弹出...")
        time.sleep(1)
        pg.moveTo(pos[0], pos[1] + 50)
        pyperclip.copy(excel_file)
        self.logger.info(f"已将文件路径复制到剪贴板: {excel_file}")
        time.sleep(0.5)
        pg.hotkey('ctrl', 'v')
        time.sleep(1)
        pg.press('enter')
        return True

    def attach_upload_file_cdp(self, excel_file: str) -> bool:
        """
        【推荐主路径】使用 Chrome CDP 原生协议 (`DOM.setFileInputFiles`) 直接挂载待上传 Excel 文件。
        不受屏幕分辨率、窗口遮挡或系统锁屏影响。
        """
        while not self.find_upload_window():
            time.sleep(2)

        return self.cdp.attach_upload_file(file_path=excel_file)

    def process_posting(self) -> bool:
        """点击上传弹窗的确定提交按钮 (`#..._updateDlgOk`)，并轮询等待上传弹窗关闭（最长等待 900 秒）。"""
        self.logger.info("正在点击提交按钮...")

        try:
            self.cdp.send_command(
                method="Runtime.evaluate",
                params={
                    "expression": """
                        (function() {
                            var button = document.querySelector("#ctl00_FastBusiness_MainReport_formExtenderGLMasterImport_updateDlgOk");
                            if (button) {
                                button.click();
                                return 'clicked_ok_button';
                            }
                            return 'button_not_found';
                        })()
                    """,
                    "returnByValue": True
                }
            )

            self.logger.info("已点击提交按钮，等待上传完成...")
            now = time.time()
            while self.find_upload_window():
                time.sleep(5)
                if time.time() - now > 900:
                    self.logger.error("上传超时")
                    raise Exception("上传超时")
            self.logger.info("上传完成！")
            return True
        except Exception as e:
            self.logger.error(f"提交失败: {e}")
            return False

    def upload_file(self, excel_file: str) -> bool:
        """
        单文件完整上传闭环：
        1. 点击 Import Data 打开上传弹窗；
        2. 通过 CDP 注入本地 Excel 文件路径；
        3. 点击 OK 提交并等待 FAST 解析入库完成。
        """
        self.enter_import_dialog()
        self.attach_upload_file_cdp(excel_file)
        return self.process_posting()