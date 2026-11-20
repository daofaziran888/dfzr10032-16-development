"""采样责任段业务规则：按负责人交接时点把源端样本归属到班次，并留下可复核的时间凭据。

背景：水文站换班时，数据源档案里只有登记的负责人，缺少能分清交班时点读数归属
的记录。本模块把班次交接（SHIFT_ROSTER）与源端样本（SAMPLES）关联成采样责任段
数据集，为每个班算出有效观测分钟与可归责读数笔数，采集报告据此分列各班口径。

归属口径：一律按采样时间归属到当班。采样时间与接入时间落在交接点两侧时（例如
山区雨量计 07:58 采到读数、停电恢复后 08:04 才送到），仍按采样时间归属，并在
责任段记录上标明采用的时点（归属依据 + 采用时点），原接入批次一并保留供复核。
同一读数（站点 + 采样时间）在每个有效班次口径里只计入一次，重复批次留痕不计入。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.store import store

MODULE = "shift_segment"
REQUIRED_FIELDS = ["样本编号", "站点", "采样时间", "接入时间", "原接入批次"]
STATUS_ORDER = ["已计入", "待裁定", "已更正", "已补录", "不计入"]
ACTION_RULES = {"前班更正": "已更正", "交班账补录": "已补录"}
ACTION_COUNT_MODE = {"前班更正": "更正补计", "交班账补录": "跨班补计"}
NEGATIVE_ACTIONS: list[str] = []

TIME_FORMAT = "%Y-%m-%d %H:%M"
BASIS_SAMPLE_TIME = "采样时间"  # 归属一律以采样时间为准，迟到回执不改写观测归属

# 班次交接登记：负责人、责任时段与交班账状态。交班账已签字后，迟到的跨班读数
# 只能走“前班更正”或“交班账补录”两条路落账，两条路都只计一笔。
SHIFT_ROSTER: list[dict[str, str]] = [
    {"班次编号": "SHIFT-20261119-夜班", "负责人": "陈岚", "接班时点": "2026-11-19 20:00", "交班时点": "2026-11-20 08:00", "交班账状态": "已签字"},
    {"班次编号": "SHIFT-20261120-早班", "负责人": "李洵", "接班时点": "2026-11-20 08:00", "交班时点": "2026-11-20 20:00", "交班账状态": "在岗"},
]

# 源端样本：采样时间是源端观测时刻，接入时间是平台收到时刻，接入批次留原始回执。
# RG-20261120-0758 是山区雨量计停电前采到、恢复后迟到的读数；0758R 是同一读数
# 的重传批次，用来校验“同一读数只计一次、原接入批次留痕”。
SAMPLES: list[dict[str, str]] = [
    {"样本编号": "RG-20261120-0738", "站点": "山区雨量计", "采样时间": "2026-11-20 07:38", "接入时间": "2026-11-20 07:39", "原接入批次": "BATCH-0739", "读数": "3.6mm"},
    {"样本编号": "RG-20261120-0748", "站点": "山区雨量计", "采样时间": "2026-11-20 07:48", "接入时间": "2026-11-20 07:49", "原接入批次": "BATCH-0749", "读数": "4.1mm"},
    {"样本编号": "RG-20261120-0758", "站点": "山区雨量计", "采样时间": "2026-11-20 07:58", "接入时间": "2026-11-20 08:04", "原接入批次": "BATCH-0804", "读数": "5.2mm"},
    {"样本编号": "RG-20261120-0758R", "站点": "山区雨量计", "采样时间": "2026-11-20 07:58", "接入时间": "2026-11-20 08:10", "原接入批次": "BATCH-0810", "读数": "5.2mm"},
    {"样本编号": "RG-20261120-0808", "站点": "山区雨量计", "采样时间": "2026-11-20 08:08", "接入时间": "2026-11-20 08:09", "原接入批次": "BATCH-0809", "读数": "2.7mm"},
    {"样本编号": "RG-20261120-0818", "站点": "山区雨量计", "采样时间": "2026-11-20 08:18", "接入时间": "2026-11-20 08:19", "原接入批次": "BATCH-0819", "读数": "1.9mm"},
    {"样本编号": "WL-20261120-0630", "站点": "河道水位站", "采样时间": "2026-11-20 06:30", "接入时间": "2026-11-20 06:31", "原接入批次": "BATCH-0631", "读数": "12.6m"},
    {"样本编号": "WL-20261120-0730", "站点": "河道水位站", "采样时间": "2026-11-20 07:30", "接入时间": "2026-11-20 07:31", "原接入批次": "BATCH-0731", "读数": "12.7m"},
    {"样本编号": "WL-20261120-0830", "站点": "河道水位站", "采样时间": "2026-11-20 08:30", "接入时间": "2026-11-20 08:31", "原接入批次": "BATCH-0831", "读数": "12.8m"},
    {"样本编号": "WL-20261120-0930", "站点": "河道水位站", "采样时间": "2026-11-20 09:30", "接入时间": "2026-11-20 09:31", "原接入批次": "BATCH-0931", "读数": "12.9m"},
]


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, TIME_FORMAT)


def _shift_of(ts: str) -> dict[str, str] | None:
    """某个时刻落在哪个班的责任时段里（接班时点含、交班时点不含）。"""
    moment = _parse(ts)
    for shift in SHIFT_ROSTER:
        if _parse(shift["接班时点"]) <= moment < _parse(shift["交班时点"]):
            return shift
    return None


def _dedup_key(sample: dict[str, Any]) -> str:
    """同一读数的判定键：同一站点同一采样时刻，无论分几个批次送达。"""
    return f"{sample['站点']}|{sample['采样时间']}"


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _attribute(sample: dict[str, Any], entry_id: int, seen: set[str]) -> dict[str, Any]:
    """把一条源端样本落成责任段记录：定班次、标跨交接点、判计入方式。"""
    key = _dedup_key(sample)
    own_shift = _shift_of(sample["采样时间"])
    arrived_shift = _shift_of(sample["接入时间"])
    straddled = (own_shift or {}).get("班次编号") != (arrived_shift or {}).get("班次编号")
    row: dict[str, Any] = {
        "id": entry_id,
        "段编号": f"SEG-{entry_id:04d}",
        "样本编号": sample["样本编号"],
        "站点": sample["站点"],
        "采样时间": sample["采样时间"],
        "接入时间": sample["接入时间"],
        "原接入批次": sample["原接入批次"],
        "去重键": key,
        "跨交接点": "是" if straddled else "否",
        "归属依据": BASIS_SAMPLE_TIME,
        "采用时点": sample["采样时间"],
        "班次编号": own_shift["班次编号"] if own_shift else "无当班班次",
        "负责人": own_shift["负责人"] if own_shift else "—",
    }
    if own_shift is None:
        row.update(计入方式="无当班班次", counted=False, status="不计入", pending=False, abnormal=True)
    elif key in seen:
        # 同一读数只计一次：先到的批次占住计入位，重传批次留痕供复核
        row.update(计入方式="重复不计入", counted=False, status="不计入", pending=False, abnormal=False)
    elif straddled and own_shift["交班账状态"] == "已签字":
        # 迟到回执撞上已签字的交班账：先挂起，等前班更正或交班账补录裁定
        row.update(计入方式="待裁定", counted=False, status="待裁定", pending=True, abnormal=True)
    else:
        row.update(计入方式="正常计入", counted=True, status="已计入", pending=False, abnormal=False)
    seen.add(key)
    return row


def build_segments() -> None:
    """按接入顺序把源端样本落成采样责任段数据集；同一读数先到先占计入位。"""
    rows = store.rows(MODULE)
    if rows:
        return
    seen: set[str] = set()
    for sample in sorted(SAMPLES, key=lambda item: (item["接入时间"], item["样本编号"])):
        rows.append(_attribute(sample, _next_id(rows), seen))


build_segments()


class ShiftSegmentService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("样本编号", "")) or keyword in str(row.get("段编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记一条新到的源端读数，归属、跨交接点与去重都由口径自动判定。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        try:
            _parse(str(values["采样时间"]))
            _parse(str(values["接入时间"]))
        except ValueError:
            return None, ["采样时间与接入时间需按 YYYY-MM-DD HH:MM 填写"]
        rows = store.rows(MODULE)
        seen = {str(row.get("去重键", "")) for row in rows}
        sample = {field: str(values.get(field)).strip() for field in REQUIRED_FIELDS}
        entry = _attribute(sample, _next_id(rows), seen)
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """裁定迟到回执：前班更正或交班账补录，两班合计仍只有一笔。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采样责任段 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采样责任段可执行范围"
        if entry.get("status") != "待裁定":
            return None, f"责任段 {entry.get('段编号')} 当前为「{entry.get('status')}」，只有待裁定的迟到回执才能{action}"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["计入方式"] = ACTION_COUNT_MODE[action]
        entry["counted"] = True
        entry["pending"] = False
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        evidence = f"{entry['归属依据']} {entry['采用时点']}"
        if action == "前班更正":
            detail = f"已向 {entry['班次编号']}（{entry['负责人']}）出具更正单"
        else:
            detail = f"已在 {entry['班次编号']} 的交班账另立补录行"
        return entry, (
            f"{detail}，{entry['样本编号']} 按{evidence}计入，两班合计仍为一笔；"
            f"原接入批次 {entry['原接入批次']} 留痕，下次报账可凭采用时点复核"
        )

    def shift_report(self) -> dict[str, Any]:
        """分班小结：各班负责的站点、有效观测分钟、可归责读数笔数与来源小计。"""
        rows = store.rows(MODULE)
        shifts: list[dict[str, Any]] = []
        for shift in SHIFT_ROSTER:
            owned = [row for row in rows if row.get("班次编号") == shift["班次编号"]]
            counted = sorted(
                (row for row in owned if row.get("counted")),
                key=lambda row: str(row.get("采用时点", "")),
            )
            minutes = {(str(row["站点"]), str(row["采用时点"])[:16]) for row in counted}
            subtotal = {"正常计入": 0, "跨班补计": 0, "更正补计": 0, "未计入": 0}
            for row in owned:
                mode = str(row.get("计入方式", ""))
                if mode in ("正常计入", "跨班补计", "更正补计"):
                    subtotal[mode] += 1
                else:
                    subtotal["未计入"] += 1
            shifts.append({
                "班次编号": shift["班次编号"],
                "负责人": shift["负责人"],
                "责任时段": f"{shift['接班时点']} ~ {shift['交班时点']}",
                "交班账状态": shift["交班账状态"],
                "站点": sorted({str(row["站点"]) for row in counted}),
                "有效观测分钟": len(minutes),
                "可归责读数": len({str(row["去重键"]) for row in counted}),
                "样本编号": [str(row["样本编号"]) for row in counted],
                "来源小计": subtotal,
                "时间凭据": [
                    {
                        "样本编号": row["样本编号"],
                        "归属依据": row["归属依据"],
                        "采用时点": row["采用时点"],
                        "原接入批次": row["原接入批次"],
                        "计入方式": row["计入方式"],
                    }
                    for row in counted
                ],
            })
        total_counted = sum(int(item["可归责读数"]) for item in shifts)
        global_keys = {str(row["去重键"]) for row in rows if row.get("counted")}
        return {
            "module": MODULE,
            "口径": "按采样时间归属当班；采样与接入跨交接点时以采样时间为准，采用时点与原接入批次留痕备查",
            "班次": shifts,
            "合计": {
                "可归责读数": total_counted,
                "全局去重后读数": len(global_keys),
                "校验": "一致" if total_counted == len(global_keys) else "不一致",
            },
        }

    def generate_collection_report(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """把分班小结写进采集报告模块；样本编号与时间凭据留在责任段数据集备查。"""
        report = self.shift_report()
        rows = store.rows("report")
        entry: dict[str, Any] = {"id": _next_id(rows)}
        uncounted = sum(int(item["来源小计"]["未计入"]) for item in report["班次"])
        entry.update({
            "报告编号": f"REPO-{int(entry['id']):04d}",
            "采集源": "采样责任段数据集",
            "统计周期": f"{SHIFT_ROSTER[0]['接班时点']} ~ {SHIFT_ROSTER[-1]['交班时点']}",
            "总采集量": str(len(store.rows(MODULE))),
            "成功量": str(report["合计"]["可归责读数"]),
            "失败量": str(uncounted),
            "平均耗秒": "—",
            "生成时间": date.today().isoformat(),
            "status": "已生成",
            "pending": True,
            "abnormal": False,
        })
        rows.append(entry)
        return entry, report
