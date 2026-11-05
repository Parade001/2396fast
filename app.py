import os
import glob
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

import openpyxl
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sap2fast_full_fix as s2f
from tests._smoke_test import run_smoke_test

BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="SAP2FAST 跨国财务转换与对账系统 (1104全量版)")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ConvertOptionsRequest(BaseModel):
    pattern: str = "samples/sample_raw_sap.xlsx"
    cross_month_date_mode: str = "period_end"
    cost_center_col: Optional[int] = None
    offline_voucher_dedup: bool = True


class MappingActionRequest(BaseModel):
    action: str  # add-account | add-partner | update-account | update-partner
    sap_code: str
    fast_code: str
    fkber: Optional[str] = None
    partner_type: str = "vendor"
    force_multi: bool = False
    partner_sync_scope: str = "d11,ct00"
    dry_run: bool = True


class DbMaintenancePreviewRequest(BaseModel):
    op_type: str  # delete-account | update-account | remove-fast-errors
    fast_code: str = "6428"
    old_fast_code: str = "131111"
    new_fast_code: str = "131112"
    scope: str = "ALL"
    fiscalym: str = "202403"
    vouchers: str = "1000000001,1000000002"


def _read_template_preview(xlsx_path: str, max_rows: int = 25) -> List[Dict[str, Any]]:
    if not os.path.exists(xlsx_path):
        return []
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    records = []
    for r in range(6, min(ws.max_row + 1, 6 + max_rows)):
        cty = ws.cell(r, 1).value
        if not cty:
            continue
        records.append({
            "excel_row": r,
            "col_1_cty": cty,
            "col_2_voucher_no": ws.cell(r, 2).value or "",
            "col_3_posting_date": ws.cell(r, 3).value or "",
            "col_4_currency": ws.cell(r, 4).value or "",
            "col_5_ex_rate": ws.cell(r, 5).value if ws.cell(r, 5).value is not None else "",
            "col_7_fast_account": ws.cell(r, 7).value or "",
            "col_9_partner": ws.cell(r, 9).value or "",
            "col_11_dr_orig": ws.cell(r, 11).value if ws.cell(r, 11).value is not None else 0,
            "col_12_cr_orig": ws.cell(r, 12).value if ws.cell(r, 12).value is not None else 0,
            "col_13_dr_vnd": ws.cell(r, 13).value if ws.cell(r, 13).value is not None else 0,
            "col_14_cr_vnd": ws.cell(r, 14).value if ws.cell(r, 14).value is not None else 0,
            "col_15_summary": ws.cell(r, 15).value or "",
            "col_43_group_id": ws.cell(r, 43).value if ws.cell(r, 43).value is not None else "",
        })
    return records


def _read_audit_logs(log_filename: str, limit: int = 30) -> List[Dict[str, Any]]:
    log_path = BASE_DIR / "logs" / log_filename
    if not log_path.exists():
        return []
    items = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(items[-limit:]))


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "version": "1104-final"}


@app.get("/api/overview")
async def get_overview() -> Dict[str, Any]:
    cfg = s2f.AccountConfig.load(force_reload=True)
    acc_map = cfg.get("account") or {}
    fkber_map = cfg.get("fkber") or {}
    fkber_total = sum(len(v) for v in fkber_map.values() if isinstance(v, dict))
    input_files = sorted(glob.glob(str(BASE_DIR / "input" / "*.xlsx")))
    output_files = sorted(glob.glob(str(BASE_DIR / "output" / "*.xlsx")))

    report_csv = BASE_DIR / "output" / "offline_audit_report.csv"
    audit_rows = []
    if report_csv.exists():
        df_rep = pd.read_csv(report_csv).fillna("")
        audit_rows = df_rep.to_dict(orient="records")

    sample_result = str(BASE_DIR / "output" / "fixed_sample_raw_sap.xlsx_1.result.xlsx")
    preview_rows = _read_template_preview(sample_result, max_rows=20)

    return {
        "version": "1104 终极全量闭环版",
        "account_mapping_count": len(acc_map),
        "fkber_mapping_count": fkber_total,
        "account_6051000001_target": acc_map.get("6051000001", "51181"),
        "input_file_count": len(input_files),
        "output_file_count": len(output_files),
        "audit_rows": audit_rows,
        "template_preview": preview_rows,
        "mapping_audit_logs": _read_audit_logs("mapping_change_audit.log", 15),
        "db_audit_logs": _read_audit_logs("db_maintenance_audit.log", 15),
    }


@app.post("/api/smoke-test")
async def api_smoke_test() -> Dict[str, Any]:
    try:
        res = run_smoke_test()
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/sample-convert")
async def api_sample_convert(req: ConvertOptionsRequest) -> Dict[str, Any]:
    try:
        sample_path = s2f.generate_raw_sap_sample_file()
        cfg = s2f.ProcessConfig(
            offline_voucher_dedup=req.offline_voucher_dedup,
            cross_month_date_mode=req.cross_month_date_mode,
            cost_center_col=req.cost_center_col,
        )
        target_pattern = req.pattern.strip() or sample_path
        exported = s2f.reprocess_local_files(
            input_pattern=target_pattern,
            output_dir="output",
            process_cfg=cfg,
        )
        s2f.audit_local_files(input_pattern="output/*.xlsx", output_dir="output")
        preview_file = exported[0] if exported else str(BASE_DIR / "output" / "fixed_sample_raw_sap.xlsx_1.result.xlsx")
        preview_rows = _read_template_preview(preview_file, max_rows=25)
        return {
            "ok": True,
            "exported_files": [os.path.basename(p) for p in exported],
            "preview_file": os.path.basename(preview_file),
            "preview_rows": preview_rows,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/audit")
async def api_run_audit(pattern: str = "input/*.xlsx,output/*.xlsx") -> Dict[str, Any]:
    try:
        report_path = s2f.audit_local_files(input_pattern=pattern, output_dir="output")
        rows = []
        if report_path and os.path.exists(report_path):
            rows = pd.read_csv(report_path).fillna("").to_dict(orient="records")
        return {"ok": True, "report_path": report_path, "rows": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/mappings")
async def api_get_mappings(search: str = "", fkber: str = "") -> Dict[str, Any]:
    cfg = s2f.AccountConfig.load(force_reload=True)
    q = search.strip().lower()
    rows = []

    if not fkber or fkber == "account":
        for sap_c, fast_c in (cfg.get("account") or {}).items():
            s_str, f_str = str(sap_c), str(fast_c)
            if not q or q in s_str.lower() or q in f_str.lower():
                rows.append({"scope": "account (二级高优)", "fkber": "-", "sap_code": s_str, "fast_code": f_str})

    for fk_key, sub in (cfg.get("fkber") or {}).items():
        if fkber and fkber not in ("", "all", str(fk_key)):
            continue
        if isinstance(sub, dict):
            for sap_c, fast_c in sub.items():
                s_str, f_str = str(sap_c), str(fast_c)
                if not q or q in s_str.lower() or q in f_str.lower() or q in str(fk_key).lower():
                    rows.append({"scope": f"fkber:{fk_key}", "fkber": str(fk_key), "sap_code": s_str, "fast_code": f_str})

    partners = []
    for p_type in ("vendor", "customer"):
        for k, v in (cfg.get(p_type) or {}).items():
            partners.append({"partner_type": p_type, "sap_partner": str(k), "fast_partner": str(v)})

    return {
        "total": len(rows),
        "rows": rows[:150],
        "partners": partners,
        "audit_logs": _read_audit_logs("mapping_change_audit.log", 20),
    }


@app.post("/api/mappings/action")
async def api_mapping_action(req: MappingActionRequest) -> Dict[str, Any]:
    sap_c = req.sap_code.strip()
    fast_c = req.fast_code.strip()
    if not sap_c or not fast_c:
        raise HTTPException(status_code=400, detail="SAP 代码与目标 FAST 代码均不能为空")

    mgr = s2f.MappingManager()
    sync_tables = tuple(x.strip() for x in req.partner_sync_scope.split(",") if x.strip())
    ok = False
    msg = ""

    if req.action == "add-account":
        ok = mgr.add_account_mapping(sap_c, fast_c, fkber=req.fkber or None)
        msg = f"新增科目映射 {sap_c} -> {fast_c} {'成功' if ok else '被拦截（该 SAP 科目已存在映射，请使用修改操作）'}"
    elif req.action == "add-partner":
        ok = mgr.add_partner_mapping(sap_c, fast_c, partner_type=req.partner_type)
        msg = f"新增客商映射 {sap_c} -> {fast_c} {'成功' if ok else '被拦截（目标 FAST 客商代码已存在）'}"
    elif req.action == "update-partner":
        ok = mgr.update_partner_mapping(
            sap_c,
            fast_c,
            partner_type=req.partner_type,
            sync_tables=sync_tables,
            dry_run=req.dry_run,
        )
        msg = (
            f"修改客商映射 {sap_c} -> {fast_c} (同步范围={sync_tables}, DryRun={req.dry_run}) "
            f"{'通过' if ok else '被拦截（目标 FAST 客商代码已存在）'}"
        )
    elif req.action == "update-account":
        ok = mgr.update_account_mapping(
            sap_c,
            fast_c,
            fkber=req.fkber or None,
            force_multi=req.force_multi,
            dry_run=req.dry_run,
        )
        msg = (
            f"修改科目映射 {sap_c} -> {fast_c} (force_multi={req.force_multi}, DryRun={req.dry_run}) "
            f"{'通过' if ok else '被 [6.22 多对一安全锁] 拦截（或原科目不存在），如需强制同步请勾选允许修改多对一映射'}"
        )
    else:
        raise HTTPException(status_code=400, detail=f"未知映射操作: {req.action}")

    return {
        "ok": ok,
        "message": msg,
        "audit_logs": _read_audit_logs("mapping_change_audit.log", 20),
    }


@app.post("/api/db-maintenance/preview")
async def api_db_maintenance_preview(req: DbMaintenancePreviewRequest) -> Dict[str, Any]:
    if req.op_type == "delete-account":
        if not req.fast_code.strip():
            raise HTTPException(status_code=400, detail="请填写要清理的 FAST 科目代码")
        res = s2f.modify_fast_database(req.fast_code.strip(), dry_run=True)
        return {"ok": True, "preview": res, "audit_logs": _read_audit_logs("db_maintenance_audit.log", 15)}
    elif req.op_type == "update-account":
        if not req.old_fast_code.strip() or not req.new_fast_code.strip():
            raise HTTPException(status_code=400, detail="请填写旧 FAST 科目与新 FAST 科目代码")
        res = s2f.update_fast_account(
            req.old_fast_code.strip(),
            req.new_fast_code.strip(),
            scope=req.scope,
            dry_run=True,
        )
        return {"ok": True, "preview": res, "audit_logs": _read_audit_logs("db_maintenance_audit.log", 15)}
    elif req.op_type == "remove-fast-errors":
        v_set = set(x.strip() for x in req.vouchers.split(",") if x.strip())
        if not v_set or len(req.fiscalym.strip()) < 6:
            raise HTTPException(status_code=400, detail="请提供有效的 6 位财务期间 (YYYYMM) 及待删凭证号")
        ym = req.fiscalym.strip()
        rec = s2f.DataReconciler(ym)
        cnt = rec.remove_fast_errors(int(ym[:4]), int(ym[4:6]), v_set, dry_run=True)
        cal_ym = s2f.fiscalym_to_calendarym(ym)
        v_str = ",".join([f"'{v}'" for v in sorted(v_set)])
        return {
            "ok": True,
            "preview": {
                "dry_run": True,
                "fiscal_ym": ym,
                "calendar_ym": cal_ym,
                "voucher_count": cnt,
                "sql_count": 2,
                "sql_preview": [
                    f"UPDATE m11${cal_ym} SET status = 1 WHERE so_ct IN ({v_str});",
                    f"-- 游标级联删除 c11$000000, r00${cal_ym}, d11${cal_ym}, o11$000000, i11${cal_ym}, ct00 及 m11${cal_ym} 中 status=1 的记录",
                ],
            },
            "audit_logs": _read_audit_logs("db_maintenance_audit.log", 15),
        }
    raise HTTPException(status_code=400, detail=f"未知操作类型: {req.op_type}")


@app.get("/api/docs/{doc_key}")
async def api_get_doc(doc_key: str) -> Dict[str, str]:
    doc_map = {
        "eval_1104": "功能对比、评估报告1104.md",
        "accept_1104": "需求6-7实现验收报告1104.md",
        "readme": "README.md",
        "cfo_1103": "1103_CFO会议纪要_正式版.md",
        "cfo_req": "1103与CFO会议纪要及需求更新.md",
    }
    fname = doc_map.get(doc_key)
    if not fname:
        raise HTTPException(status_code=404, detail="未找到指定文档")
    fpath = BASE_DIR / fname
    if not fpath.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {fname}")
    return {"title": fname, "content": fpath.read_text(encoding="utf-8", errors="ignore")}


@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>SAP2FAST 1104 Workbench</h1>")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
