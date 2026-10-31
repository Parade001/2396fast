import time
import sys

from db import get_hana_pool,get_mssql_pool,HanaSQL,MSSQLSQL
import pandas as pd
from utils import (fiscalym_to_calendarym,
                    check_exist_voucher_no,
                    DataProcessor,
                    export_to_sap,
                    setup_logger
                    )
from config import AccountConfig,ProcessConfig

class DataReconciler:
    """
    使用方法：
    初始化：DataReconciler('202311')  输入会计期间yyyymm
    运行fetch_sap_to_fast()  导出可直接导入fast的凭证分录
    """

    def __init__(self,fiscalym:str):
        self.logger = setup_logger(__name__)
        self.sap_pool = get_hana_pool()
        self.fast_pool = get_mssql_pool()
        self.fiscalym = fiscalym
        self.calendarym = fiscalym_to_calendarym(fiscalym)
        self._get_foreign_currency_account_dict()
        

    def _get_foreign_currency_account_dict(self)->dict:
        # 获取外币科目        
        df = self.fast_pool.execute_query(MSSQLSQL.QUERY_TK)
        df['account_code'] = df['account_code'].str.strip()
        self.foreign_currency_account = df.to_dict(orient='records')       

    def _get_sap_fast_arising(self) -> None:
        # 获取sap、fast月发生额
        sapsql = HanaSQL.format_account_arising_month({
            "gjahr": self.fiscalym[:4],
            "monat": self.fiscalym[4:6]
        })        
        self.df_sap_account = self.sap_pool.execute_query(sapsql)
        # print(sapsql)

        fastsql = MSSQLSQL.format_account_arising_month({
            "ym": self.calendarym
        })
        # print(fastsql)
        self.df_fast_account = self.fast_pool.execute_query(fastsql)               
                    
    def compare_account_diff(self,account_mapping:dict=None)->list:
        # 获取sap、fast月发生额差异的account_code
        # 对比sap fast 科目发生额差额，获取差额科目清单
        # 根据差异清单，获取对应凭证号
        # 对比凭账号差异，获取缺失凭证号分录
        # 生成导入分录
        self._get_sap_fast_arising()
        df_fast = self.df_fast_account.copy()
        df_sap = self.df_sap_account.copy()   
        # 提取比较所需字段，重命名避免列冲突
        fast_compare = df_fast.rename(columns={'book_amount': 'book_amount_fast'})
        sap_compare = df_sap.rename(columns={'book_amount': 'book_amount_sap'})
        
        # 引进科目配置表
        if account_mapping is None:
            account_mapping=AccountConfig.config

        try:           
            #df.fillna('',inplace=True)
            # 1) fkber + account_code 映射: 把嵌套 dict 摊平为一张小表, 一次 left join
            fkber_rows = []
            for k, v in (account_mapping.get('fkber') or {}).items():
                if isinstance(v, dict):
                    for saccount, faccount in v.items():
                        fkber_rows.append({
                            'fkber': str(k),
                            'account_code': str(saccount),
                            'fast_account': str(faccount),
                        })
            if fkber_rows:
                fkber_map = pd.DataFrame(fkber_rows).drop_duplicates(
                    subset=['fkber', 'account_code'], keep='last'
                )
                df = sap_compare.merge(fkber_map, on=['fkber', 'account_code'],
                            how='left', suffixes=('', '__new'))
                if 'fast_account__new' in df.columns:
                    df['fast_account'] = df['fast_account__new'].fillna(df['fast_account'])
                    df.drop(columns=['fast_account__new'], inplace=True)

            # 2) account_code 映射: account 优先级更高 (在 fkber 之后覆盖)
            account_rows = [
                {'account_code': str(k), 'fast_account': str(v)}
                for k, v in (account_mapping.get('account') or {}).items()
            ]
            if account_rows:
                account_map = pd.DataFrame(account_rows).drop_duplicates(
                    subset=['account_code'], keep='last'
                )
                df = df.merge(account_map, on='account_code',
                            how='left', suffixes=('', '__new'))
                if 'fast_account__new' in df.columns:
                    df['fast_account'] = df['fast_account__new'].fillna(df['fast_account'])
                    df.drop(columns=['fast_account__new'], inplace=True)

            # 外币科目更新
            for account in self.foreign_currency_account:
                if account['account_code'][:3] in ['111','112']:
                    continue
                df.loc[(df['currency']==account['currency']) & (df['fast_account'].str[:-1]==account['account_code'][:-1]),'fast_account'] = account['account_code']

            df_sap = df.groupby('fast_account').agg({'book_amount_sap': 'sum'}).reset_index()
            df_fast=fast_compare.groupby('account_code').agg({'book_amount_fast': 'sum'}).reset_index()
            df_diff = df_sap.merge(df_fast, right_on='account_code',left_on='fast_account', how='left')
            df_diff.fillna({'book_amount_sap':0,'book_amount_fast':0},inplace=True)
            # df_diff.to_csv('compare.csv',index=False)
            # sys.exit(0)
            df_diff['book_amount_sap'] = df_diff['book_amount_sap'].astype(float)
            df_diff['book_amount_fast'] = df_diff['book_amount_fast'].astype(float)
            fast_list_account=df_diff.loc[df_diff['book_amount_fast']!=df_diff['book_amount_sap'],'fast_account'].unique().tolist()
            for ac in ['5152','6351']:
                if ac in fast_list_account:
                    continue
                else:
                    fast_list_account.append(ac)
            
            sap_list_account = df.loc[df['fast_account'].isin(fast_list_account),'account_code'].unique().tolist()
            for ac in ['6603050001','6603050003']:
                if ac in sap_list_account:
                    continue
                else:
                    sap_list_account.append(ac)
            
            
            # 根据科目清单，获取对应sap\fast分录的凭证号
            sap_accout_list_sql = HanaSQL.format_voucher_by_account({
                "account_codes":','.join([f"'{x}'" for x in sap_list_account]),
                "gjahr":self.fiscalym[:4],
                "monat":self.fiscalym[4:6] 
            })
            df_sap_accout_list = self.sap_pool.execute_query(sap_accout_list_sql)

            fast_accout_list_sql = MSSQLSQL.format_query_voucher_by_account({
                "account_codes":','.join([f"'{x}'" for x in fast_list_account]),
                "ym": self.calendarym
            })
            df_fast_accout_list = self.fast_pool.execute_query(fast_accout_list_sql)

            sap_voucher_nos = df_sap_accout_list['voucher_no'].astype(str).unique().tolist()
            fast_voucher_nos = df_fast_accout_list['voucher_no'].astype(str).unique().tolist()
            for fast_vno in fast_voucher_nos:
                if "_" in fast_vno: 
                    fast_voucher_nos.remove(fast_vno)
                    fast_voucher_nos.append(fast_vno.split('_')[0] if len(fast_vno.split('_')[0])>5 else fast_vno.split('_')[1])

            # 匹配规则：
            # 1) sap voucher_no 直接存在于 fast 凭证号列表中 → 匹配
            # 2) sap voucher_no 是某个 fast voucher_no 的子串 → 匹配（因为fast有_前后缀）
            # 找出SAP有但FAST中不存在的凭证号
            missing_voucher_nos = []
            for sap_vno in sap_voucher_nos:
                if sap_vno in fast_voucher_nos:
                    continue
                else:
                    missing_voucher_nos.append(sap_vno)

            missing_voucher_nos = check_exist_voucher_no(missing_voucher_nos,self.calendarym)
            # self.logger.info(f"获取缺失凭证号数量: {len(missing_voucher_nos)}条！")
            return  missing_voucher_nos
        except Exception as e:
            raise

        # 以df_sap为左表、account_code为键左连接，保留df_sap所有编码
        merged_df = sap_compare.merge(fast_compare, on='account_code', how='left')
        # 筛选差异：df_fast无对应编码 或 book_amount不相符
        diff_mask = merged_df['book_amount_fast'].isna() | (merged_df['book_amount_sap'] != merged_df['book_amount_fast'])
        # 提取df_sap中差异的account_code并转为集合返回
        return set(merged_df.loc[diff_mask, 'account_code'])
       
    def remove_fast_errors(self, gjahr: int, monat: int,so_ct:set[str]) -> None:
        # 移除fast差异科目数据
        pass  

    def fetch_sap_to_fast(self) -> None:   
        voucher_nos = self.compare_account_diff()
        if not voucher_nos:
            self.logger.info(f"会计期间{self.fiscalym} 无缺失凭证号！")
            return
        pool = get_hana_pool()
        step =500
        dfs=[]
        for i in range(0,len(voucher_nos),step):
            batch = voucher_nos[i:i + step]
            sql = HanaSQL.format_voucher_no({
                "voucher_nos":','.join([f"'{x}'" for x in batch]),  
                "gjahr":self.fiscalym[:4]            
            })
            df = pool.execute_query(sql)
            dfs.append(df)
        df = pd.concat(dfs, axis=0)
        cfg = ProcessConfig()
        processor = DataProcessor(cfg)
        df = processor.run(df)
        # df.to_excel('converted_result.xlsx', index=False)
        export_to_sap(df, filename=f"output/{self.fiscalym}_{time.time()}_added_data.xlsx")
        # self.logger.info(f"导出缺失凭证号: {len(voucher_nos)}条！")

    