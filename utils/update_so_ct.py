import pandas as pd

from db import get_mssql_pool,MSSQLSQL

def update_voucher_no(df: pd.DataFrame):
    """更新凭证号"""
    df=df.copy()
    pool = get_mssql_pool()
    lenght = 1000
    # 取voucher_no列转成列表
    voucher_no_list = df['voucher_no'].tolist()

    
    # 分批处理，每批500条记录
    for i in range(0, len(voucher_no_list), lenght):
        batch = voucher_no_list[i:i + lenght]

        # 构造IN查询占位符
        placeholders = ','.join(['%s'] * len(batch))
        # 查询数据库中存在的voucher_no
        query = MSSQLSQL.format_query_voucher_by_no(placeholders)
        found_df = pool.execute_query(query, tuple(batch))

        # 获取已存在的voucher_no集合
        found_set = set(found_df['voucher_no'].tolist()) if not found_df.empty else set()

        # 遍历批次中的每个voucher_no
        for v_no in batch:
            if v_no  in found_set:
                # 数据库中存在的记录，尝试加后缀继续查询
                # 从字符'A'开始逐个尝试后缀
                suffix_char = 'A'
                while True:
                    suffix_voucher_no = f"{v_no}_{suffix_char}"
                    suffix_query = MSSQLSQL.format_query_voucher_by_no("%s")
                    suffix_result = pool.execute_query(suffix_query, (suffix_voucher_no,))
                    if suffix_result.empty:
                        # 加后缀也查询不到，更新
                        df.loc[df['voucher_no'] == v_no, 'voucher_no'] = suffix_voucher_no
                        break
                    # 尝试下一个字符后缀
                    suffix_char = chr(ord(suffix_char) + 1)
        return df
def check_exist_voucher_no(voucher_nos: list,ym:str):
    """检查凭证号是否存在"""
    result = voucher_nos[:]
    pool = get_mssql_pool()
    step = 500
    for i in range(0, len(voucher_nos), step):
        batch = voucher_nos[i:i + step]
        vouchers = ','.join([f"('%{v}%')" for v in batch])            
        with pool.cursor() as cursor:
            sql = f"""
                    DECLARE @tmp TABLE (pat NVARCHAR(20));
                    INSERT INTO @tmp VALUES {vouchers} ;

                    select ltrim(so_ct,' ') voucher_no from [m11${ym}] t
                    WHERE EXISTS (
                        SELECT 1 FROM @tmp p
                        WHERE t.so_ct LIKE p.pat
                    );
                    """
            # print(sql)
            cursor.execute(sql)
            tmp = cursor.fetchall()
            if not tmp:
                continue
            for p in tmp:
                _p = p[0]
                if '_' in _p:
                    _p = _p.split('_')[0] if len(_p.split('_')[0])>3 else _p.split('_')[1]
                if '-' in  _p :
                    _p = _p.split('-')[0] if len(_p.split('-')[0])>3 else _p.split('-')[1]
                if _p in result:    
                    result.remove(_p)
    return result
