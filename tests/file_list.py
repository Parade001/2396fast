# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: tests/file_list.py
模块定位: 本地 Excel 结果文件扫描与归档移动辅助测试脚本
================================================================================
【本文件核心作用】
扫描当前工作目录下的 `.xlsx` 文件，并提供 `move_files` 将已处理或已上传的 Excel 文件移入 `output/` 目录。
================================================================================
"""
import os
import shutil
cwd =  os.getcwd()

FILES =  [ os.path.join(cwd, f) for f in os.listdir('.') if f.endswith('.xlsx')]

def move_files(file_name: str):
    # 移动文件到指定目录
    try:
        
        shutil.move(os.path.join(cwd, file_name), os.path.join(cwd, 'output'))
    except Exception as e:
        print(f"移动文件失败: {str(e)}")
        raise
    



