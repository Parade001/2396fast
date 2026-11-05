import os
import sys
import time
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from utils import ChromeLauncher, CDPClient, LoginHandler, PostingHandler, setup_logger
from sap2fast_full_fix import Config


def postToGl(
    auto_login: bool = True,
    interactive_fallback: bool = True,
    headless: bool = False,
) -> int:
    logger = setup_logger("FastAutoPost")
    config = Config().get_chrome_config()

    logger.info("=" * 50)
    logger.info("FastAutoPost 启动 (1104 全自动登录+批量过账版)")
    logger.info("=" * 50)

    if not config:
        logger.error("加载 Chrome 配置文件失败")
        return 1

    chrome_path = config.get("chrome_path", "")
    remote_debugging_port = int(config.get("remote_debugging_port", 9222))
    max_retries = int(config.get("max_retries", 3))
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
        posting_handler = PostingHandler(cdp_client)

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
                logger.info("请确认完成 FAST 网页登录，按 Enter 键继续执行批量过账...")
                input()
            elif not (fast_user and fast_password):
                logger.error("未配置 FAST_USER / FAST_PASSWORD 且处于非交互模式，无法完成登录")
                return 1

        logger.info("开始导航至 Post Transaction 过账页面...")
        posting_handler.navigate_to_posting_page()

        retry_count = 0
        total_processed = 0

        while retry_count < max_retries:
            logger.info(f"第 {retry_count + 1} 次尝试处理过帐...")
            if posting_handler.process_posting():
                total_processed += 1
                logger.info(f"成功处理第 {total_processed} 批记录")
                retry_count = 0
                time.sleep(2)
            else:
                logger.warning("当前批次无更多过账记录或未触发过账，重试核验...")
                retry_count += 1
                time.sleep(2)

        logger.info("=" * 50)
        logger.info(f"处理完成，共处理 {total_processed} 批记录")
        logger.info("=" * 50)

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
