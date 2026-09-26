"""检测任务业务规则：状态流转、字段校验、批量处理与统计口径都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "所属样品", "检测项目"]
STATUS_ORDER = ["待分配", "已分配", "检测中", "已完成", "已复核"]
ACTION_RULES: dict[str, dict[str, Any]] = {
    "分配任务": {"target": "已分配", "allowed": {"待分配"}},
    "开始检测": {"target": "检测中", "allowed": {"已分配"}},
    "提交复核": {"target": "已复核", "allowed": {"检测中", "已完成"}},
}
NEGATIVE_ACTIONS = []
_IDEMPOTENT_ACTIONS: dict[str, tuple[str, tuple[int, ...], dict[str, Any]]] = {}


class TaskService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        effective_page = min(max(page, 1), max(1, (total + size - 1) // size))
        start = (effective_page - 1) * size
        return [self._as_view(row) for row in rows[start:start + size]], total, effective_page

    def metrics(self) -> dict[str, int]:
        """统计卡片和列表共用状态口径，避免页面数字与清单状态不一致。"""
        rows = store.rows(MODULE)
        today = date.today().isoformat()
        return {
            "total": len(rows),
            "pending_assignment": sum(1 for row in rows if row.get("status") == "待分配"),
            "in_progress": sum(1 for row in rows if row.get("status") == "检测中"),
            "overdue": sum(
                1
                for row in rows
                if row.get("status") != "已复核"
                and str(row.get("截止日期") or "") < today
            ),
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._as_view(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        optional_fields = ["检测标准", "指定检测员", "截止日期", "优先级"]
        entry.update({field: values.get(field) for field in optional_fields if values.get(field) is not None})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._as_view(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务单 {entry_id} 不存在或已归档"
        ok, message = self._apply_action(entry, action)
        return (self._as_view(entry) if ok else None), message

    def run_batch_action(
        self,
        *,
        action: str,
        ids: list[int],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """逐条执行批量动作；单条失败只记录该项结果，不影响其余任务继续处理。"""
        normalized_action = action.strip()
        unique_ids: list[int] = []
        seen: set[int] = set()
        for entry_id in ids:
            if entry_id in seen:
                continue
            seen.add(entry_id)
            unique_ids.append(entry_id)

        signature = (normalized_action, tuple(unique_ids))
        if idempotency_key:
            cached = _IDEMPOTENT_ACTIONS.get(idempotency_key)
            if cached is not None:
                cached_action, cached_ids, cached_result = cached
                if (cached_action, cached_ids) != signature:
                    raise ValueError("相同幂等编号提交了不同的任务或动作，请刷新后重试")
                result = dict(cached_result)
                result["idempotent"] = True
                return result

        items: list[dict[str, Any]] = []
        succeeded = 0
        for entry_id in unique_ids:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                items.append({
                    "id": entry_id,
                    "ok": False,
                    "message": f"检测任务单 {entry_id} 不存在或已归档",
                    "entry": None,
                })
                continue

            ok, message = self._apply_action(entry, normalized_action)
            if ok:
                succeeded += 1
            items.append({
                "id": entry_id,
                "ok": ok,
                "message": message,
                "entry": self._as_view(entry) if ok else None,
            })

        failed = len(unique_ids) - succeeded
        if not unique_ids:
            summary = "未选择检测任务"
        elif failed == 0:
            summary = f"批量{normalized_action}完成：成功 {succeeded} 条"
        elif succeeded == 0:
            summary = f"批量{normalized_action}未生效：失败 {failed} 条"
        else:
            summary = f"批量{normalized_action}部分成功：成功 {succeeded} 条，失败 {failed} 条"

        result = {
            "ok": failed == 0 and bool(unique_ids),
            "action": normalized_action,
            "total": len(unique_ids),
            "requested": len(ids),
            "succeeded": succeeded,
            "failed": failed,
            "duplicates": len(ids) - len(unique_ids),
            "idempotent": False,
            "message": summary,
            "items": items,
        }
        if idempotency_key:
            _IDEMPOTENT_ACTIONS[idempotency_key] = (normalized_action, tuple(unique_ids), result)
        return result

    def _apply_action(self, entry: dict[str, Any], action: str) -> tuple[bool, str]:
        rule = ACTION_RULES.get(action)
        task_no = entry.get("任务编号") or entry.get("id")
        if rule is None:
            return False, f"任务「{task_no}」的动作「{action}」不属于检测任务可执行范围"

        target = str(rule["target"])
        current = str(entry.get("status") or "")
        if current == target:
            return True, f"任务「{task_no}」已是「{target}」，无需重复{action}"
        if current not in rule["allowed"]:
            return False, f"任务「{task_no}」当前为「{current}」，不能{action}"

        entry["status"] = target
        entry["任务状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return True, f"任务「{task_no}」已{action}"

    def _as_view(self, entry: dict[str, Any]) -> dict[str, Any]:
        view = dict(entry)
        view["任务状态"] = entry.get("status")
        return view
