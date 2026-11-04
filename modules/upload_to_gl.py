import os
import sys
import glob
import time
import shutil
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from utils import ChromeLauncher, CDPClient, LoginHandler, UploadHandler, setup_logger
from sap2fast_full_fix import Config


def upload_to_fast(
    input_dir: str = "output",
    auto_login: bool = True,
    interactive_fallback: bool = True,
    headless: bool = False,
) -> int:
    logger = setup_logger("FastAutoUpload")
    config = Config().get_chrome_config()

    logger.info("=" * 50)
    logger.info("FastAutoUpload 启动 (1104 全自动登录+循环上传版)")
    logger.info("=" * 50)

    if not config:
        logger.error("加载 Chrome 配置文件失败")
        return 1

    chrome_path = config.get("chrome_path", "")
    remote_debugging_port = int(config.get("remote_debugging_port", 9222))
    fast_url = config.get("fast_url", "http://10.7.3.230/FAW_TIMBERLAND/Main/Login.aspx")
    fast_user = config.get("fast_user", "")
    fast_password = config.get("fast_password", "")
    fast_unit = config.get("fast_unit", "CTY")

    if not chrome_path or not os.path.exists(chrome_path):
        logger.error(f"Chrome 路径无效: {chrome_path}")
        return 1

    chrome_launcher = ChromeLauncher(chrome_path, remote_debugging_port)
    cdp_client = None

    try:
        logger.info("正在启动 Chrome 浏览器...")
        if not chrome_launcher.start_chrome(headless=headless):
            logger.error("Chrome 启动失败")
            return 1

        logger.info("正在连接 CDP...")
        cdp_client = CDPClient(f"http://localhost:{remote_debugging_port}")
        if not cdp_client.connect_to_page():
            logger.error("连接 CDP 失败")
            return 1

        login_handler = LoginHandler(cdp_client, login_url=fast_url)
        upload_handler = UploadHandler(cdp_client)

        logged_in = False
        if auto_login and fast_user and fast_password:
            logger.info(f"正在使用配置账号 ({fast_user}) 自动登录 FAST 系统...")
            logged_in = login_handler.login(fast_user, fast_password, fast_unit)
            if logged_in:
                logger.info("FAST 网页自动登录成功！")
            else:
                logger.warning("自动填表登录未确认成功，准备切换人工确认或重试...")
        else:
            if not login_handler.navigate_to_login():
                logger.error("导航到登录页面失败")
                return 1

        if not logged_in:
            if interactive_fallback and sys.stdin and sys.stdin.isatty():
                logger.info("请确认完成 FAST 网页登录，按 Enter 键继续执行批量上传...")
                input()
            elif not (fast_user and fast_password):
                logger.error("未配置 FAST_USER / FAST_PASSWORD 且处于非交互模式，无法完成登录")
                return 1

        logger.info("开始导航至 General Voucher 上传页面...")
        upload_handler.navigate_to_upload_page()

        target_dir = os.path.join(BASE_DIR, input_dir) if not os.path.isabs(input_dir) else input_dir
        files = sorted(glob.glob(os.path.join(target_dir, "*.xlsx")))
        uploaded_dir = os.path.join(target_dir, "uploaded")
        os.makedirs(uploaded_dir, exist_ok=True)

        for f in files:
            if os.path.basename(f).startswith("~$"):
                continue
            ok = upload_handler.upload_file(f)
            if ok:
                logger.info(f"已上传文件: {f}")
                shutil.move(f, os.path.join(uploaded_dir, os.path.basename(f)))
                time.sleep(2)
            else:
                logger.error(f"上传文件失败: {f}")
                return 1

    except KeyboardInterrupt:
        logger.info("用户中断操作")
    except Exception as e:
        logger.error(f"发生错误: {e}", exc_info=True)
        return 1
    finally:
        if cdp_client:
            cdp_client.close()
            logger.info("CDP 连接已关闭")
        if config.get("close_browser_on_exit", False):
            chrome_launcher.stop_chrome()
            logger.info("Chrome 已关闭")

    return 0
