# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: modules/sap_export.py
模块定位: SAP HANA 按录入日期 (cpudt) 增量抽取凭证并导出 43 列 FAST 模板模块
================================================================================
【本文件核心作用】
1. 查询越南 FAST 数据库中已导入的最新凭证日期 (`get_next_fast_date`)；
2. 换算对应的 SAP 财务期间并获取该账期内 SAP HANA 的起止录入日期 (`get_sap_begin_end_cupdt`)；
3. 按天循环从 SAP HANA 抽取原始 17 列凭证，调用 `DataProcessor` 完成 8 步转换，并调用 `export_to_sap`
   生成可直接导入越南 FAST 的 43 列 `.result.xlsx` 模板文件。
================================================================================
"""
import logging
import time

from config import StatusConfig,ProcessConfig
from db import get_hana_pool,HanaSQL
from utils import (get_next_fast_date,
                        calandarym_to_ficalym,
                        get_sap_begin_end_cupdt,
                        DataProcessor,
                        export_to_sap,
                        setup_logger
                        )

class SAPExporter:
    """
    使用方法：
    初始化：SAPExporter()  输入自然日期，如果不输入，取fast最新更新日期
    运行run()  导出可直接导入fast的凭证分录
    """

    def __init__(self, cpudt: str = ''):
        self.logger = setup_logger(__name__)   
        if cpudt == "":
            cpudt = get_next_fast_date()            
        # 转换为fiscalym格式
        fiscalym = calandarym_to_ficalym(cpudt)
        period ={
            "gjahr":fiscalym[:4],
            "monat":fiscalym[4:6]
        }
        if cpudt[-2:] == "01":
            earliest_cpudt,latest_cpudt = get_sap_begin_end_cupdt(fiscalym)
            cpudt = earliest_cpudt
        else:
            earliest_cpudt = cpudt
            latest_cpudt = cpudt
        
        self.status = StatusConfig(earliest_cpudt=earliest_cpudt,
                                latest_cpudt=latest_cpudt,
                                current_cupdt=cpudt,
                                current_period=period)
         
    def run(self)->None:        
        # 跑同步数据
        pconfig = ProcessConfig() # 处理参数
        self.logger.info(f"Loaded the processconfig")
        fiscalym = self.status.current_period["gjahr"] + self.status.current_period["monat"]
        processor = DataProcessor(pconfig)  # 数据处理器        
        conn = get_hana_pool()
        df = conn.execute_query(HanaSQL.format_voucher_by_cpudt(self.status.get_kwargs()))
        df = processor.run(df)
        export_to_sap(df, filename=f"output/{fiscalym}_{time.time()}_sync_data.xlsx")
        self.logger.info(f"Exported {fiscalym} data to excel")
        while self.status.next_cpudt():
            df = conn.execute_query(HanaSQL.format_voucher_by_cpudt(self.status.get_kwargs()))
            df = processor.run(df)
            export_to_sap(df, filename=f"output/{fiscalym}_{time.time()}_sync_data.xlsx")
            self.logger.info(f"Exported {fiscalym} data to excel")


