import pandas as pd
from openpyxl import Workbook
from utils import resolve_period_date,safe_eq
from utils.update_so_ct import update_voucher_no    



def export_to_sap( df: pd.DataFrame, filename: str = '') -> None:
    if df.empty:
        return 
    # df.to_excel('converted_result.xlsx', index=False)
    df = update_voucher_no(df) # 更新重复的voucher_no
    wk = Workbook()
    sh = wk.active
    # sh.title = 'import data'
    split_num = 20000 # 最小excel记录条数
    file_no = 1
    # 设置所有列的宽度为20
    belnr=''
    radd = 0

    for row, data in enumerate(df.itertuples(index=False), start=6):
        # 达到excel记录条控制
        if row >= split_num*file_no and not safe_eq(data[4], belnr):
            wk.save(filename + '_%d.result.xlsx' % file_no)
            file_no +=1
            wk = Workbook()
            sh = wk.active
            radd =6-row

        # 写入明细
        currency  = data[16]
        sh.cell(row + radd, 1, "CTY")
        sh.cell(row + radd, 2, data[4])  #voucher_no
        sh.cell(row + radd, 3, resolve_period_date(data[0],int(data[2]),int(data[3]))) # date
        sh.cell(row + radd, 4, currency)  #currency
        sh.cell(row + radd, 5, 1 if currency== 'VND' else data[18]) #ex
        sh.cell(row + radd, 7, data[17])  # account_no
        if currency!= 'VND':
            sh.cell(row + radd, 11 if data[7]=='S' else 12, data[9]) # original amount
        sh.cell(row + radd, 13 if data[7]== 'S' else 14, data[8]) # book_amount
        sh.cell(row + radd, 9, str(data[12])+ str(data[13])) #客商信息
        sh.cell(row + radd, 15, " ".join([str(data[14]),str(data[15])]) ) #summary
        # 安全处理 NA：当 data[19] 是 pandas.NA/NaN/None 时返回空，否则原样写入
        sh.cell(row + radd, 43, data[19] if pd.notna(data[19]) else '')
        belnr=data[4]
                
    wk.save(filename+ '_%d.result.xlsx' % file_no)
