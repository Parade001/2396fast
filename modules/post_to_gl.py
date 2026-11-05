#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: modules/post_to_gl.py
模块定位: 越南 FAST 凭证全自动登录与批量过账至总账 (Post to GL) 编排模块（CFO 需求 7）
================================================================================
【本文件核心作用】
1. 读取 Chrome 与越南 FAST 登录配置，拉起远程调试浏览器并连接 CDP；
2. 调用 `LoginHandler.login(fast_user, fast_password, fast_unit)` 自动完成网页登录；
3. 调用 `PostingHandler.navigate_to_posting_page()` 进入 `Post Transaction` 页面；
4. 循环调用 `PostingHandler.process_posting()`：自动全选当页未过账凭证 -> 点击 `Post to GL`
   -> 等待 Refresh 过账完成，直至所有批次全部过账入总账。

【1104 版本关键修改与修复说明】
1. 正式接通了 `LoginHandler.login()` 全自动填表登录（老版本仅停在登录页等待人工按键）；
2. 修复了老版本第 68 行引用未定义变量 `upload_handler` 导致的 `NameError` 阻断性 Bug；
3. 配合修复后的 `PostingHandler.process_posting()`，准确统计成功过账批次并支持重试控制。
================================================================================
"""

import os
import sys
import time
import logging

# 自动挂载项目根目录与 .pydeps 本地依赖目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
_PYDEPS_DIR = os.path.join(BASE_DIR, ".pydeps")
if os.path.isdir(_PYDEPS_DIR) and _PYDEPS_DIR not in sys.path:
    sys.path.insert(0, _PYDEPS_DIR)

from utils import ChromeLauncher, CDPClient, LoginHandler, PostingHandler, setup_logger
from sap2fast_full_fix import Config


def postToGl(
    auto_login: bool = True,
    interactive_fallback: bool = True,
    headless: bool = False,
) -> int:
    """
    执行越南 FAST 凭证全自动登录与批量勾选 Post to GL 总账过账流程。
    :param auto_login: 是否启用账号密码自动填表登录（默认 True）
    :param interactive_fallback: 当未配置账密或自动登录未通过时，是否允许交互式按回车继续
    :param headless: 是否以 Chrome 无头模式运行
    :return: 0 表示过账流程正常结束，1 表示出现异常
    """
    logger = setup_logger("FastAutoPost")
    config = Config().get_chrome_config()

    logger.info("=" * 50)
    logger.info("FastAutoPost 启动 (1104 全自动登录+批量过账版)")
    logger.info("=" * 50)

    if not config:
        logger.error("加载 Chrome 配置文件失败")
        return 1

    # 读取 Chrome 启动参数与越南 FAST 登录凭据
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
        # 步骤 1：启动 Chrome 调试浏览器
        logger.info("正在启动 Chrome 浏览器...")
        if not chrome_launcher.start_chrome(headless=headless):
            logger.error("Chrome 启动失败")
            return 1

        # 步骤 2：连接 CDP WebSocket
        logger.info("正在连接 CDP...")
        cdp_client = CDPClient(f"http://localhost:{remote_debugging_port}")
        if not cdp_client.connect_to_page():
            logger.error("连接 CDP 失败")
            return 1

        login_handler = LoginHandler(cdp_client, login_url=fast_url)
        posting_handler = PostingHandler(cdp_client)

        # 步骤 3：1104 全自动填表登录
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

        # 步骤 4：导航至 Post Transaction 页面并循环执行全选过账
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
        # 步骤 5：安全释放 CDP 连接与浏览器进程
        if cdp_client:
            cdp_client.close()
            logger.info("CDP 连接已关闭")
        if config.get("close_browser_on_exit", False):
            chrome_launcher.stop_chrome()
            logger.info("Chrome 已关闭")

    return 0
