import sys
import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import ChromeLauncher,CDPClient,LoginHandler,PostingHandler
from config import Config


def postToGl():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    config = Config().get_chrome_config()

    logger.info("=" * 50)
    logger.info("FastAutoPost 启动")
    logger.info("=" * 50)

    if config:
        logger.info("配置文件加载成功")
    else:
        logger.error(f"加载配置文件失败: {e}")
        return 1

    chrome_path = config.get("chrome_path", "")
    remote_debugging_port = config.get("remote_debugging_port", 9222)
    max_retries = config.get("max_retries", 3)

    if not chrome_path or not os.path.exists(chrome_path):
        logger.error(f"Chrome 路径无效: {chrome_path}")
        logger.error("请检查配置文件中的 chrome_path 设置")
        return 1

    chrome_launcher = ChromeLauncher(chrome_path, remote_debugging_port)
    cdp_client = None
    login_handler = None
    posting_handler = None

    try:
        logger.info("正在启动 Chrome 浏览器...")
        if not chrome_launcher.start_chrome():
            logger.error("Chrome 启动失败")
            return 1

        logger.info("正在连接 CDP...")
        cdp_client = CDPClient(f"http://localhost:{remote_debugging_port}")
        if not cdp_client.connect_to_page():
            logger.error("连接 CDP 失败")
            return 1

        login_handler = LoginHandler(cdp_client)
        posting_handler = PostingHandler(cdp_client)

        logger.info("正在导航到登录页面...")
        if not login_handler.navigate_to_login():
            logger.error("导航到登录页面失败")
            return 1

        logger.info("=" * 50)
        logger.info("请手动完成登录操作")
        logger.info("登录完成后，按 Enter 键继续...")
        logger.info("=" * 50)
        input()

        logger.info("开始处理过帐...")
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
                logger.warning("处理过帐失败，重试...")
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

        if config.get("close_browser_on_exit", True):
            # chrome_launcher.stop_chrome()
            logger.info("Chrome 已关闭")

    return 0
