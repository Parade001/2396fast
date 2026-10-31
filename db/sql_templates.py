# -*- coding: utf-8 -*-
"""
SQL语句模板模块

所有SQL语句集中管理，便于维护和优化
"""

from string import Template
from typing import Optional, Dict, Any


# =============================================================================
# HANA SQL 模板
# =============================================================================

class HanaSQL:
    """HANA数据库SQL模板"""
    
    # -------------------------------------------------------------------------
    # 根据凭证号查询
    # -------------------------------------------------------------------------
    
    QUERY_VOUCHER_BY_VOUCHER_NO = """
        select a.cpudt, a.bukrs company_code, a.gjahr year, a.monat period, ltrim(a.belnr,'0') voucher_no, a.BLART voucher_type,
            b.hkont account_code, b.shkzg direction, sum(b.dmbtr) book_amount, sum(b.wrbtr) original_amount,
            '' cost_center, b.fkber , ltrim(b.lifnr,'0') lifnr,
            ltrim(b.kunnr,'0') kunnr, a.bktxt text_summary, '' zuonr, a.waers currency 
        from erp.bkpf a 
        inner join erp.bseg b on a.bukrs=b.bukrs and a.gjahr=b.gjahr and a.belnr=b.belnr  
        where  a.bukrs='2390' and a.gjahr='{gjahr}' and ltrim(a.belnr,'0') in ({voucher_nos} )       
            group by a.cpudt, a.bukrs, a.gjahr, a.monat, a.belnr, b.hkont, b.shkzg, b.fkber,
                b.lifnr, a.bktxt,  a.blart, b.kunnr, a.waers  
        order by voucher_no
        

    """
    
    # -------------------------------------------------------------------------
    # 根据录入日期查询
    # -------------------------------------------------------------------------

    QUERY_VOUCHER_BY_DATE = """
        select a.cpudt, a.bukrs company_code, a.gjahr year, a.monat period, ltrim(a.belnr,'0') voucher_no, a.BLART voucher_type,
            b.hkont account_code, b.shkzg direction, sum(b.dmbtr) book_amount, sum(b.wrbtr) original_amount,
            '' cost_center, b.fkber  , ltrim(b.lifnr,'0') lifnr,
            ltrim(b.kunnr,'0') kunnr, a.bktxt text_summary, '' zuonr, a.waers currency 
        from erp.bkpf a 
        inner join erp.bseg b on a.bukrs=b.bukrs and a.gjahr=b.gjahr and a.belnr=b.belnr  
        where  a.bukrs='2390' AND a.monat='{monat}' AND a.GJAHR ='{gjahr}' and a.cpudt='{cpudt}' 
        group BY a.cpudt, a.bukrs, a.gjahr , a.monat, ltrim(a.belnr,'0') , a.BLART, b.hkont , b.shkzg ,  b.fkber  , ltrim(b.lifnr,'0') ,
            ltrim(b.kunnr,'0') , a.bktxt ,  a.waers 
        order by voucher_no
    """
    # -----------------
    # 整月科目发生额查询
    # -----------------
    QUERY_ACOUNTARISING_BY_MONTH ="""
        select b.hkont account_code ,b.fkber ,sum(case when b.shkzg='S' then 1 else -1 end*b.dmbtr)*100 book_amount, a.waers currency  
        from erp.bkpf a inner join erp.bseg b on a.bukrs=b.bukrs and a.gjahr=b.gjahr and a.belnr=b.belnr
        where a.bukrs='2390' and a.gjahr={gjahr} and a.monat={monat} group by b.hkont,b.fkber,a.waers
    """

    # ------------------
    # 按科目代码查询
    #------------------------
    QUERY_VOUCHER_BY_ACCOUNT = """
        select distinct ltrim(a.belnr,'0') voucher_no
        from erp.bkpf a 
        inner join erp.bseg b on a.bukrs=b.bukrs and a.gjahr=b.gjahr and a.belnr=b.belnr  
        where  a.bukrs='2390' AND a.monat='{monat}' AND a.GJAHR ='{gjahr}' and b.hkont in ({account_codes}) 
        
    """
    QUERY_BEGIN_END_CPUDT = """
        select min(a.cpudt) begin_cpudt, max(a.cpudt) end_cpudt from erp.bkpf a where a.bukrs='2390' and a.gjahr={gjahr} and a.monat={monat}
    """

    QUERY_LATEST_CPUDT = """
        select max(a.cpudt) latest_cpudt from erp.bkpf a where a.bukrs='2390' and a.gjahr={gjahr} and a.monat={monat}
    """

    @classmethod
    def format_voucher_no(cls,kwargs) -> str:
        """格式化按凭证号查询"""
        return cls.QUERY_VOUCHER_BY_VOUCHER_NO.format(**kwargs)
    
    @classmethod
    def format_voucher_by_cpudt(cls,kwargs) -> str:
        """格式化日期范围查询"""
        return cls.QUERY_VOUCHER_BY_DATE.format(**kwargs)

    @classmethod
    def format_account_arising_month(cls,kwargs) -> str:
        """格式化按科目月发生额查询查询"""
        return cls.QUERY_ACOUNTARISING_BY_MONTH.format(**kwargs)

    @classmethod
    def format_voucher_by_account(cls,kwargs) -> str:
        """格式化按科目代码查询"""
        return cls.QUERY_VOUCHER_BY_ACCOUNT.format(**kwargs)
        
    @classmethod
    def format_begin_end_cpudt(cls,kwargs) -> str:
        """格式化按账期查询"""
        return cls.QUERY_BEGIN_END_CPUDT.format(**kwargs)



# =============================================================================
# MSSQL SQL 模板
# =============================================================================

class MSSQLSQL:
       
    # -------------------------------------------------------------------------
    # 按科目月发生额查询
    # -------------------------------------------------------------------------
    QUERY_ACCOUNTARISING_BY_MONTH = """
    select rtrim(a.tk, ' ') account_code,sum(a.ps_no-a.ps_co) book_amount,b.ma_nt currency from d11${ym} a 
    inner join m11${ym} b on a.stt_rec=b.stt_rec  group by a.tk,b.ma_nt    
    """

    # 根据凭证号查询
    QUERY_VOUCHER_BY_NO = """
        select distinct ltrim(so_ct, ' ') voucher_no from c11$000000
        where ltrim(so_ct, ' ') in ({voucher_no})   """

    # -------------------------------------------------------------------------
    # 按科目代码查询凭证号
    # -------------------------------------------------------------------------
    QUERY_VOUCHER_BY_ACCOUNT = """
    select distinct ltrim(so_ct, ' ') voucher_no from d11${ym}   where tk in ({account_codes})   """
    # 最新的更新日期
    QUERY_LATEST_DATE = """
        select max(datetime2) latest_date from 011$000000  """

    # -------------------------------------
    #  标记要删除的stt_rec
    # -------------------------------------    
    UPDATE_STT_REC = """update m11${ym} set status = 1 where  so_ct IN ({so_cts})  """


    # 外币科目
    QUERY_TK = """
    SELECT tk account_code ,ma_nt currency  FROM [TBL_FAWSX_SP229_App].[dbo].[dmtk] where ma_nt!='VND' and loai_tk=1 """

    # --------------------------------------
    # 删除标记stt_rec=1的记录
    # --------------------------------------
    DELETE_STT_REC = """
                DECLARE @TableName NVARCHAR(256);  
                DECLARE @SQL NVARCHAR(MAX);  
                declare @name nvarchar(50);                
                set @name = 'm11${ym}'                
                -- 创建一个表变量来存储要删除记录的表名  
                DECLARE @Tables TABLE (TableName NVARCHAR(256));                  
                -- 插入需要删除记录的表名  
                INSERT INTO @Tables (TableName)  
                VALUES   
                    ('c11$000000'),  
                    ('r00${ym}'),  
                    ('d11${ym}'),  
                    ('o11$000000'),   
                    ('i11${ym}'),  
                    ('ct00');                  
                -- 定义一个游标来遍历这些表  
                DECLARE TableCursor CURSOR FOR  
                SELECT TableName FROM @Tables;                  
                OPEN TableCursor;  
                FETCH NEXT FROM TableCursor INTO @TableName;                 
                WHILE @@FETCH_STATUS = 0  
                BEGIN  
                    BEGIN TRY  
                        -- 动态构造删除语句  
                        SET @SQL = 'DELETE FROM ' + QUOTENAME(@TableName) + ' WHERE stt_rec IN (select stt_rec
                from '+QUOTENAME(@name)+'
                where status=1)';                      
                    -- 执行删除语句  
                    EXEC sp_executesql @SQL;             
                    PRINT 'Deleted records from table: ' + @TableName;  
                END TRY  
                BEGIN CATCH  
                    -- 错误处理：记录错误信息  
                    PRINT 'Error occurred while deleting from table: ' + @TableName;  
                    PRINT 'Error Number: ' + CAST(ERROR_NUMBER() AS NVARCHAR);  
                    PRINT 'Error Message: ' + ERROR_MESSAGE();  
                END CATCH              
                FETCH NEXT FROM TableCursor INTO @TableName;  
            END              
            delete from m11${ym} where status=1;
            CLOSE TableCursor;  
            DEALLOCATE TableCursor;
            """
    
    @classmethod
    def format_account_arising_month(cls,kwargs) -> str:
        """生成月度科目余额表"""
        return cls.QUERY_ACCOUNTARISING_BY_MONTH.format(
            **kwargs
        )
    
    @classmethod
    def format_query_voucher_by_account(cls, kwargs) -> str:
        """生成按科目代码查询凭证号表"""
        return cls.QUERY_VOUCHER_BY_ACCOUNT.format(
            **kwargs
        )
    @classmethod
    def format_update_stt_rec(cls,kwargs) -> str:
        """更新要删除的stt_rec"""
        return cls.UPDATE_STT_REC.format(
            **kwargs
        )
    @classmethod
    def format_delete_stt_rec(cls, kwargs) -> str:
        """删除标记stt_rec=1的记录"""
        return cls.DELETE_STT_REC.format(
            **kwargs
        )

    @classmethod
    def format_query_voucher_by_no(cls, voucher_nos:str) -> str:
        """生成根据凭证号查询表"""
        return cls.QUERY_VOUCHER_BY_NO.format(
            voucher_no=voucher_nos
        )