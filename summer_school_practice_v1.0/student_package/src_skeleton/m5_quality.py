from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any


BATCH_TIME = 1710000120

ALERT_FIELDS = [
    "alert_time", "target_id", "alert_type", "severity", "field", "description",
]

QUALITY_FIELDS = [
    "target_id", "timestamp", "position_valid", "delayed", "duplicate_detected",
    "heading_valid", "message_valid", "anomaly_level", "display_status",
]


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value: Any) -> float | None:
    if _empty(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _boolean(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    return default


def _alert(
    record: dict[str, Any],
    alert_type: str,
    severity: str,
    field: str,
    description: str,
    batch_time: int = BATCH_TIME,
) -> dict[str, Any]:
    timestamp = _integer(record.get("latest_time", record.get("timestamp")))
    return {
        "alert_time": batch_time,
        "target_id": str(record.get("target_id", "")),
        "alert_type": alert_type,
        "severity": severity,
        "field": field,
        "description": description,
        "_record_timestamp": timestamp,
    }


def check_record(record: dict[str, Any], batch_time: int = BATCH_TIME) -> list[dict[str, Any]]:
    """检查位置缺失、时间延迟、航向越界和可选帧验证失败。"""
    lat = _number(record.get("lat"))
    lon = _number(record.get("lon"))
    timestamp = _integer(record.get("latest_time", record.get("timestamp")))
    heading = _number(record.get("heading"))
    alerts: list[dict[str, Any]] = []

    if lat is None or lon is None:
        missing = "lat和lon" if lat is None and lon is None else "lat" if lat is None else "lon"
        alerts.append(_alert(
            record,
            "POSITION_MISSING",
            "HIGH",
            missing,
            f"位置字段缺失：{missing}为空。",
            batch_time,
        ))

    if timestamp is not None and batch_time - timestamp > 60:
        alerts.append(_alert(
            record,
            "DATA_DELAYED",
            "MEDIUM",
            "timestamp",
            f"记录时间{timestamp}比批次时间{batch_time}滞后{batch_time - timestamp}秒。",
            batch_time,
        ))

    if heading is not None and not 0 <= heading < 360:
        alerts.append(_alert(
            record,
            "HEADING_OUT_OF_RANGE",
            "MEDIUM",
            "heading",
            f"heading={heading:g}，要求0<=heading<360。",
            batch_time,
        ))

    if not _boolean(record.get("message_valid"), default=False):
        alerts.append(_alert(
            record,
            "FRAME_VALIDATION_ERROR",
            "HIGH",
            "message_valid",
            "上游记录未通过帧或结构校验。",
            batch_time,
        ))

    return alerts


def check_duplicates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """使用 target_id+timestamp 联合键检查重复；每个重复键输出一条告警。"""
    keyed: list[tuple[tuple[str, int], dict[str, Any]]] = []
    for record in records:
        target_id = str(record.get("target_id", ""))
        timestamp = _integer(record.get("latest_time", record.get("timestamp")))
        if target_id and timestamp is not None:
            keyed.append(((target_id, timestamp), record))
    counts = Counter(key for key, _ in keyed)

    alerts = []
    emitted: set[tuple[str, int]] = set()
    for key, record in keyed:
        if counts[key] > 1 and key not in emitted:
            emitted.add(key)
            alerts.append(_alert(
                record,
                "DUPLICATE_RECORD",
                "MEDIUM",
                "target_id+timestamp",
                f"联合键target_id={key[0]}, timestamp={key[1]}出现{counts[key]}次。",
            ))
    return alerts


def build_quality_situation(
    records: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 HIGH > MEDIUM > NONE 合成逐记录质量态势。"""
    by_key: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
    for alert in alerts:
        key = (str(alert.get("target_id", "")), _integer(alert.get("_record_timestamp")))
        by_key.setdefault(key, []).append(alert)

    rows: list[dict[str, Any]] = []
    for record in records:
        target_id = str(record.get("target_id", ""))
        timestamp = _integer(record.get("latest_time", record.get("timestamp")))
        lat = _number(record.get("lat"))
        lon = _number(record.get("lon"))
        heading = _number(record.get("heading"))
        record_alerts = by_key.get((target_id, timestamp), [])
        types = {alert["alert_type"] for alert in record_alerts}
        severities = {alert["severity"] for alert in record_alerts}
        anomaly_level = "HIGH" if "HIGH" in severities else "MEDIUM" if severities else "NONE"
        display_status = "ERROR" if anomaly_level == "HIGH" else "WARNING" if anomaly_level == "MEDIUM" else "NORMAL"
        rows.append({
            "target_id": target_id,
            "timestamp": timestamp,
            "position_valid": lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180,
            "delayed": "DATA_DELAYED" in types,
            "duplicate_detected": "DUPLICATE_RECORD" in types,
            "heading_valid": heading is None or 0 <= heading < 360,
            "message_valid": _boolean(record.get("message_valid"), default=False),
            "anomaly_level": anomaly_level,
            "display_status": display_status,
        })
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_m5(
    input_path: Path | None = None,
    output_dir: Path | None = None,
    *,
    batch_time: int = BATCH_TIME,
) -> dict[str, Any]:
    student_root = Path(__file__).resolve().parents[1]
    input_path = input_path or student_root / "data" / "m5" / "anomaly_cases.csv"
    output_dir = output_dir or student_root / "output"
    records = _read_csv(input_path)

    alerts = [alert for record in records for alert in check_record(record, batch_time)]
    alerts.extend(check_duplicates(records))
    quality = build_quality_situation(records, alerts)
    _write_csv(output_dir / "alert_log.csv", ALERT_FIELDS, alerts)
    _write_csv(output_dir / "quality_situation.csv", QUALITY_FIELDS, quality)

    counts = Counter(alert["alert_type"] for alert in alerts)
    severity_counts = Counter(alert["severity"] for alert in alerts)
    return {
        "input_records": len(records),
        "alerts": len(alerts),
        "quality_rows": len(quality),
        "alert_type_counts": dict(sorted(counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
    }


def main() -> int:
    summary = run_m5()
    print(
        "M5 完成："
        f"检查 {summary['input_records']} 条记录，产生 {summary['alerts']} 条告警，"
        f"质量态势 {summary['quality_rows']} 行。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
