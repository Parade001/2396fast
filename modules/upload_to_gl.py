import sys
import os
import json
import time
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import ChromeLauncher,CDPClient,LoginHandler,UploadHandler
from config import Config


def upload_to_fast():
    logger = logging.getLogger(__name__)
    config = Config().get_chrome_config()

    logger.info("=" * 50)
    logger.info("FastAutoUpload 启动")
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
    upload_handler = None

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
        upload_handler = UploadHandler(cdp_client)

        logger.info("正在导航到登录页面...")
        if not login_handler.navigate_to_login():
            logger.error("导航到登录页面失败")
            return 1

        logger.info("=" * 50)
        logger.info("请手动完成登录操作")
        logger.info("登录完成后，按 Enter 键继续...")
        logger.info("=" * 50)
        input()

        logger.info("开始处理文件上传...")
        upload_handler.navigate_to_upload_page()

        from file_list import FILES, move_files
        
        
        for f in FILES:
            ok = upload_handler.upload_file(f)
            if ok:  
                logger.info(f"已上传文件: {f}")
                move_files(f)
                time.sleep(2)   
            else:
                logger.error(f"上传文件失败: {f}")
                raise



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
