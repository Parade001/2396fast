#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: modules/upload_to_gl.py
模块定位: 越南 FAST 凭证模板全自动登录、循环上传与文件归档编排模块（CFO 需求 7）
================================================================================
【本文件核心作用】
1. 读取 Chrome 与越南 FAST 登录配置（`chrome_path`、`remote_debugging_port`、`fast_url`、
   `fast_user`、`fast_password`、`fast_unit`）；
2. 启动 Chrome 远程调试实例 (`ChromeLauncher`) 并建立 CDP WebSocket 连接 (`CDPClient`)；
3. 调用 `LoginHandler.login(fast_user, fast_password, fast_unit)` 执行全自动填表登录；
   若未配置账密且处于交互式终端，支持优雅回退人工登录确认；
4. 导航至 `General Voucher` 上传页，自动扫描 `output/*.xlsx` 下的全部 43 列 FAST 模板文件，
   逐个调用 `UploadHandler.upload_file()` 完成 CDP 协议级上传，并将已上传成功的文件自动移入
   `output/uploaded/` 归档目录，防止重复上传。

【1104 版本关键修改与修复说明】
- 正式接通了 `LoginHandler.login()` 全自动填表登录（老版本仅调用了 `navigate_to_login()` 停在登录页苦等人工回车）；
- 修复了模块路径与 `Config().get_chrome_config()` 的对接，支持 `auto_login` 与 `headless` 参数。
================================================================================
"""

import os
import sys
import glob
import time
import shutil
import logging

# 自动挂载项目根目录与 .pydeps 本地依赖目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
_PYDEPS_DIR = os.path.join(BASE_DIR, ".pydeps")
if os.path.isdir(_PYDEPS_DIR) and _PYDEPS_DIR not in sys.path:
    sys.path.insert(0, _PYDEPS_DIR)

from utils import ChromeLauncher, CDPClient, LoginHandler, UploadHandler, setup_logger
from sap2fast_full_fix import Config


def upload_to_fast(
    input_dir: str = "output",
    auto_login: bool = True,
    interactive_fallback: bool = True,
    headless: bool = False,
) -> int:
    """
    执行越南 FAST 凭证模板全自动登录与循环上传流程。
    :param input_dir: 待上传的 43 列 FAST 模板所在目录（默认 'output'）
    :param auto_login: 是否启用账号密码自动填表登录（默认 True）
    :param interactive_fallback: 当未配置账密或自动登录未通过时，是否允许交互式按回车继续
    :param headless: 是否以 Chrome 无头模式运行
    :return: 0 表示全部上传成功，1 表示出现错误
    """
    logger = setup_logger("FastAutoUpload")
    config = Config().get_chrome_config()

    logger.info("=" * 50)
    logger.info("FastAutoUpload 启动 (1104 全自动登录+循环上传版)")
    logger.info("=" * 50)

    if not config:
        logger.error("加载 Chrome 配置文件失败")
        return 1

    # 读取 Chrome 启动参数与 FAST 登录凭据
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
        # 步骤 1：启动 Chrome 调试进程
        logger.info("正在启动 Chrome 浏览器...")
        if not chrome_launcher.start_chrome(headless=headless):
            logger.error("Chrome 启动失败")
            return 1

        # 步骤 2：建立 CDP WebSocket 连接
        logger.info("正在连接 CDP...")
        cdp_client = CDPClient(f"http://localhost:{remote_debugging_port}")
        if not cdp_client.connect_to_page():
            logger.error("连接 CDP 失败")
            return 1

        login_handler = LoginHandler(cdp_client, login_url=fast_url)
        upload_handler = UploadHandler(cdp_client)

        # 步骤 3：1104 全自动填表登录（若配置了 fast_user / fast_password 则自动完成登录）
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

        # 若未自动登录成功，在交互终端下支持人工确认兜底
        if not logged_in:
            if interactive_fallback and sys.stdin and sys.stdin.isatty():
                logger.info("请确认完成 FAST 网页登录，按 Enter 键继续执行批量上传...")
                input()
            elif not (fast_user and fast_password):
                logger.error("未配置 FAST_USER / FAST_PASSWORD 且处于非交互模式，无法完成登录")
                return 1

        # 步骤 4：导航至 General Voucher 页面并循环上传 Excel 文件
        logger.info("开始导航至 General Voucher 上传页面...")
        upload_handler.navigate_to_upload_page()

        target_dir = os.path.join(BASE_DIR, input_dir) if not os.path.isabs(input_dir) else input_dir
        files = sorted(glob.glob(os.path.join(target_dir, "*.xlsx")))
        uploaded_dir = os.path.join(target_dir, "uploaded")
        os.makedirs(uploaded_dir, exist_ok=True)

        for f in files:
            # 跳过 Excel 临时锁文件 (~$*.xlsx)
            if os.path.basename(f).startswith("~$"):
                continue
            ok = upload_handler.upload_file(f)
            if ok:
                logger.info(f"已上传文件: {f}")
                # 上传成功后立即移入 uploaded/ 归档子目录，避免二次重复导入
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
        # 步骤 5：清理 CDP 连接与浏览器进程
        if cdp_client:
            cdp_client.close()
            logger.info("CDP 连接已关闭")
        if config.get("close_browser_on_exit", False):
            chrome_launcher.stop_chrome()
            logger.info("Chrome 已关闭")

    return 0
