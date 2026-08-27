from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any


LAT_LON_MAX_CODE = (1 << 22) - 1
TARGET_ID_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

MAPPING_FIELDS = [
    "source_format", "input_field", "unified_field", "mapping_rule",
    "unit_conversion", "null_strategy", "evidence", "verified",
]

CANDIDATE_FIELDS = [
    "source_format", "input_field", "candidate_unified_field",
    "candidate_rule", "confidence", "review_note",
]


def _mapping(
    source: str,
    input_field: str,
    unified_field: str,
    rule: str,
    conversion: str,
    null_strategy: str,
    evidence: str,
) -> dict[str, Any]:
    return {
        "source_format": source,
        "input_field": input_field,
        "unified_field": unified_field,
        "mapping_rule": rule,
        "unit_conversion": conversion,
        "null_strategy": null_strategy,
        "evidence": evidence,
        "verified": True,
    }


def verify_candidate_mapping(candidate_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """依据权威字段定义修正候选，并补全统一模型必需的正式映射。"""
    candidate_keys = {
        (str(row.get("source_format", "")), str(row.get("input_field", "")))
        for row in candidate_rows
    }
    required_candidate_keys = {
        ("OpenSky", "target_id"),
        ("OpenSky", "latest_time"),
        ("TeachingLink", "latitude_code+validity_flags.bit0"),
        ("TeachingLink", "longitude_code+validity_flags.bit1"),
        ("TeachingLink", "altitude_code+validity_flags.bit2"),
        ("TeachingLink", "callsign"),
        ("TeachingLink", "status_flags.bit2"),
        ("TeachingLink", "message_valid"),
    }
    if not required_candidate_keys.issubset(candidate_keys):
        missing = sorted(required_candidate_keys - candidate_keys)
        raise ValueError(f"候选映射缺少课程要求的核验项：{missing}")

    source_evidence = "source_field_definitions.md 与 unified_model.json"
    protocol_evidence = "teaching_message_spec.md、partner_field_dictionary.csv 与有效位样例"
    rows = [
        _mapping("OpenSky", "target_id", "track_id", "转为六位小写十六进制并保留前导0", "无", "必需；非法则拒绝映射", source_evidence),
        _mapping("OpenSky", "latest_time", "timestamp", "正整数Unix秒直接映射", "无", "缺失或非正整数则time_valid=false", source_evidence),
        _mapping("OpenSky", "callsign", "identity.callsign", "去除首尾空格", "无", "空字符串转null", source_evidence),
        _mapping("OpenSky", "lat", "position.lat", "合法纬度直接映射", "度到度", "缺失或越界转null", source_evidence),
        _mapping("OpenSky", "lon", "position.lon", "合法经度直接映射", "度到度", "缺失或越界转null", source_evidence),
        _mapping("OpenSky", "altitude", "position.alt", "高度物理量直接映射", "米到米", "缺失转null", source_evidence),
        _mapping("OpenSky", "alt_type", "position.alt_type", "仅接受barometric/geometric", "无", "高度缺失时unknown", source_evidence),
        _mapping("OpenSky", "speed", "motion.speed", "地速物理量直接映射", "m/s到m/s", "缺失转null", source_evidence),
        _mapping("OpenSky", "heading", "motion.heading", "检查0<=heading<360后映射", "度到度", "缺失转null；越界转null", source_evidence),
        _mapping("OpenSky", "vertical_rate", "motion.vertical_rate", "垂直速度直接映射", "m/s到m/s", "缺失转null", source_evidence),
        _mapping("OpenSky", "on_ground", "status.on_ground", "规范化为布尔值", "无", "缺失按false并使message_valid=false", source_evidence),
        _mapping("OpenSky", "lat+lon", "quality.position_valid", "纬经均非空且合法时为true", "无", "任一缺失为false", source_evidence),
        _mapping("OpenSky", "time_source", "quality.time_source", "保留position_time或last_contact_fallback", "无", "缺失回退position_time", source_evidence),
        _mapping("OpenSky", "message_valid", "quality.message_valid", "仅表示源记录结构校验结果", "无", "缺失为false", source_evidence),
        _mapping("TeachingLink", "target_id", "track_id", "转为六位小写十六进制并保留前导0", "无", "必需；非法则拒绝映射", protocol_evidence),
        _mapping("TeachingLink", "timestamp/latest_time", "timestamp", "正整数Unix秒直接映射", "无", "缺失或非正整数则time_valid=false", protocol_evidence),
        _mapping("TeachingLink", "callsign+validity_flags.bit6", "identity.callsign", "bit6有效时去除补0", "无", "bit6=0时为null", protocol_evidence),
        _mapping("TeachingLink", "latitude_code+validity_flags.bit0", "position.lat", "有效时code/(2^22-1)*180-90", "定点码到度", "bit0=0时为null", protocol_evidence),
        _mapping("TeachingLink", "longitude_code+validity_flags.bit1", "position.lon", "有效时code/(2^22-1)*360-180", "定点码到度", "bit1=0时为null", protocol_evidence),
        _mapping("TeachingLink", "altitude_code+validity_flags.bit2", "position.alt", "有效时code-1000", "定点码到米", "bit2=0时为null", protocol_evidence),
        _mapping("TeachingLink", "status_flags.bit1", "position.alt_type", "高度有效时0=barometric、1=geometric", "无", "高度无效时unknown", protocol_evidence),
        _mapping("TeachingLink", "speed_code+validity_flags.bit3", "motion.speed", "有效时code*0.1", "定点码到m/s", "bit3=0时为null", protocol_evidence),
        _mapping("TeachingLink", "heading_code+validity_flags.bit4", "motion.heading", "有效时code*0.01并检查小于360", "定点码到度", "bit4=0时为null", protocol_evidence),
        _mapping("TeachingLink", "vertical_rate_code+validity_flags.bit5", "motion.vertical_rate", "有效时code*0.01-327.68", "定点码到m/s", "bit5=0时为null", protocol_evidence),
        _mapping("TeachingLink", "status_flags.bit0", "status.on_ground", "按bit0恢复布尔值", "无", "无", protocol_evidence),
        _mapping("TeachingLink", "status_flags.bit2", "quality.time_source", "0=position_time、1=last_contact_fallback", "无", "回退不等于时间无效", protocol_evidence),
        _mapping("TeachingLink", "message_valid", "quality.message_valid", "保留完整帧接收判据", "无", "缺失为false", protocol_evidence),
    ]
    return rows


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


def _target_id(value: Any) -> str:
    text = str(value).strip().lower()
    if not TARGET_ID_RE.fullmatch(text):
        raise ValueError(f"target_id 必须是六位十六进制字符串：{value!r}")
    return text


def map_to_unified(record: dict[str, Any], source_format: str) -> dict[str, Any]:
    """按人工核验后的确定性规则生成统一态势消息。"""
    if not isinstance(record, dict):
        raise TypeError("record 必须是 dict。")
    normalized_source = source_format.strip().lower()
    if normalized_source not in {"opensky", "teachinglink"}:
        raise ValueError("source_format 必须是 OpenSky 或 TeachingLink。")

    track_id = _target_id(record.get("target_id"))
    timestamp = _integer(record.get("latest_time", record.get("timestamp")))
    time_valid = timestamp is not None and timestamp > 0

    if normalized_source == "opensky":
        lat = _number(record.get("lat"))
        lon = _number(record.get("lon"))
        lat = lat if lat is not None and -90 <= lat <= 90 else None
        lon = lon if lon is not None and -180 <= lon <= 180 else None
        altitude = _number(record.get("altitude"))
        speed = _number(record.get("speed"))
        heading = _number(record.get("heading"))
        heading = heading if heading is None or 0 <= heading < 360 else None
        vertical_rate = _number(record.get("vertical_rate"))
        callsign_text = str(record.get("callsign", "")).strip()
        callsign = callsign_text or None
        alt_type = str(record.get("alt_type", "unknown")).strip().lower()
        if altitude is None or alt_type not in {"barometric", "geometric"}:
            alt_type = "unknown"
        time_source = str(record.get("time_source") or record.get("timestamp_source") or "position_time")
        if time_source not in {"position_time", "last_contact_fallback"}:
            time_source = "position_time"
        on_ground = _boolean(record.get("on_ground"))
        message_valid = _boolean(record.get("message_valid"))
        source_name = "OpenSky"
    else:
        validity_flags = _integer(record.get("validity_flags")) or 0
        status_flags = _integer(record.get("status_flags")) or 0

        lat_code = _integer(record.get("latitude_code"))
        lon_code = _integer(record.get("longitude_code"))
        altitude_code = _integer(record.get("altitude_code"))
        speed_code = _integer(record.get("speed_code"))
        heading_code = _integer(record.get("heading_code"))
        vertical_rate_code = _integer(record.get("vertical_rate_code"))

        lat = lat_code / LAT_LON_MAX_CODE * 180.0 - 90.0 if validity_flags & 0x01 and lat_code is not None and 0 <= lat_code <= LAT_LON_MAX_CODE else None
        lon = lon_code / LAT_LON_MAX_CODE * 360.0 - 180.0 if validity_flags & 0x02 and lon_code is not None and 0 <= lon_code <= LAT_LON_MAX_CODE else None
        altitude = float(altitude_code - 1000) if validity_flags & 0x04 and altitude_code is not None else None
        speed = speed_code * 0.1 if validity_flags & 0x08 and speed_code is not None else None
        heading = heading_code * 0.01 if validity_flags & 0x10 and heading_code is not None else None
        if heading is not None and not 0 <= heading < 360:
            heading = None
        vertical_rate = vertical_rate_code * 0.01 - 327.68 if validity_flags & 0x20 and vertical_rate_code is not None else None

        callsign_text = str(record.get("callsign", "")).strip().rstrip("\x00")
        callsign = callsign_text or None if validity_flags & 0x40 else None
        alt_type = "geometric" if altitude is not None and status_flags & 0x02 else "barometric" if altitude is not None else "unknown"
        time_source = "last_contact_fallback" if status_flags & 0x04 else "position_time"
        on_ground = bool(status_flags & 0x01)
        message_valid = _boolean(record.get("message_valid"))
        source_name = "TeachingLink"

    return {
        "track_id": track_id,
        "source": source_name,
        "timestamp": timestamp or 0,
        "identity": {"callsign": callsign},
        "position": {"lat": lat, "lon": lon, "alt": altitude, "alt_type": alt_type},
        "motion": {"speed": speed, "heading": heading, "vertical_rate": vertical_rate},
        "status": {"on_ground": on_ground},
        "quality": {
            "position_valid": lat is not None and lon is not None,
            "time_valid": time_valid,
            "message_valid": message_valid,
            "time_source": time_source,
            "anomaly_flags": [],
        },
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_m4(output_dir: Path | None = None) -> dict[str, int]:
    student_root = Path(__file__).resolve().parents[1]
    output_dir = output_dir or student_root / "output"
    candidate_path = student_root / "reference" / "pre_generated_mapping_candidate.csv"
    opensky_path = output_dir / "current_situation.csv"
    teaching_path = student_root / "data" / "m4" / "partner_current_situation.csv"

    candidates = _read_csv(candidate_path)
    verified = verify_candidate_mapping(candidates)
    _write_csv(output_dir / "llm_mapping_candidate.csv", CANDIDATE_FIELDS, candidates)
    _write_csv(output_dir / "verified_mapping_table.csv", MAPPING_FIELDS, verified)

    unified = [map_to_unified(row, "OpenSky") for row in _read_csv(opensky_path)]
    unified.extend(map_to_unified(row, "TeachingLink") for row in _read_csv(teaching_path))
    ndjson_path = output_dir / "unified_situation.ndjson"
    ndjson_path.parent.mkdir(parents=True, exist_ok=True)
    with ndjson_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in unified:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    return {
        "candidate_rows": len(candidates),
        "verified_rows": len(verified),
        "unified_rows": len(unified),
        "opensky_rows": sum(row["source"] == "OpenSky" for row in unified),
        "teachinglink_rows": sum(row["source"] == "TeachingLink" for row in unified),
    }


def main() -> int:
    summary = run_m4()
    print(
        "M4 完成："
        f"核验 {summary['candidate_rows']} 条候选，形成 {summary['verified_rows']} 条正式规则，"
        f"输出 {summary['unified_rows']} 条统一态势消息。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
