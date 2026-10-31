from database import HANAConnector
from utils import DataProcessor
import pandas as pd


def main():    
    conn = HANAConnector()
    conn.connect()
    dp = DataProcessor()
    sql="""
    select a.cpudt, a.bukrs company_code, a.gjahr year, a.monat month, ltrim(a.belnr,'0') voucher_no, a.BLART voucher_type,
            b.hkont account_code, b.shkzg direction, b.dmbtr book_amount, b.wrbtr original_amount,
            b.kostl cost_center, b.fkber  , ltrim(b.lifnr,'0') lifnr,b.BUZEI line_no,
            ltrim(b.kunnr,'0') kunnr, b.sgtxt text_summary, b.zuonr, a.waers currency 
        from erp.bkpf a 
        inner join erp.bseg b on a.bukrs=b.bukrs and a.gjahr=b.gjahr and a.belnr=b.belnr  
        where  a.bukrs='2390'AND a.monat='09' AND a.GJAHR =2024 
        ORDER BY b.BUZEI 
      
        """
    df = pd.read_sql_query(sql, conn.engine)
    df =dp.run(df)
    df.to_excel('bkpf.xlsx', index=False)

if __name__ == '__main__':
    main()