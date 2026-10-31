import argparse
from modules import DataReconciler
import arrow


def sync_sap(args):
    """同步SAP数据"""
    cpudt=args.date
    try:
        arrow.get(cpudt, "YYYYMMDD")
    except ValueError:
        print(f"[同步SAP] 日期格式错误: 格式必须为YYYYMMDD")
        return
    print(f"[同步SAP] 日期: {cpudt}")
    # 在此处编写实际的同步SAP逻辑
    exporter = SAPExporter(cpudt)
    exporter.run()


def monthly_reconciliation(args):
    """月度对账"""
    ym=args.yyyyymm
    if 2055<int(ym[:4]) or int(ym[:4])<2023 or int(ym[4:6])>12 or int(ym[4:6])<1:
        print(f"[月度对账] 财务账期格式错误: 格式必须为YYYYMM")
        return
    rc = DataReconciler(ym)
    rc.fetch_sap_to_fast()
    # 在此处编写实际的月度对账逻辑

def postfast_page(args):
    """PostFast页面处理"""
    print(f"[PostFast页面] URL: {args.url}, 操作: {args.action}")
    # 在此处编写实际的PostFast页面逻辑


def upload_file(args):
    """上传文件"""
    print(f"[上传文件] 文件: {args.file}, 目标: {args.target}")
    # 在此处编写实际的上传文件逻辑


def parse_args():
    parser = argparse.ArgumentParser(description="CLI执行工具 - 支持多种自动化任务")
    subparsers = parser.add_subparsers(dest="command", help="可用命令列表")

    # 同步SAP命令
    parser_sync = subparsers.add_parser("sync-sap", help="同步SAP数据")
    parser_sync.add_argument("--date", type=str, default=None, help="同步日期 (格式: YYYYMMDD)")
  

    # 月度对账命令
    parser_recon = subparsers.add_parser("reconcile", help="执行月度对账")
    parser_recon.add_argument("--yyyymm", type=str, required=True, help="指定财务账期 (格式: YYYYMM)")
    

    # PostFast页面命令
    parser_postfast = subparsers.add_parser("postfast", help="PostFast页面操作")
    parser_postfast.add_argument("--url", type=str, default=None, help="PostFast页面URL")
    parser_postfast.add_argument("--action", type=str, choices=["export", "import", "refresh"], default="export", help="页面操作类型")

    # 上传文件命令
    parser_upload = subparsers.add_parser("upload", help="上传文件")
    parser_upload.add_argument("file", type=str, help="要上传的文件路径")
    parser_upload.add_argument("--target", type=str, default="default", help="上传目标位置")

    return parser.parse_args()


def main():
    args = parse_args()

    if args.command == "sync-sap":
        sync_sap(args)
    elif args.command == "reconcile":
        monthly_reconciliation(args)
    elif args.command == "postfast":
        postfast_page(args)
    elif args.command == "upload":
        upload_file(args)
    else:
        print("请指定要执行的命令，使用 --help 查看帮助信息")


if __name__ == "__main__":
    main()

