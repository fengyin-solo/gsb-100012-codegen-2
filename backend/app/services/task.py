"""检测任务业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "task"
REQUIRED_FIELDS = ["任务编号", "所属样品", "检测项目"]
STATUS_ORDER = ["待分配", "已分配", "检测中", "已完成", "已复核"]
ACTION_RULES = {"分配任务": "已分配", "开始检测": "检测中", "提交复核": "已复核"}
# 每个动作允许的来源状态：不满足前置状态的记录逐条拦下，不影响同批其它记录
ACTION_PRECONDITIONS = {
    "分配任务": {"待分配"},
    "开始检测": {"已分配"},
    "提交复核": {"检测中", "已完成"},
}
NEGATIVE_ACTIONS = []
FINAL_STATUS = STATUS_ORDER[-1]


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
        start = max(page - 1, 0) * size
        return [self._with_display_status(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._with_display_status(entry) if entry else None

    def summary(self) -> dict[str, int]:
        """状态统计：给列表页的指标卡用，口径与列表筛选保持一致。"""
        rows = store.rows(MODULE)
        today = date.today().isoformat()
        overdue = sum(
            1
            for row in rows
            if row.get("status") != FINAL_STATUS
            and str(row.get("截止日期") or "") < today
            and str(row.get("截止日期") or "").strip()
        )
        return {
            "总数": len(rows),
            "待分配": sum(1 for row in rows if row.get("status") == "待分配"),
            "检测中": sum(1 for row in rows if row.get("status") == "检测中"),
            "逾期": overdue,
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._with_display_status(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测任务单 {entry_id} 不存在或已归档"
        message = self._check_action(entry, action)
        if message is not None:
            return None, message
        self._apply_action(entry, action)
        return self._with_display_status(entry), f"检测任务单已{action}"

    def run_batch_action(self, entry_ids: list[int], action: str) -> dict[str, Any]:
        """批量执行动作：逐条校验、逐条落结果，单条失败不影响其它记录。

        重复提交同一条记录同一动作时按幂等处理：已是目标状态的记为跳过，
        不重复变更、也不计失败，刷新或重试后清单与统计保持一致。
        """
        results: list[dict[str, Any]] = []
        if action not in ACTION_RULES:
            # 动作本身不合法：整批不执行，但每条仍给出明确结果
            for entry_id in dict.fromkeys(entry_ids):
                results.append({
                    "id": entry_id,
                    "label": f"#{entry_id}",
                    "ok": False,
                    "skipped": False,
                    "message": f"动作「{action}」不属于检测任务可执行范围",
                })
            return self._batch_receipt(action, results)

        for entry_id in dict.fromkeys(entry_ids):  # 去重且保持提交顺序
            entry = store.find(MODULE, entry_id)
            if entry is None:
                results.append({
                    "id": entry_id,
                    "label": f"#{entry_id}",
                    "ok": False,
                    "skipped": False,
                    "message": f"检测任务单 {entry_id} 不存在或已归档",
                })
                continue
            label = str(entry.get("任务编号") or f"#{entry_id}")
            current = str(entry.get("status") or "")
            target = ACTION_RULES[action]
            if current == target:
                results.append({
                    "id": entry_id,
                    "label": label,
                    "ok": True,
                    "skipped": True,
                    "message": f"已处于「{target}」，无需重复{action}",
                })
                continue
            message = self._check_action(entry, action)
            if message is not None:
                results.append({
                    "id": entry_id,
                    "label": label,
                    "ok": False,
                    "skipped": False,
                    "message": message,
                })
                continue
            self._apply_action(entry, action)
            results.append({
                "id": entry_id,
                "label": label,
                "ok": True,
                "skipped": False,
                "message": f"已{action}，状态流转为「{target}」",
            })
        return self._batch_receipt(action, results)

    def _check_action(self, entry: dict[str, Any], action: str) -> str | None:
        """返回 None 表示可以执行，否则返回可读的拦截原因。"""
        if action not in ACTION_RULES:
            return f"动作「{action}」不属于检测任务可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return f"目标状态「{target}」不在允许的状态序列里"
        current = str(entry.get("status") or "")
        if current == target:
            return f"检测任务单已处于「{target}」，无需重复{action}"
        allowed_from = ACTION_PRECONDITIONS.get(action, set())
        if current not in allowed_from:
            return f"当前状态「{current}」不允许{action}，请先完成前置环节"
        return None

    def _apply_action(self, entry: dict[str, Any], action: str) -> None:
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != FINAL_STATUS
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        entry["任务状态"] = target

    def _with_display_status(self, entry: dict[str, Any]) -> dict[str, Any]:
        """列表与详情里的「任务状态」列始终跟随内部状态，刷新后口径统一。"""
        entry["任务状态"] = str(entry.get("status") or "")
        return entry

    def _batch_receipt(self, action: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        succeeded = sum(1 for item in results if item["ok"] and not item["skipped"])
        skipped = sum(1 for item in results if item["skipped"])
        failed = sum(1 for item in results if not item["ok"])
        total = len(results)
        if failed:
            message = f"批量{action}完成：成功 {succeeded} 条，跳过 {skipped} 条，失败 {failed} 条，失败原因见逐条结果"
        elif skipped:
            message = f"批量{action}完成：成功 {succeeded} 条，{skipped} 条已是目标状态无需重复操作"
        else:
            message = f"批量{action}完成：{succeeded} 条全部成功"
        return {
            "ok": failed == 0,
            "action": action,
            "message": message,
            "total": total,
            "succeeded": succeeded,
            "skipped": skipped,
            "failed": failed,
            "results": results,
        }
