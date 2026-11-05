#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/chrome_launcher.py
模块定位: Google Chrome 远程调试浏览器进程启动与生命周期管理器
================================================================================
【本文件核心作用】
1. 负责拉起带有 `--remote-debugging-port=9222` 参数的 Chrome 浏览器进程，供 `CDPClient` 连接；
2. 启动前自动探测端口 `9222` 是否已有运行中的 Chrome 调试实例 (`is_chrome_running`)，避免重复拉起；
3. 提供安全停止浏览器进程的方法 (`stop_chrome`)。

【1104 版本关键修改与修复说明】
1. 跨平台兼容修复：
   - 老版本硬编码了 Windows 专属的 `subprocess.CREATE_NEW_PROCESS_GROUP` 与固定路径 `d:/sharedocs/tmp/chrome-user-data`，
     在非 Windows 环境或无 D 盘机器上会报错；
   - 1104 版改为通过 `hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")` 动态判断，并在非 Windows 环境下
     自动使用系统临时目录 `tempfile.gettempdir()`。
2. 新增无头模式支持 (`start_chrome(headless: bool = False)`)：
   - 当传入 `headless=True`（或命令行 `--headless`）时，自动附加 `--headless=new --no-sandbox --disable-gpu`，
     支持在服务器后台静默完成 FAST 网页自动登录、Excel 上传与 Post 过账。
================================================================================
"""

import os
import time
import tempfile
import subprocess
import urllib.request
from typing import Optional
try:
    import requests
except ImportError:
    requests = None


class ChromeLauncher:
    """Chrome 远程调试浏览器启动器（支持有界面模式与无头 Headless 模式）。"""

    def __init__(self, chrome_path: str, remote_debugging_port: int = 9222, user_data_dir: Optional[str] = None):
        """
        初始化 Chrome 启动器。
        :param chrome_path: Chrome 可执行文件绝对路径
        :param remote_debugging_port: CDP 远程调试端口号（默认 9222）
        :param user_data_dir: 独立的用户配置文件夹路径
        """
        self.chrome_path = chrome_path
        self.remote_debugging_port = remote_debugging_port
        # 1104 跨平台兼容：Windows 下默认使用 d:/sharedocs/tmp/chrome-user-data（若无 D 盘则回退临时目录）
        if user_data_dir:
            self.user_data_dir = user_data_dir
        elif os.name == "nt" and os.path.exists("d:/"):
            self.user_data_dir = "d:/sharedocs/tmp/chrome-user-data"
        else:
            self.user_data_dir = os.path.join(tempfile.gettempdir(), "chrome-user-data")
        self.process: Optional[subprocess.Popen] = None

    def start_chrome(self, headless: bool = False) -> bool:
        """
        启动带有远程调试端口的 Chrome 进程，并轮询等待调试端口就绪。
        :param headless: 是否开启 `--headless=new` 无头后台模式
        :return: Chrome 调试端口就绪返回 True，否则返回 False
        """
        try:
            if self.is_chrome_running():
                print(f"Chrome 已在端口 {self.remote_debugging_port} 上运行")
                return True

            cmd = [
                self.chrome_path,
                f"--remote-debugging-port={self.remote_debugging_port}",
                "--remote-allow-origins=*",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={self.user_data_dir}",
            ]
            # 1104 新增：支持无头模式运行
            if headless:
                cmd.extend(["--headless=new", "--no-sandbox", "--disable-gpu"])

            popen_kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            # 仅在 Windows 等支持 CREATE_NEW_PROCESS_GROUP 的系统上附加该标志
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(cmd, **popen_kwargs)

            for i in range(10):
                time.sleep(1)
                if self.is_chrome_running():
                    print(f"Chrome 启动成功，调试端口: {self.remote_debugging_port}")
                    return True
                print(f"  等待中... {i + 1}/10")

            return False

        except Exception:
            return False

    def is_chrome_running(self) -> bool:
        """通过访问 `http://localhost:<port>/json/version` 检测 Chrome 远程调试端口是否已就绪。"""
        url = f"http://localhost:{self.remote_debugging_port}/json/version"
        try:
            if requests is not None:
                response = requests.get(url, timeout=2)
                return response.status_code == 200
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def stop_chrome(self):
        """安全终止由本启动器拉起的 Chrome 进程。"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            finally:
                self.process = None

    def get_debugger_url(self) -> str:
        """返回当前 Chrome 实例的本地 HTTP 调试根地址。"""
        return f"http://localhost:{self.remote_debugging_port}"
