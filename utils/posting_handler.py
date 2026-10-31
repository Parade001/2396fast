import time
from typing import List, Dict, Any
from utils import CDPClient


class PostingHandler:
    def __init__(self, cdp_client: CDPClient):
        self.cdp = cdp_client

    def navigate_to_posting_page(self) -> bool:
        print("正在导航到过帐页面...")

        general_clicked = self.cdp.click_element(
            "a[id*='General'], a[href*='General'], span:contains('General'), " +
            "a:contains('General'), li:contains('General')"
        )

        if not general_clicked:
            script = """
                (function() {
                    var links = document.querySelectorAll('a, span, li');
                    for (var i = 0; i < links.length; i++) {
                        if (links[i].innerText && 
                            (links[i].innerText.indexOf('General') >= 0 ||
                             links[i].innerText.indexOf('常规') >= 0)) {
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

        post_transaction_clicked = self.cdp.click_element(
            "a[id*='PostTransaction'], a[href*='PostTransaction'], " +
            "a:contains('Post Transaction'), a:contains('过帐')"
        )

        if not post_transaction_clicked:
            script = """
                (function() {
                    var links = document.querySelectorAll('a, button');
                    for (var i = 0; i < links.length; i++) {
                        if (links[i].innerText && 
                            (links[i].innerText.indexOf('Post Transaction') >= 0 ||
                             links[i].innerText.indexOf('Post') >= 0 ||
                             links[i].innerText.indexOf('过帐') >= 0)) {
                            links[i].click();
                            return true;
                        }
                    }
                    return false;
                })()
            """
            post_transaction_clicked = self.cdp.execute_script(script)

        if post_transaction_clicked:
            print("已点击 Post Transaction 按钮")
            time.sleep(3)
            return True

        print("导航到过帐页面失败")
        return False

    def find_checkboxes_in_table(self) -> List[Dict[str, Any]]:
        print("正在查找表格中的复选框...")

        # 查找特定id的input控件
        script = """
            (function() {
                var checkboxes = [];
                var input = document.getElementById('ctl00_FastBusiness_MainReport_inputCell_2.1');
                
                if (input) {
                    checkboxes.push({
                        checkboxId: input.id,
                        checkboxName: input.name,
                        tagName: input.tagName,
                        type: input.type
                    });
                }
                
                return checkboxes;
            })()
        """

        result = self.cdp.execute_script(script)
        return result if result else []

    def click_all_checkboxes(self) -> int:
        print("正在点击全选按钮...")

        # 首先尝试查找 class=HeaderArrowImage 且 innerText=Select 的 div 元素
        script = """
            (function() {
                // 查找 class=HeaderArrowImage 且 innerText=Select 的 div 元素
                var divs = document.querySelectorAll('div.HeaderArrowImage');
                for (var i = 0; i < divs.length; i++) {
                    var text = (divs[i].innerText || divs[i].textContent || '').trim();
                    if (text === 'Select') {
                        divs[i].click();
                        return 'clicked_header_arrow';
                    }
                }
                return null;
            })()
        """

        result = self.cdp.execute_script(script)

        if result == 'clicked_header_arrow':
            print("已点击 Select 全选按钮 (HeaderArrowImage)")
            time.sleep(1)
            # 获取选中的复选框数量
            count_script = """
                (function() {
                    var checkboxes = document.querySelectorAll('table input[type="checkbox"]');
                    var checked = 0;
                    for (var i = 0; i < checkboxes.length; i++) {
                        if (checkboxes[i].checked) {
                            checked++;
                        }
                    }
                    return checked;
                })()
            """
            checked_count = self.cdp.execute_script(count_script) or 0
            print(f"已全选 {checked_count} 个复选框")
            return checked_count

        # 如果找不到 HeaderArrowImage div，回退到原来的逐个点击方式
        print("未找到 Select 全选按钮，使用逐个点击方式...")

        script = """
            (function() {
                var checkboxes = document.querySelectorAll('table input[type="checkbox"]');
                var clicked = 0;
                for (var i = 0; i < checkboxes.length; i++) {
                    if (!checkboxes[i].checked) {
                        checkboxes[i].click();
                        clicked++;
                    }
                }
                return clicked;
            })()
        """

        result = self.cdp.execute_script(script)
        clicked_count = result if isinstance(result, int) else 0

        if clicked_count > 0:
            print(f"已点击 {clicked_count} 个复选框")
        else:
            print("没有找到需要点击的复选框")

        time.sleep(1)
        return clicked_count

    def click_post_to_gl_button(self) -> bool:
        print("正在点击 Post TO GL 按钮...")

        # 首先尝试使用指定的 div ID 和 innerText 查找
        script = """
            (function() {
                var div = document.getElementById('ctl00_FastBusiness_MainReport_ToolbarButton_GLPost');
                if (div) {
                    var text = (div.innerText || div.textContent || '').trim();
                    if (text === 'Post to GL') {
                        div.click();
                        return 'clicked_specific_div';
                    }
                }
                return null;
            })()
        """

        result = self.cdp.execute_script(script)

        if result == 'clicked_specific_div':
            print("已点击 Post to GL 按钮 (ctl00_FastBusiness_MainReport_ToolbarButton_GLPost)")
            time.sleep(3)
            return True

        # 回退到原来的选择器方式
        selectors = [
            "input[value*='Post TO GL']",
            "input[value*='Post To GL']",
            "input[value*='Post to GL']",
            "button:contains('Post TO GL')",
            "button:contains('Post To GL')",
            "a:contains('Post TO GL')",
            "input[id*='PostGL']",
            "button[id*='PostGL']"
        ]

        for selector in selectors:
            if self.cdp.click_element(selector):
                print(f"使用选择器点击 Post TO GL 按钮: {selector}")
                time.sleep(3)
                return True

        # 回退到通用的 JavaScript 查找方式
        script = """
            (function() {
                var elements = document.querySelectorAll('input, button, a, div');
                for (var i = 0; i < elements.length; i++) {
                    var el = elements[i];
                    var text = (el.value || el.innerText || '').toLowerCase();
                    if (text.indexOf('post') >= 0 && text.indexOf('gl') >= 0) {
                        el.click();
                        return true;
                    }
                }
                return false;
            })()
        """
        result = self.cdp.execute_script(script)
        if result:
            print("通过 JavaScript 点击 Post TO GL 按钮")
            time.sleep(3)
            return True

        print("未找到 Post TO GL 按钮")
        return False

    def has_more_records(self) -> bool:
        checkboxes = self.find_checkboxes_in_table()
        return len(checkboxes) > 0

    def wait_for_processing(self, timeout: int = 30) -> bool:
        print("等待处理完成...")
        time.sleep(2)

        start_time = time.time()
        while time.time() - start_time < timeout:
            # 检查页面中是否存在 a 标签包含 "Refresh" 文本，且 td 标签包含 "Post Transaction" 文本
            processing_complete = self.cdp.execute_script("""
                (function() {
                    var hasRefresh = false;
                    var hasPostTransaction = false;
                    
                    // 查找 a 标签下包含 "Refresh" 文本的元素
                    var links = document.querySelectorAll('a');
                    for (var i = 0; i < links.length; i++) {
                        var text = (links[i].innerText || links[i].textContent || '').trim();
                        if (text.indexOf('Refresh') >= 0) {
                            hasRefresh = true;
                            break;
                        }
                    }
                    
                    // 查找 td 标签内包含 "Post Transaction" 文本的元素
                    var tds = document.querySelectorAll('td');
                    for (var j = 0; j < tds.length; j++) {
                        var text = (tds[j].innerText || tds[j].textContent || '').trim();
                        if (text === 'Post Transaction') {
                            hasPostTransaction = true;
                            break;
                        }
                    }
                    
                    // 两个条件同时满足时，说明处理完成
                    return hasRefresh && hasPostTransaction;
                })()
            """)

            if processing_complete:
                print("处理完成（检测到 Refresh 链接和 Post Transaction 文本）")
                return True

            time.sleep(1)

        print(f"等待处理完成超时（{timeout}秒）")
        return False

    def process_posting(self) -> bool:        

        if not self.has_more_records():
            print("没有更多需要过帐的记录")
            return False

        self.click_all_checkboxes()
        self.click_post_to_gl_button()
        print("点击 Post TO GL 按钮失败")    
        self.wait_for_processing()
        print("过帐处理完成")
        return True

    