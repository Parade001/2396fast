from db import get_mssql_pool


SQL_USD =[ "update r00${yyyymm} set tk={new_account} where tk={old_account} and ma_nt = 'USD'",
        "update r00${yyyymm} set tk_du={new_account} where tk_du={old_account} and ma_nt = 'USD'",
        "update d11${yyyymm} set tk={new_account} from m11${yyyymm} a,d11${yyyymm} b where a.stt_rec=b.stt_rec and a.ma_nt='USD' and b.tk={old_account}",
        "update i111${yyyymm} set d$=replace(d$,'{old_account}','{new_account}') where d$ like '%{old_account}%'  and m$='#10$USD' ", 
        "update ct00 set tk={new_account} where tk={old_account} and ma_nt = 'USD'" ,
        "update ct00 set tk_du={new_account} where tk_du={old_account} and ma_nt = 'USD'"
        ]
SQL = [ "update r00${yyyymm} set tk={new_account} where tk={old_account} ",
        "update r00${yyyymm} set tk_du={new_account} where tk_du={old_account} ",
        "update d11${yyyymm} set tk={new_account} from m11${yyyymm} a,d11${yyyymm} b where a.stt_rec=b.stt_rec and  b.tk={old_account}",
        "update i111${yyyymm} set d$=replace(d$,'{old_account}','{new_account}') where d$ like '%{old_account}%'  ", 
        "update ct00 set tk={new_account} where tk={old_account} " ,
        "update ct00 set tk_du={new_account} where tk_du={old_account}"
        ]

mm= ['01','02','03','04','05','06','07','08','09','10','11','12']

def update_account()->None:
    """
    更新科目余额表中的科目余额
    :param old_account: 旧科目
    :param new_account: 新科目
    :param currency: 货币，默认USD
    :return: None
    """
    print('======================================')
    print('更新流程：')
    print('1. 收入年份；')
    print('2. 选择更新方式：全部更新或针对USD业务更新科目代码')
    print('3. 按E退出')
    print('======================================')
    pool = get_mssql_pool()
    
    while 1:
        yyyy  = input('请输入年份（四位数字，如2024）：')
        if str(yyyy).upper()=="E":
            break
        if not 2023<int(yyyy) < 2030 :
            print("请输入正确的年份，年份大于2023!")
            continue
        else:
            print(f"已确定年份：{yyyy}年/n")
         
        select  = input('请选择更新方式（选择数字即可:\n1-只更新USD币种的科目；\n2-更新所有币种科目）：')
        if select not in ['1','2']:
            print("请输入正确的更新方式!")
            continue
        else:
            print(f"确定更新年份：{yyyy}年，更新方式：{select}")

        account = input('请输入旧科目-新科目（例如：131111-131112）：')
        if '-' not in account:
            print("请输入正确的科目格式!")
            continue

        old_account,new_account = account.split('-')
        print(f"确定更新年份：{yyyy}年，\n更新方式：{select}，\n旧科目：{old_account}，\n新科目：{new_account}")
        confirm=input('确认更新？（Y/N）：')
        if confirm.upper()!='Y':
            print("更新已取消!")
            continue
               
        print('执行更新程序...')
        with pool.begin_transaction() as tx:
            for m in mm:
                yyyymm = f"{yyyy}{m}"
                for sql in SQL_USD if str(select) == '1' else SQL:
                    print(sql.format(yyyymm=yyyymm,old_account=old_account,new_account=new_account))
                    # tx.execute(sql.format(yyyymm=yyyymm,old_account=old_account,new_account=new_account))
                # tx.commit()
                print(f"更新{yyyymm}完成!")
    
if __name__ == '__main__':
    update_account()
