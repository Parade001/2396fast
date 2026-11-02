from utils.general import (
    resolve_period_date,
    get_next_fast_date,
    fiscalym_to_calendarym,
    calandarym_to_ficalym,
    get_sap_begin_end_cupdt,
    safe_eq,
)
from utils.export import export_to_sap
from utils.dataframe_handler import DataProcessor
from utils.update_so_ct import check_exist_voucher_no,update_voucher_no
from utils.logger import setup_logger

# 以下为浏览器自动化模块，依赖 requests / websocket / pyautogui / pyperclip 等
# 可选依赖。它们仅用于「FAST 网页登录 / 上传 / 过账」链路，与核心数据处理、导出无关；
# 缺失时不应阻断 `from utils import DataProcessor` 等核心导入。
try:
    from utils.chrome_launcher import ChromeLauncher
    from utils.cdp_client import CDPClient
    from utils.login_handler import LoginHandler
    from utils.upload_handler import UploadHandler
    from utils.posting_handler import PostingHandler
except ImportError:  # pragma: no cover - 可选依赖未安装
    ChromeLauncher = None
    CDPClient = None
    LoginHandler = None
    UploadHandler = None
    PostingHandler = None