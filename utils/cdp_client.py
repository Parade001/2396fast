#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/cdp_client.py
模块定位: Chrome DevTools Protocol (CDP) WebSocket 远程调试协议通信客户端
================================================================================
【本文件核心作用】
1. 通过 HTTP (`http://localhost:9222/json`) 获取已开启远程调试端口的 Chrome 页面列表，
   并通过 WebSocket (`webSocketDebuggerUrl`) 建立与目标网页的底层双向控制连接；
2. 封装页面导航 (`Page.navigate`)、脚本执行 (`Runtime.evaluate`)、DOM 元素查询与点击、
   表单输入值填充（自动触发 `input`/`change`/`blur`/`keydown`/`keyup` 事件）等核心浏览器操控能力；
3. 提供纯协议级文件上传能力 (`attach_upload_file`)：通过 `DOM.enable` + `DOM.getDocument`
   + `DOM.setFileInputFiles` 直接将本地 43 列 FAST 模板 Excel 文件挂载到网页 `<input type="file">`
   控件上，无需依赖操作系统级窗口坐标点击，不惧锁屏或后台运行。

【1104 版本关键修改与修复说明】
- 修复了 `attach_upload_file` 方法（第 212~282 行）中原本多处直接调用未定义全局变量 `logger.error` /
  `logger.info` 导致一旦调用必然抛出 `NameError: name 'logger' is not defined` 的阻断性 Bug；
  现已全部统一修正为实例属性 `self.logger.error` / `self.logger.info`。
================================================================================
"""

import os
import json
import time
import urllib.request
try:
    import requests
except ImportError:
    requests = None
try:
    import websocket
except ImportError:
    websocket = None
from typing import Dict, Any, Optional, List
from utils import setup_logger


class CDPClient:
    """
    Chrome DevTools Protocol (CDP) 客户端核心类。
    负责管理与 Chrome 调试端口的 WebSocket 会话、命令收发及无界面文件上传。
    """

    def __init__(self, debugger_url: str = "http://localhost:9222"):
        """
        初始化 CDP 客户端。
        :param debugger_url: Chrome 远程调试 HTTP 地址，默认 http://localhost:9222
        """
        self.debugger_url = debugger_url
        self.ws: Optional[Any] = None
        self.session_id: Optional[str] = None
        self.message_id = 0
        # 初始化模块专属日志记录器（后续所有方法统一使用 self.logger）
        self.logger = setup_logger(__name__)

    def get_pages(self) -> List[Dict[str, Any]]:
        """获取当前 Chrome 实例打开的所有调试目标页面列表。"""
        try:
            response = requests.get(f"{self.debugger_url}/json", timeout=10)
            return response.json()
        except Exception as e:
            self.logger.error(f"获取页面列表失败: {e}")
            return []

    def connect_to_page(self, page_url: str = None) -> bool:
        """
        根据 URL 关键字或默认首个 page 类型标签页，建立 WebSocket 调试连接。
        :param page_url: 可选的目标页面 URL 子串
        :return: 连接成功返回 True，失败返回 False
        """
        try:
            pages = self.get_pages()
            target_page = None

            for page in pages:
                if page_url and page_url in page.get("url", ""):
                    target_page = page
                    break
                elif page.get("type") == "page":
                    target_page = page

            if not target_page:
                self.logger.error("未找到目标页面")
                return False

            ws_url = target_page.get("webSocketDebuggerUrl")
            if not ws_url:
                self.logger.error("未找到 WebSocket 调试 URL")
                return False

            self.ws = websocket.create_connection(ws_url, timeout=30)
            self.logger.info(f"已连接到页面: {target_page.get('title', 'Unknown')}")
            return True

        except Exception as e:
            self.logger.error(f"连接页面失败: {e}")
            return False

    def send_command(self, method: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """
        向 Chrome CDP WebSocket 发送单条 JSON-RPC 指令并同步接收响应。
        :param method: CDP 协议方法名（如 'Page.navigate', 'Runtime.evaluate', 'DOM.setFileInputFiles'）
        :param params: 协议方法参数字典
        """
        if not self.ws:
            self.logger.error("WebSocket 未连接")
            return None

        try:
            self.message_id += 1
            message = {
                "id": self.message_id,
                "method": method,
                "params": params or {}
            }

            self.ws.send(json.dumps(message))
            response = self.ws.recv()
            return json.loads(response)

        except Exception as e:
            self.logger.error(f"发送命令失败: {e}")
            return None

    def navigate(self, url: str) -> bool:
        """控制当前标签页导航跳转到指定 URL，并等待 3 秒使初始 DOM 渲染。"""
        result = self.send_command("Page.navigate", {"url": url})
        if result and "error" not in result:
            self.logger.info(f"导航到: {url}")
            time.sleep(3)
            return True
        return False

    def wait_for_page_load(self, timeout: int = 30) -> bool:
        """监听 CDP Page.loadEventFired 事件，等待页面完全加载。"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                self.ws.settimeout(1)
                message = self.ws.recv()
                data = json.loads(message)
                if data.get("method") == "Page.loadEventFired":
                    return True
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
        return False

    def execute_script(self, script: str, *args) -> Any:
        """在当前页面上下文执行 JavaScript 表达式并按值返回结果。"""
        if args:
            args_str = json.dumps(args)
            wrapped_script = f"(function() {{ return (function() {{ {script} }}).apply(this, {args_str}); }})()"
            result = self.send_command("Runtime.evaluate", {
                "expression": wrapped_script,
                "returnByValue": True
            })
        else:
            result = self.send_command("Runtime.evaluate", {
                "expression": script,
                "returnByValue": True
            })

        if result and "result" in result:
            result_data = result["result"].get("result", {})
            return result_data.get("value")
        return None

    def query_selector(self, selector: str) -> Optional[Dict[str, Any]]:
        """查询单个 CSS 选择器是否存在，并返回其 tagName / id / className 信息。"""
        script = f'''
            (function() {{
                var element = document.querySelector('{selector}');
                if (element) {{
                    return {{
                        found: true,
                        tagName: element.tagName,
                        id: element.id,
                        className: element.className
                    }};
                }}
                return {{ found: false }};
            }})()
        '''
        return self.execute_script(script)

    def query_selector_all(self, selector: str) -> Optional[List[Dict[str, Any]]]:
        """查询所有匹配指定 CSS 选择器的 DOM 元素列表。"""
        script = f'''
            (function() {{
                var elements = document.querySelectorAll('{selector}');
                var result = [];
                for (var i = 0; i < elements.length; i++) {{
                    result.push({{
                        index: i,
                        tagName: elements[i].tagName,
                        id: elements[i].id,
                        className: elements[i].className
                    }});
                }}
                return result;
            }})()
        '''
        return self.execute_script(script)

    def click_element(self, selector: str) -> bool:
        """通过 CSS 选择器查找并触发 DOM 元素的原生 click() 方法。"""
        script = f'''
            (function() {{
                var element = document.querySelector('{selector}');
                if (element) {{
                    element.click();
                    return true;
                }}
                return false;
            }})()
        '''
        result = self.execute_script(script)
        return result == True

    def set_input_value(self, selector: str, value: str, max_retries: int = 5) -> bool:
        """
        向指定的 `<input>` 或 `<select>` 控件写入文本值，并完整派发
        `input` / `change` / `blur` / `keydown` / `keyup` 事件以兼容越南 FAST 的 ASP.NET WebForms 校验。
        """
        escaped_value = value.replace("'", "\\'").replace('"', '\\"')

        for i in range(max_retries):
            script = f'''
                (function() {{
                    var element = document.querySelector('{selector}');
                    if (element) {{
                        element.focus();
                        element.click();

                        element.value = '{escaped_value}';

                        element.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        element.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        element.dispatchEvent(new Event('blur', {{ bubbles: true }}));

                        if (element.onkeypress) {{
                            var event = document.createEvent('KeyboardEvent');
                            event.initEvent('keypress', true, true);
                            element.dispatchEvent(event);
                        }}

                        element.dispatchEvent(new KeyboardEvent('keydown', {{ bubbles: true }}));
                        element.dispatchEvent(new KeyboardEvent('keyup', {{ bubbles: true }}));

                        return true;
                    }}
                    return false;
                }})()
            '''
            result = self.execute_script(script)
            if result == True:
                return True
            time.sleep(1)

        return False

    def attach_upload_file(self, file_path: str = '') -> bool:
        """
        【1104 核心修复方法】
        通过 Chrome CDP 原生协议 `DOM.setFileInputFiles` 将本地 Excel 文件直接注入到
        越南 FAST 网页上传弹窗的文件选择框 (`#fileupload` 或 `input[type="file"]`) 中。
        - 已将原代码中全部未定义的 `logger.` 修复为 `self.logger.`，彻底消除 NameError。
        """
        # 1. 检查本地待上传的 Excel 文件是否存在
        if not os.path.exists(file_path):
            self.logger.error(f"文件不存在: {file_path}")
            return False

        # 2. 检查 CDP WebSocket 是否处于已连接状态
        if not self.ws:
            self.logger.error("WebSocket 未连接")
            return False

        try:
            # 3. 启用 CDP DOM 域并获取完整文档根节点（含穿透 iframe）
            self.send_command("DOM.enable")
            time.sleep(1)
            doc_result = self.send_command("DOM.getDocument", {"depth": -1, "pierce": True})

            if not doc_result:
                self.logger.error("获取文档根节点失败: 未收到响应")
                return False
            if "error" in doc_result:
                self.logger.error(f"获取文档根节点失败: {doc_result['error']}")
                return False
            if "result" not in doc_result:
                self.logger.error(f"获取文档根节点失败: 响应中缺少result字段，响应内容: {doc_result}")
                return False
            if "root" not in doc_result["result"]:
                self.logger.error(f"获取文档根节点失败: 响应中缺少root字段，响应内容: {doc_result}")
                return False
            if "nodeId" not in doc_result["result"]["root"]:
                self.logger.error(f"获取文档根节点失败: 响应中缺少nodeId字段，响应内容: {doc_result}")
                return False

            # 4. 查找越南 FAST 上传弹窗中的文件上传 input 控件并获取其远程 objectId
            evaluate_result = self.send_command("Runtime.evaluate", {
                "expression": "document.querySelector('#fileupload') || document.querySelector('input[type=\"file\"]')",
                "returnByValue": False
            })

            if not evaluate_result:
                self.logger.error("查询文件上传输入框失败: 未收到响应")
                return False
            if "error" in evaluate_result:
                self.logger.error(f"查询文件上传输入框失败: {evaluate_result['error']}")
                return False
            if "result" not in evaluate_result:
                self.logger.error(f"查询文件上传输入框失败: 响应中缺少result字段，响应内容: {evaluate_result}")
                return False
            if "result" not in evaluate_result["result"] or "objectId" not in evaluate_result["result"]["result"]:
                self.logger.error("未找到文件上传输入框或响应中缺少objectId字段")
                return False

            object_id = evaluate_result["result"]["result"]["objectId"]
            self.logger.info(f"找到文件上传控件，objectId: {object_id}")

            # 5. 调用 DOM.setFileInputFiles 注入本地 Excel 绝对路径
            self.send_command("DOM.setFileInputFiles", {
                "files": [file_path],
                "objectId": object_id
            })

            self.logger.info(f"已设置文件: {file_path}")
            return True

        except Exception as e:
            self.logger.error(f"发生错误: {e}")
            return False

    def close(self):
        """安全关闭 WebSocket 调试连接。"""
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            finally:
                self.ws = None