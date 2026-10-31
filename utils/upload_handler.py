import time

from utils import CDPClient
import logging
import pyperclip
import pyautogui as pg

class UploadHandler:
    def __init__(self, cdp_client: CDPClient):
        self.cdp = cdp_client
        self.logger = logging.getLogger(__name__)
   

    def navigate_to_upload_page(self) -> bool:
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
        self.logger.info("进入上传界面...")

        # 首先尝试查找 id=ctl00_FastBusiness_MainReport_ToolbarButton_ImportData 的 div 元素
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
        
        # 查找特定id的div控件
        script = """
            (function() {
                var div = document.querySelector("div#ctl00_FastBusiness_MainReport_formExtenderGLMasterImport_updateDlgTitleText");
                return div !== null;
            })()
        """
        result = self.cdp.execute_script(script)       
        print(f'查找上传窗口结果: {result}')
        
        while result is None:
            # 重新建立CDP连接
            self.cdp.connect_to_page()
            time.sleep(2)
            result = self.cdp.execute_script(script)
        
        return bool(result)

    def attach_upload_file(self, excel_file : str) -> bool:
        if not self.find_upload_window():
            self.logger.error("未找到上传窗口")
            raise
        
        self.logger.info(f"正在上传文件: {excel_file}")
        # 点击文件上传input控件 
        pos=pg.locateCenterOnScreen('select.png',confidence=0.8)
        if pos is None:
            self.logger.error("未找到文件上传控件 (Select...")
            raise Exception("未找到文件上传控件")
        pg.click(pos)
        self.logger.info("已点击文件上传控件，等待对话框弹出...")
        time.sleep(1)
        pg.moveTo(pos[0],pos[1]+50)
        # 将文件路径复制到剪贴板
        pyperclip.copy(excel_file)
        self.logger.info(f"已将文件路径复制到剪贴板: {excel_file}")
        time.sleep(0.5)
        pg.hotkey('ctrl', 'v')

        time.sleep(1)
        pg.press('enter')       
        
        return True
     
    def attach_upload_file_cdp(self, excel_file : str) -> bool:
        while not self.find_upload_window():
            
            time.sleep(2)
            
        return self.cdp.attach_upload_file(file_path=excel_file)

    def process_posting(self) -> bool:        
        self.logger.info("正在点击提交按钮...")
        
        # 查找并点击提交按钮
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
            now=time.time()
            while self.find_upload_window():
                time.sleep(5)
                if time.time()-now>900:
                    self.logger.error("上传超时")
                    raise Exception("上传超时")
            self.logger.info("上传完成！")
            return True
        except Exception as e:
            self.logger.error(f"提交失败: {e}")
            return False

    def upload_file(self, excel_file : str) -> bool:
        self.enter_import_dialog()
        self.attach_upload_file_cdp(excel_file)
        
        return self.process_posting()
    