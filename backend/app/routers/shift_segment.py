"""采样责任段接口：维护责任段数据集，裁定迟到回执归属，并输出分班采集报告。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.shift_segment import ShiftSegmentService

router = APIRouter(prefix="/api/shift_segment", tags=["采样责任段"])

service = ShiftSegmentService()

LIST_FIELDS = ["段编号", "样本编号", "站点", "采样时间", "接入时间", "跨交接点", "归属依据", "采用时点", "班次编号", "计入方式", "原接入批次"]
STATUSES = ["已计入", "待裁定", "已更正", "已补录", "不计入"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采样责任段清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "shift_segment", "total": total, "items": items}


@router.get("/report")
def collection_report() -> dict[str, Any]:
    """分班采集报告：各班负责的站点、有效观测分钟、可归责读数与来源小计。"""
    return service.shift_report()


@router.post("/report")
def generate_collection_report() -> dict[str, Any]:
    """依据采样责任段数据集生成一份交班采集报告，写回采集报告模块备查。"""
    entry, report = service.generate_collection_report()
    return {
        "ok": True,
        "message": f"交班采集报告 {entry['报告编号']} 已生成，样本编号与时间凭据留在采样责任段数据集备查",
        "entry": entry,
        "report": report,
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按段编号或样本编号检索"),
    status: str | None = Query(default=None, description="已计入、待裁定、已更正、已补录、不计入"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按编号与状态过滤采样责任段列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条采样责任段明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采样责任段 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条新到的源端读数，归属班次与计入方式由口径自动判定。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"登记未通过：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"读数已归属到 {entry['班次编号']}，计入方式：{entry['计入方式']}", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对迟到回执执行前班更正或交班账补录；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
