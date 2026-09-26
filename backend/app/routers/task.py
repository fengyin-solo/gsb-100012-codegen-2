"""检测任务接口：维护检测任务单，覆盖分配任务、开始检测、提交复核等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, BatchActionPayload, BatchActionResult, EntryPayload, PageResult
from app.services.task import TaskService

router = APIRouter(prefix="/api/task", tags=["检测任务"])

service = TaskService()

LIST_FIELDS = ["任务编号", "所属样品", "检测项目", "检测标准", "指定检测员", "截止日期", "优先级", "任务状态"]
STATUSES = ["待分配", "已分配", "检测中", "已完成", "已复核"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="待分配、已分配、检测中、已完成、已复核"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号与状态过滤检测任务列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 注意：summary / export / batch-actions 必须放在 /{entry_id} 之前，
# 否则 "summary"、"export" 会被当成 entry_id 解析，直接 422。
@router.get("/summary")
def summary() -> dict[str, int]:
    """状态统计：列表页指标卡的数据源，与列表筛选口径一致。"""
    return service.summary()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出检测任务清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "task", "total": total, "items": items}


@router.post("/batch-actions", response_model=BatchActionResult)
def run_batch_action(payload: BatchActionPayload) -> BatchActionResult:
    """批量执行分配任务、开始检测、提交复核。

    逐条处理、逐条返回结果：单条失败只影响自己，不会拖累同批其它记录；
    重复提交按幂等处理，已是目标状态的记录记为跳过；空选择直接说明原因。
    """
    action = payload.action.strip()
    if not payload.ids:
        return BatchActionResult(
            ok=False,
            action=action,
            message="未选择任何检测任务单，批量操作未执行",
        )
    receipt = service.run_batch_action(payload.ids, action)
    return BatchActionResult(**receipt)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条检测任务单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"检测任务单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条检测任务单，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="检测任务单已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条检测任务单执行分配任务、开始检测、提交复核；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
