#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/posting_handler.py
模块定位: 越南 FAST 财务系统网页端批量勾选与总账过账 (Post to GL) 自动化处理器
================================================================================
【本文件核心作用】
1. 导航至越南 FAST 系统的过账菜单：`General` -> `Post Transaction`；
2. 自动检测待过账凭证列表表格中是否存在未过账记录 (`find_checkboxes_in_table` / `has_more_records`)；
3. 自动点击表头 `Select` 全选按钮 (`div.HeaderArrowImage`) 或逐行勾选全部复选框 (`click_all_checkboxes`)；
4. 自动点击工具栏上的 `Post to GL` 按钮 (`ctl00_FastBusiness_MainReport_ToolbarButton_GLPost`)，
   并轮询等待页面出现 `Refresh` 与 `Post Transaction` 标志以确认当前批次过账完成 (`wait_for_processing`)。

【1104 版本关键修改与修复说明】
- 修复了 `process_posting()` 方法（第 281~301 行）中的严重逻辑与误导日志缺陷：
  * 老版本未判断 `self.click_all_checkboxes()` 与 `self.click_post_to_gl_button()` 的返回值，
    无论点击成功还是失败都会无条件执行 `print("点击 Post TO GL 按钮失败")` 并继续等待；
  * 1104 版改为严格校验返回值：若未勾选到复选框则立即返回 `False`；若 `click_post_to_gl_button()`
    返回 `False` 才打印失败并返回 `False`；点击成功后打印 `"已点击 Post TO GL 按钮，正在等待过账处理完成..."`。
================================================================================
"""

import time
from typing import List, Dict, Any
from utils import CDPClient


class PostingHandler:
    """越南 FAST 网页端批量勾选与 Post to GL 总账过账处理器。"""

    def __init__(self, cdp_client: CDPClient):
        """
        初始化过账处理器。
        :param cdp_client: 已连接到越南 FAST 网页的 CDPClient 实例
        """
        self.cdp = cdp_client

    def navigate_to_posting_page(self) -> bool:
        """依次点击 General 菜单与 Post Transaction 子菜单，进入批量过账页面。"""
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
        """查找 FAST 待过账列表中的首行复选框控件，判断当前页是否有待过账凭证。"""
        print("正在查找表格中的复选框...")

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
        """
        优先点击表头 `Select` 全选按钮 (`div.HeaderArrowImage`)；
        若未找到全选按钮，则自动遍历点击表格中所有未勾选的 checkbox，返回勾选数量。
        """
        print("正在点击全选按钮...")

        script = """
            (function() {
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
        """点击工具栏上的 `Post to GL` 按钮触发凭证过账入总账。"""
        print("正在点击 Post TO GL 按钮...")

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
        """判断当前页面列表中是否仍有待过账记录。"""
        checkboxes = self.find_checkboxes_in_table()
        return len(checkboxes) > 0

    def wait_for_processing(self, timeout: int = 30) -> bool:
        """轮询等待 FAST 后端过账完成并返回带有 Refresh 链接的 Post Transaction 页面。"""
        print("等待处理完成...")
        time.sleep(2)

        start_time = time.time()
        while time.time() - start_time < timeout:
            processing_complete = self.cdp.execute_script("""
                (function() {
                    var hasRefresh = false;
                    var hasPostTransaction = false;
                    
                    var links = document.querySelectorAll('a');
                    for (var i = 0; i < links.length; i++) {
                        var text = (links[i].innerText || links[i].textContent || '').trim();
                        if (text.indexOf('Refresh') >= 0) {
                            hasRefresh = true;
                            break;
                        }
                    }
                    
                    var tds = document.querySelectorAll('td');
                    for (var j = 0; j < tds.length; j++) {
                        var text = (tds[j].innerText || tds[j].textContent || '').trim();
                        if (text === 'Post Transaction') {
                            hasPostTransaction = true;
                            break;
                        }
                    }
                    
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
        """
        【1104 核心修复方法】
        执行单批次全选并点击 Post to GL 过账：
        1. 检查是否有待过账复选框记录；
        2. 校验 `click_all_checkboxes()` 返回的勾选数量必须大于 0；
        3. 校验 `click_post_to_gl_button()` 返回值，仅在返回 False 时才报告点击失败并退出；
        4. 点击成功后等待 `wait_for_processing()` 完成过账并返回 True。
        """
        if not self.has_more_records():
            print("没有更多需要过帐的记录")
            return False

        checked_count = self.click_all_checkboxes()
        if checked_count <= 0:
            print("未勾选到可过账的复选框记录")
            return False

        if not self.click_post_to_gl_button():
            print("点击 Post TO GL 按钮失败")
            return False

        print("已点击 Post TO GL 按钮，正在等待过账处理完成...")
        if not self.wait_for_processing():
            print("等待过账处理完成超时")
            return False

        print("过帐处理完成")
        return True