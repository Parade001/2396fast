import json
import requests
import websocket
import time
from typing import Dict, Any, Optional, List
from utils import setup_logger



class CDPClient:
    def __init__(self, debugger_url: str = "http://localhost:9222"):
        self.debugger_url = debugger_url
        self.ws: Optional[websocket.WebSocket] = None
        self.session_id: Optional[str] = None
        self.message_id = 0
        self.logger = setup_logger(__name__)





    def get_pages(self) -> List[Dict[str, Any]]:
        try:
            response = requests.get(f"{self.debugger_url}/json", timeout=10)
            return response.json()
        except Exception as e:
            self.logger.error(f"获取页面列表失败: {e}")
            return []

    def connect_to_page(self, page_url: str = None) -> bool:
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
        result = self.send_command("Page.navigate", {"url": url})
        if result and "error" not in result:
            self.logger.info(f"导航到: {url}") 
            time.sleep(3)
            return True
        return False

    def wait_for_page_load(self, timeout: int = 30) -> bool:
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
        if args:
            # 将参数转换为 JavaScript 数组
            args_str = json.dumps(args)
            # 包装脚本以接收参数
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
        import os

        # 检查文件路径是否存在
        if not os.path.exists(file_path):
            self.logger.error(f"文件不存在: {file_path}")
            return False

        # 检查WebSocket连接
        if not self.ws:
            self.logger.error("WebSocket 未连接")
            return False

        try:
            # 启用DOM域
            self.send_command("DOM.enable")
            time.sleep(1)
            # 获取文档根节点
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

            # 查询文件上传输入框节点
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

            # 设置文件到文件上传控件
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
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
            finally:
                self.ws = None