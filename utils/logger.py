# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: utils/logger.py
模块定位: 全局统一日志配置与按日滚动文件输出模块
================================================================================
【本文件核心作用】
提供 `setup_logger(name, log_dir, level)` 函数，同时向控制台 (`StreamHandler`) 与
`./logs/{name}_{YYYYMMDD}.log` 文件 (`FileHandler`, UTF-8 编码) 输出格式化运行日志。
================================================================================
"""
import logging
import os
from datetime import datetime


def setup_logger( name:str=None,log_dir: str = './logs', level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if logger.handlers:
        return logger
    
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f"{name}_{datetime.now().strftime('%Y%m%d')}.log")
    
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger