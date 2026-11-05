import subprocess
import tempfile
import time
import os
from typing import Optional
import requests


class ChromeLauncher:
    def __init__(self, chrome_path: str, remote_debugging_port: int = 9222, user_data_dir: Optional[str] = None):
        self.chrome_path = chrome_path
        self.remote_debugging_port = remote_debugging_port
        self.user_data_dir = user_data_dir or (
            "d:/sharedocs/tmp/chrome-user-data" if os.name == "nt" else os.path.join(tempfile.gettempdir(), "chrome-user-data")
        )
        self.process: Optional[subprocess.Popen] = None

    def start_chrome(self, headless: bool = False) -> bool:
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
            if headless:
                cmd.extend(["--headless=new", "--no-sandbox", "--disable-gpu"])

            popen_kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(cmd, **popen_kwargs)

            # print("等待 Chrome 启动...")
            for i in range(10):
                time.sleep(1)
                if self.is_chrome_running():
                    print(f"Chrome 启动成功，调试端口: {self.remote_debugging_port}")
                    return True
                print(f"  等待中... {i+1}/10")

            # print("Chrome 启动失败")
            return False

        except Exception as e:
            # print(f"启动 Chrome 时出错: {e}")
            return False

    def is_chrome_running(self) -> bool:
        
        try:
            response = requests.get(
                f"http://localhost:{self.remote_debugging_port}/json/version",
                timeout=2
            )
            return response.status_code == 200
        except Exception as e:
            # print(f"检查 Chrome 是否运行时出错: {e}")
            return False

    def stop_chrome(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                self.process.kill()
            finally:
                self.process = None
                # print("Chrome 已关闭")

    def get_debugger_url(self) -> str:
        return f"http://localhost:{self.remote_debugging_port}"
