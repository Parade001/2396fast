# -*- coding: utf-8 -*-

from config import ProcessConfig
from utils import DataProcessor
from modules import DataReconciler
from utils import setup_logger


def main():
    cfg = ProcessConfig()
    process = DataProcessor(cfg)
    file = 'converted_result.xlsx'
    df = process.run(file)
    df.to_excel('bkpf.xlsx', index=False)
    print(df)
    
def run():
    logger = setup_logger(__name__)

    yyyymm=['202408']
    for ym in yyyymm:
        d= DataReconciler(ym)
        d.fetch_sap_to_fast()
        logger.info(f"会计期间{ym} 导出完成")
        

if __name__ == "__main__":
    # main()
    run()
