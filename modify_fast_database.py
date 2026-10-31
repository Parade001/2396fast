
from db import get_mssql_pool


update_table = ['c11$000000','r00${ym}','d11${ym}','o11$000000','m11${ym}','i11${ym}']; 

M = ['01','02','03','04','05','06','07','08','09','10','11','12']


def modify_fast_database(account_no):
    year = ['2024','2025']
    pool = get_mssql_pool()
    for y in year:
        for mm in M:
            ym = f'{y}{mm}'
            for table in update_table:
                table = table.replace('{ym}',ym)
                sql = "delete " + table + " where stt_rec in (select distinct stt_rec from ct00 where tk='"+account_no+"'); "
                pool.execute_none(sql)
    sql = "delete ct00 where tk='"+account_no+"';"
    pool.execute_none(sql)

if __name__ == '__main__':
    # year= input('请输入年份：')
    account_no = input('请输入账号：')
    modify_fast_database(account_no)
    print('修改完成')
