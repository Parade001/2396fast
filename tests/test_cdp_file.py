# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: tests/test_cdp_file.py
模块定位: Chrome CDP 协议级文件上传 (`DOM.setFileInputFiles`) 联调测试脚本
================================================================================
【本文件核心作用】
用于测试 Chrome 远程调试连接、登录越南 FAST 系统并通过 CDP 协议将本地 Excel 凭证模板
直接注入网页上传输入框 (`#fileupload`) 的完整交互链路。
================================================================================
"""
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
        upload_handler.enter_import_dialog()
        # time.sleep(5)
        print('识别窗口')
        # 获取整个页面信息
        # 启用 DOM 域
        # 启用 DOM 域
        cdp_client.send_command("DOM.enable")
        
        # 获取文档根节点
        doc_result = cdp_client.send_command("DOM.getDocument", {"depth": -1, "pierce": True})
        
        if not doc_result:
            logger.error("获取文档根节点失败: 未收到响应")
            return 1
        if "error" in doc_result:
            logger.error(f"获取文档根节点失败: {doc_result['error']}")
            return 1
        if "result" not in doc_result:
            logger.error(f"获取文档根节点失败: 响应中缺少result字段，响应内容: {doc_result}")
            return 1
        if "root" not in doc_result["result"]:
            logger.error(f"获取文档根节点失败: 响应中缺少root字段，响应内容: {doc_result}")
            return 1
        if "nodeId" not in doc_result["result"]["root"]:
            logger.error(f"获取文档根节点失败: 响应中缺少nodeId字段，响应内容: {doc_result}")
            return 1
        
        root_node_id = doc_result["result"]["root"]["nodeId"]
        
        # 查询文件上传输入框节点
        # 使用 Runtime.evaluate 执行 JavaScript 来获取文件上传控件
        evaluate_result = cdp_client.send_command("Runtime.evaluate", {
            "expression": "document.querySelector('#fileupload')",
            "returnByValue": False
        })
        
        if not evaluate_result:
            logger.error("查询文件上传输入框失败: 未收到响应")
            return 1
        if "error" in evaluate_result:
            logger.error(f"查询文件上传输入框失败: {evaluate_result['error']}")
            return 1
        if "result" not in evaluate_result:
            logger.error(f"查询文件上传输入框失败: 响应中缺少result字段，响应内容: {evaluate_result}")
            return 1
        if "result" not in evaluate_result["result"] or "objectId" not in evaluate_result["result"]["result"]:
            logger.error("未找到文件上传输入框或响应中缺少objectId字段")
            return 1
        
        object_id = evaluate_result["result"]["result"]["objectId"]
        
        logger.info(f"找到文件上传控件，objectId: {object_id}")
        
        # 设置文件到文件上传控件
        file_path = "D:\\sharedocs\\sap2tbl\\2025第5期补充数据.xlsx_6.result.xlsx"
        cdp_client.send_command("DOM.setFileInputFiles", {
            "files": [file_path],
            "objectId": object_id
        })
        
        logger.info(f"已设置文件: {file_path}")
        # 触发 change 事件以确保文件被正确识别
        script = """
            (function() {
                var input = document.querySelector('input[type="file"]');
                if (input) {
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return 'change event dispatched';
                }
                return 'input not found';
            })()
        """
        result = cdp_client.execute_script(script)
        logger.info(f"执行脚本结果: {result}")
    except Exception as e:
        print(f"发生错误: {e}")
import pandas as pd
import json
from pathlib import Path

def update_column_g_data(file_path: str):
    """
    更新Excel文件中G列的数据
    如果值是6701000005，更新为6426；
    6001010001更新为521122；
    160300001更新为6424；
    如果长度为10，等待手工录入更新数据，并把该映射保存备后续自动更新
    """
    flag = False
    # 定义默认映射关系
    default_mapping = {
        '6701000005': '6426',
        '6001010001': '521122',
        '1603000001': '6424'
    }
    
    # 尝试从文件加载之前保存的映射关系
    mapping_file = Path(file_path).parent / "g_column_mapping.json"
    if mapping_file.exists():
        with open(mapping_file, 'r', encoding='utf-8') as f:
            saved_mapping = json.load(f)
        # 合并默认映射和保存的映射
        mapping = {**default_mapping, **saved_mapping}
    else:
        mapping = default_mapping.copy()
    
    # 读取Excel文件
    df = pd.read_excel(file_path)
    
    # 检查是否有G列
    if len(df.columns) < 7:  # G列是第7列（索引为6）
        print("警告：Excel文件中没有G列或列数不足")
        return file_path
    
    column_g_name = df.columns[6]  # G列的名称
    
    # 存储新的映射关系
    new_mappings = {}
    
    # 遍历G列的每个值
    for idx, value in enumerate(df[column_g_name]):
        str_value = str(value)  # 转换为字符串进行比较
        
        # 检查是否在映射表中
        if str_value in mapping:
            df.at[idx, column_g_name] = mapping[str_value]
            flag = True
            print(f"行 {idx+2}: {str_value} 已更新为 {mapping[str_value]}")  # +2是因为索引从0开始，且Excel有标题行
        elif len(str_value) == 10:
            # 如果长度为10，提示用户输入新值
            print(f"行 {idx+2}: 发现长度为10的值 '{str_value}'，请输入要更新的值{file_path}:")
            new_value = input().strip()
            
            # 添加到新映射中
            new_mappings[str_value] = new_value
            
            # 更新DataFrame
            df.at[idx, column_g_name] = new_value
            flag = True
            print(f"行 {idx+2}: {str_value} 已更新为 {new_value}")
    
    # 如果有新的映射关系，保存到文件
    if new_mappings:
        # 更新映射字典
        mapping.update(new_mappings)
        
        # 保存映射关系到JSON文件
        with open(mapping_file, 'w', encoding='utf-8') as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        print(f"新的映射关系已保存到 {mapping_file}")
    
    # 保存更新后的Excel文件，覆盖原文件
    if flag:
        df.to_excel(file_path, index=False)
        print(f"文件已更新并保存至 {file_path}")
    
        

if __name__ == "__main__":
    from file_list import FILES
    for file_path in FILES:
        update_column_g_data(file_path)

        
