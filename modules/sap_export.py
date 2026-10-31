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
"""同步数据
1、获取fast最新的更新日期
2、根据更新日期，获取sap的开始和结束日期
3、根据开始和结束日期，查询fast的数据
4、处理数据
5、导出数据到excel
6、重复以上步骤，直到处理完所有数据

"""

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


