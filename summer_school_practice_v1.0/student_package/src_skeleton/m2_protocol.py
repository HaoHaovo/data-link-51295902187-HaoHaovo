from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any


# =========================
# TeachingLink 固定协议常量
# =========================

FRAME_SIZE = 41
MAGIC = 0x4453
VERSION = 1
MESSAGE_TYPE = 1

LAT_LON_MAX_CODE = (1 << 22) - 1
UINT16_MAX = 0xFFFF
UINT32_MAX = 0xFFFFFFFF

ALTITUDE_MIN = -1000.0
ALTITUDE_MAX = 64535.0
SPEED_MIN = 0.0
SPEED_MAX = 6553.5
HEADING_MIN = 0.0
HEADING_MAX = 360.0
VERTICAL_RATE_MIN = -327.68
VERTICAL_RATE_MAX = 327.67

TARGET_ID_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

VALIDATION_LOG_FIELDS = [
    "record_no", "target_id", "stage", "field",
    "problem_type", "value", "description",
]

DECODED_FIELDS = [
    "target_id", "callsign", "timestamp", "timestamp_source", "time_source",
    "message_seq", "lat", "lon", "altitude", "alt_type", "speed", "heading",
    "vertical_rate", "on_ground", "status_flags", "validity_flags",
    "latitude_code", "longitude_code", "altitude_code", "speed_code",
    "heading_code", "vertical_rate_code", "lat_valid", "lon_valid",
    "altitude_valid", "speed_valid", "heading_valid", "vertical_rate_valid",
    "callsign_valid", "checksum", "expected_checksum", "message_valid",
    "validation_errors", "source",
]

ROUNDTRIP_FIELDS = [
    "field", "source_value", "source_valid", "protocol_code", "flag_bit",
    "decoded_value", "decoded_valid", "absolute_error/tolerance", "passed",
]


# =========================
# 通用辅助函数
# =========================

def _issue(field: str, problem_type: str, value: Any, description: str) -> dict[str, Any]:
    return {
        "field": field,
        "problem_type": problem_type,
        "value": value,
        "description": description,
    }


def _is_number(value: Any) -> bool:
    # bool 是 int 的子类，不能把 True/False 当成数值字段。
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _q(value: float) -> int:
    """课程统一量化函数 Q(y) = floor(y + 0.5)。"""
    return math.floor(value + 0.5)


def _check_optional_number(
    value: Any,
    field: str,
    minimum: float,
    maximum: float,
    issues: list[dict[str, Any]],
    *,
    maximum_inclusive: bool = True,
) -> float | None:
    """解析阶段使用：缺失/类型错误/越界时记录问题并返回 None。"""
    if value is None:
        issues.append(_issue(field, "MISSING", value, f"{field} 为空。"))
        return None

    if not _is_number(value):
        issues.append(_issue(field, "TYPE_ERROR", value, f"{field} 必须是数值或空值。"))
        return None

    number = float(value)
    if not math.isfinite(number):
        issues.append(_issue(field, "OUT_OF_RANGE", value, f"{field} 必须是有限数值。"))
        return None

    too_low = number < minimum
    too_high = number > maximum if maximum_inclusive else number >= maximum
    if too_low or too_high:
        bracket = "]" if maximum_inclusive else ")"
        issues.append(
            _issue(
                field,
                "OUT_OF_RANGE",
                value,
                f"{field} 超出允许范围 [{minimum}, {maximum}{bracket}。",
            )
        )
        return None

    return number


def _require_encoding_number(
    record: dict[str, Any],
    field: str,
    minimum: float,
    maximum: float,
    *,
    maximum_inclusive: bool = True,
) -> float | None:
    """编码阶段再次防御性检查，禁止截断、掩码或取模处理物理量越界。"""
    value = record.get(field)
    if value is None:
        return None
    if not _is_number(value):
        raise TypeError(f"{field} 必须是数值或 None。")

    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} 必须是有限数值。")

    too_low = number < minimum
    too_high = number > maximum if maximum_inclusive else number >= maximum
    if too_low or too_high:
        bracket = "]" if maximum_inclusive else ")"
        raise ValueError(f"{field} 超出允许范围 [{minimum}, {maximum}{bracket}。")
    return number


# =========================
# M2 核心函数 1：OpenSky Parser
# =========================

def parse_state_vector(vector: list[Any]) -> dict[str, Any]:
    """将 OpenSky 状态向量转换为发送方内部结构化记录。

    必需字段不可用时 record_valid=False。
    可选字段缺失或不合法时置为 None，并记录问题。
    """
    issues: list[dict[str, Any]] = []
    record: dict[str, Any] = {
        "target_id": None,
        "callsign": None,
        "timestamp": None,
        "timestamp_source": None,
        "lat": None,
        "lon": None,
        "altitude": None,
        "alt_type": "unknown",
        "speed": None,
        "heading": None,
        "vertical_rate": None,
        "on_ground": None,
        "origin_country": None,
        "position_source": None,
        "source": "OpenSky",
        "record_valid": False,
        "validation_errors": [],
        "_validation_issues": issues,
    }

    if not isinstance(vector, list):
        issues.append(_issue("vector", "TYPE_ERROR", type(vector).__name__, "状态向量必须是 list。"))
        record["validation_errors"] = ["TYPE_ERROR"]
        return record

    def at(index: int) -> Any:
        return vector[index] if index < len(vector) else None

    # 0: icao24 -> target_id，必需。
    raw_target_id = at(0)
    if raw_target_id is None:
        issues.append(_issue(
            "target_id", "REQUIRED_FIELD_MISSING", None,
            "icao24 缺失，无法形成必需的 target_id。",
        ))
    elif not isinstance(raw_target_id, str):
        issues.append(_issue(
            "target_id", "TYPE_ERROR", raw_target_id,
            "icao24 必须是恰好 6 位的十六进制字符串。",
        ))
    elif not TARGET_ID_RE.fullmatch(raw_target_id):
        issues.append(_issue(
            "target_id", "ENCODING_ERROR", raw_target_id,
            "icao24 必须是恰好 6 位的十六进制字符串。",
        ))
    else:
        record["target_id"] = raw_target_id.lower()

    # 1: callsign，可空；去首尾空格；有效时 1-8 ASCII 字节。
    raw_callsign = at(1)
    if raw_callsign is None:
        issues.append(_issue("callsign", "MISSING", None, "callsign 为空。"))
    elif not isinstance(raw_callsign, str):
        issues.append(_issue("callsign", "TYPE_ERROR", raw_callsign, "callsign 必须是字符串或空值。"))
    else:
        callsign = raw_callsign.strip()
        if not callsign:
            issues.append(_issue("callsign", "MISSING", raw_callsign, "callsign 去除首尾空格后为空。"))
        else:
            try:
                callsign_bytes = callsign.encode("ascii")
            except UnicodeEncodeError:
                issues.append(_issue(
                    "callsign", "ENCODING_ERROR", raw_callsign,
                    "callsign 有效时必须是 ASCII。",
                ))
            else:
                if not 1 <= len(callsign_bytes) <= 8:
                    issues.append(_issue(
                        "callsign", "ENCODING_ERROR", raw_callsign,
                        "callsign 去除首尾空格后必须为 1-8 个 ASCII 字节。",
                    ))
                else:
                    record["callsign"] = callsign

    # 2: origin_country，仅保留来源元数据。
    raw_country = at(2)
    if raw_country is not None:
        if isinstance(raw_country, str):
            record["origin_country"] = raw_country
        else:
            issues.append(_issue(
                "origin_country", "TYPE_ERROR", raw_country,
                "origin_country 必须是字符串或空值。",
            ))

    # 3/4: timestamp，优先 time_position，空时回退 last_contact。
    time_position = at(3)
    last_contact = at(4)
    if time_position is not None:
        selected_time = time_position
        selected_field = "time_position"
        selected_source = "position_time"
    elif last_contact is not None:
        issues.append(_issue(
            "time_position", "MISSING", None,
            "time_position 为空，使用 last_contact 回退。",
        ))
        selected_time = last_contact
        selected_field = "last_contact"
        selected_source = "last_contact_fallback"
    else:
        selected_time = None
        selected_field = "timestamp"
        selected_source = None

    if selected_time is None:
        issues.append(_issue(
            "timestamp", "REQUIRED_FIELD_MISSING", None,
            "time_position 与 last_contact 均为空，不能生成正常帧。",
        ))
    elif not isinstance(selected_time, int) or isinstance(selected_time, bool):
        issues.append(_issue(
            selected_field, "TYPE_ERROR", selected_time,
            f"{selected_field} 必须是 Unix 秒整数。",
        ))
    elif not 0 <= selected_time <= UINT32_MAX:
        issues.append(_issue(
            selected_field, "OUT_OF_RANGE", selected_time,
            f"{selected_field} 必须能表示为 uint32。",
        ))
    else:
        record["timestamp"] = selected_time
        record["timestamp_source"] = selected_source

    # 5/6: lon/lat。
    record["lon"] = _check_optional_number(at(5), "lon", -180.0, 180.0, issues)
    record["lat"] = _check_optional_number(at(6), "lat", -90.0, 90.0, issues)

    # 7/13: altitude，baro 优先；仅 baro 为空时回退 geo。
    baro_altitude = at(7)
    geo_altitude = at(13)
    if baro_altitude is not None:
        altitude = _check_optional_number(
            baro_altitude, "baro_altitude", ALTITUDE_MIN, ALTITUDE_MAX, issues
        )
        if altitude is not None:
            record["altitude"] = altitude
            record["alt_type"] = "barometric"
    elif geo_altitude is not None:
        issues.append(_issue(
            "baro_altitude", "MISSING", None,
            "baro_altitude 为空，使用 geo_altitude 回退。",
        ))
        altitude = _check_optional_number(
            geo_altitude, "geo_altitude", ALTITUDE_MIN, ALTITUDE_MAX, issues
        )
        if altitude is not None:
            record["altitude"] = altitude
            record["alt_type"] = "geometric"
    else:
        issues.append(_issue(
            "altitude", "MISSING", None,
            "baro_altitude 与 geo_altitude 均为空。",
        ))

    # 8: on_ground，必需且必须是 bool。
    raw_on_ground = at(8)
    if raw_on_ground is None:
        issues.append(_issue(
            "on_ground", "REQUIRED_FIELD_MISSING", None,
            "on_ground 缺失。",
        ))
    elif not isinstance(raw_on_ground, bool):
        issues.append(_issue(
            "on_ground", "TYPE_ERROR", raw_on_ground,
            "on_ground 必须是布尔值。",
        ))
    else:
        record["on_ground"] = raw_on_ground

    # 9/10/11: speed/heading/vertical_rate。
    record["speed"] = _check_optional_number(
        at(9), "speed", SPEED_MIN, SPEED_MAX, issues
    )
    record["heading"] = _check_optional_number(
        at(10), "heading", HEADING_MIN, HEADING_MAX, issues,
        maximum_inclusive=False,
    )
    record["vertical_rate"] = _check_optional_number(
        at(11), "vertical_rate", VERTICAL_RATE_MIN, VERTICAL_RATE_MAX, issues
    )

    # 16: position_source，仅保留来源元数据。
    raw_position_source = at(16)
    if raw_position_source is not None:
        if isinstance(raw_position_source, int) and not isinstance(raw_position_source, bool):
            record["position_source"] = raw_position_source
        else:
            issues.append(_issue(
                "position_source", "TYPE_ERROR", raw_position_source,
                "position_source 必须是整数或空值。",
            ))

    # 只有必需字段决定一条源记录能否生成正常帧。
    record["record_valid"] = (
        record["target_id"] is not None
        and record["timestamp"] is not None
        and record["on_ground"] is not None
    )
    record["validation_errors"] = [item["problem_type"] for item in issues]
    return record


# =========================
# M2 核心函数 2：Checksum
# =========================

def calculate_checksum(data_without_checksum: bytes) -> int:
    """计算无符号字节值之和模 65536；编码/解码时传入前 39 字节。"""
    if not isinstance(data_without_checksum, (bytes, bytearray)):
        raise TypeError("data_without_checksum 必须是 bytes 或 bytearray。")
    return sum(data_without_checksum) % 65536


# =========================
# M2 核心函数 3：Encoder
# =========================

def encode_position_message(record: dict[str, Any], message_seq: int) -> bytes:
    """按固定偏移、大端字节序封装一条 41 字节 TeachingLink 位置状态消息。"""
    if not isinstance(record, dict):
        raise TypeError("record 必须是 dict。")
    if record.get("record_valid") is False:
        raise ValueError("发送方记录未通过必需字段检查，不能生成正常帧。")

    target_id = record.get("target_id")
    if not isinstance(target_id, str) or not TARGET_ID_RE.fullmatch(target_id):
        raise ValueError("target_id 必须是恰好 6 位的十六进制字符串。")

    timestamp = record.get("timestamp")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        raise TypeError("timestamp 必须是整数。")
    if not 0 <= timestamp <= UINT32_MAX:
        raise ValueError("timestamp 必须能表示为 uint32。")

    on_ground = record.get("on_ground")
    if not isinstance(on_ground, bool):
        raise TypeError("on_ground 必须是布尔值。")

    timestamp_source = record.get("timestamp_source")
    if timestamp_source not in {"position_time", "last_contact_fallback"}:
        raise ValueError("timestamp_source 必须是 position_time 或 last_contact_fallback。")

    if not isinstance(message_seq, int) or isinstance(message_seq, bool):
        raise TypeError("message_seq 必须是整数。")
    if message_seq < 0:
        raise ValueError("message_seq 不能为负数。")
    seq = message_seq % 65536  # 协议明确允许发送序号按模 65536 回绕。

    callsign = record.get("callsign")
    callsign_bytes = b""
    if callsign is not None:
        if not isinstance(callsign, str):
            raise TypeError("callsign 必须是字符串或 None。")
        if callsign != callsign.strip():
            raise ValueError("callsign 应在解析阶段去除首尾空格。")
        try:
            callsign_bytes = callsign.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("callsign 有效时必须是 ASCII。") from exc
        if not 1 <= len(callsign_bytes) <= 8:
            raise ValueError("callsign 有效时必须为 1-8 个 ASCII 字节。")

    lat = _require_encoding_number(record, "lat", -90.0, 90.0)
    lon = _require_encoding_number(record, "lon", -180.0, 180.0)
    altitude = _require_encoding_number(record, "altitude", ALTITUDE_MIN, ALTITUDE_MAX)
    speed = _require_encoding_number(record, "speed", SPEED_MIN, SPEED_MAX)
    heading = _require_encoding_number(
        record, "heading", HEADING_MIN, HEADING_MAX, maximum_inclusive=False
    )
    vertical_rate = _require_encoding_number(
        record, "vertical_rate", VERTICAL_RATE_MIN, VERTICAL_RATE_MAX
    )

    alt_type = record.get("alt_type", "unknown")
    if altitude is None:
        if alt_type not in {"unknown", None}:
            raise ValueError("altitude 为空时 alt_type 应为 unknown。")
        alt_type = "unknown"
    elif alt_type not in {"barometric", "geometric"}:
        raise ValueError("altitude 有效时 alt_type 必须是 barometric 或 geometric。")

    latitude_code = _q((lat + 90.0) / 180.0 * LAT_LON_MAX_CODE) if lat is not None else 0
    longitude_code = _q((lon + 180.0) / 360.0 * LAT_LON_MAX_CODE) if lon is not None else 0
    altitude_code = _q(altitude + 1000.0) if altitude is not None else 0
    speed_code = _q(speed / 0.1) if speed is not None else 0
    heading_code = _q(heading / 0.01) if heading is not None else 0
    vertical_rate_code = (
        _q((vertical_rate + 327.68) / 0.01) if vertical_rate is not None else 0
    )

    # 编码结果必须自然落入协议位宽，禁止用掩码隐藏越界。
    if not 0 <= latitude_code <= LAT_LON_MAX_CODE:
        raise ValueError("latitude_code 超出 22 位范围。")
    if not 0 <= longitude_code <= LAT_LON_MAX_CODE:
        raise ValueError("longitude_code 超出 22 位范围。")
    for name, code in [
        ("altitude_code", altitude_code),
        ("speed_code", speed_code),
        ("heading_code", heading_code),
        ("vertical_rate_code", vertical_rate_code),
    ]:
        if not 0 <= code <= UINT16_MAX:
            raise ValueError(f"{name} 超出 uint16 范围。")

    status_flags = 0
    status_flags |= int(on_ground) << 0
    if altitude is not None and alt_type == "geometric":
        status_flags |= 1 << 1
    if timestamp_source == "last_contact_fallback":
        status_flags |= 1 << 2

    validity_flags = 0
    validity_flags |= int(lat is not None) << 0
    validity_flags |= int(lon is not None) << 1
    validity_flags |= int(altitude is not None) << 2
    validity_flags |= int(speed is not None) << 3
    validity_flags |= int(heading is not None) << 4
    validity_flags |= int(vertical_rate is not None) << 5
    validity_flags |= int(callsign is not None) << 6

    frame = bytearray(FRAME_SIZE)
    frame[0:2] = MAGIC.to_bytes(2, "big")
    frame[2] = VERSION
    frame[3] = MESSAGE_TYPE
    frame[4:6] = FRAME_SIZE.to_bytes(2, "big")
    frame[6:8] = seq.to_bytes(2, "big")
    frame[8:12] = timestamp.to_bytes(4, "big")
    frame[12:15] = int(target_id, 16).to_bytes(3, "big")
    if callsign is not None:
        frame[15:15 + len(callsign_bytes)] = callsign_bytes
    frame[23:26] = latitude_code.to_bytes(3, "big")
    frame[26:29] = longitude_code.to_bytes(3, "big")
    frame[29:31] = altitude_code.to_bytes(2, "big")
    frame[31:33] = speed_code.to_bytes(2, "big")
    frame[33:35] = heading_code.to_bytes(2, "big")
    frame[35:37] = vertical_rate_code.to_bytes(2, "big")
    frame[37] = status_flags
    frame[38] = validity_flags
    frame[39:41] = calculate_checksum(frame[:39]).to_bytes(2, "big")
    return bytes(frame)


# =========================
# M2 核心函数 4：Decoder
# =========================

def _new_decoded_record() -> dict[str, Any]:
    return {
        "magic": None,
        "version": None,
        "message_type": None,
        "message_length": None,
        "target_id": None,
        "callsign": None,
        "timestamp": None,
        "timestamp_source": None,
        "time_source": None,
        "message_seq": None,
        "lat": None,
        "lon": None,
        "altitude": None,
        "alt_type": "unknown",
        "speed": None,
        "heading": None,
        "vertical_rate": None,
        "on_ground": None,
        "status_flags": None,
        "validity_flags": None,
        "latitude_code": None,
        "longitude_code": None,
        "altitude_code": None,
        "speed_code": None,
        "heading_code": None,
        "vertical_rate_code": None,
        "lat_valid": False,
        "lon_valid": False,
        "altitude_valid": False,
        "speed_valid": False,
        "heading_valid": False,
        "vertical_rate_valid": False,
        "callsign_valid": False,
        "checksum": None,
        "expected_checksum": None,
        "message_valid": False,
        "validation_errors": [],
        "source": "TeachingLink",
        "_validation_issues": [],
    }


def decode_position_message(data: bytes) -> dict[str, Any]:
    """检查接收条件并恢复结构化记录；坏帧返回 message_valid=False，不中断后续处理。"""
    result = _new_decoded_record()
    issues: list[dict[str, Any]] = result["_validation_issues"]

    if not isinstance(data, (bytes, bytearray)):
        issues.append(_issue("frame", "TYPE_ERROR", type(data).__name__, "帧必须是 bytes 或 bytearray。"))
        result["validation_errors"] = ["TYPE_ERROR"]
        return result

    raw = bytes(data)

    # 1. 实际长度必须先检查，长度错误时不继续按固定偏移读取。
    if len(raw) != FRAME_SIZE:
        issues.append(_issue(
            "frame_length", "LENGTH_ERROR", len(raw),
            f"实际帧长度必须为 {FRAME_SIZE} 字节。",
        ))
        result["validation_errors"] = ["LENGTH_ERROR"]
        return result

    # 固定偏移读取，大端。
    magic = int.from_bytes(raw[0:2], "big")
    version = raw[2]
    message_type = raw[3]
    message_length = int.from_bytes(raw[4:6], "big")
    message_seq = int.from_bytes(raw[6:8], "big")
    timestamp = int.from_bytes(raw[8:12], "big")
    target_id = f"{int.from_bytes(raw[12:15], 'big'):06x}"
    callsign_bytes = raw[15:23]
    latitude_code = int.from_bytes(raw[23:26], "big")
    longitude_code = int.from_bytes(raw[26:29], "big")
    altitude_code = int.from_bytes(raw[29:31], "big")
    speed_code = int.from_bytes(raw[31:33], "big")
    heading_code = int.from_bytes(raw[33:35], "big")
    vertical_rate_code = int.from_bytes(raw[35:37], "big")
    status_flags = raw[37]
    validity_flags = raw[38]
    checksum = int.from_bytes(raw[39:41], "big")
    expected_checksum = calculate_checksum(raw[:39])

    result.update({
        "magic": magic,
        "version": version,
        "message_type": message_type,
        "message_length": message_length,
        "target_id": target_id,
        "timestamp": timestamp,
        "message_seq": message_seq,
        "status_flags": status_flags,
        "validity_flags": validity_flags,
        "latitude_code": latitude_code,
        "longitude_code": longitude_code,
        "altitude_code": altitude_code,
        "speed_code": speed_code,
        "heading_code": heading_code,
        "vertical_rate_code": vertical_rate_code,
        "checksum": checksum,
        "expected_checksum": expected_checksum,
    })

    # 2. message_length 与帧头。
    if message_length != FRAME_SIZE:
        issues.append(_issue(
            "message_length", "LENGTH_ERROR", message_length,
            f"message_length 必须为 {FRAME_SIZE}。",
        ))
    if magic != MAGIC:
        issues.append(_issue(
            "magic", "MAGIC_ERROR", f"0x{magic:04x}",
            f"magic 必须为 0x{MAGIC:04x}。",
        ))
    if version != VERSION:
        issues.append(_issue(
            "version", "VERSION_ERROR", version,
            f"version 必须为 {VERSION}。",
        ))
    if message_type != MESSAGE_TYPE:
        issues.append(_issue(
            "message_type", "MESSAGE_TYPE_ERROR", message_type,
            f"message_type 必须为 {MESSAGE_TYPE}。",
        ))

    # 3. checksum。
    if checksum != expected_checksum:
        issues.append(_issue(
            "checksum", "CHECKSUM_ERROR", checksum,
            f"checksum={checksum}，接收端重算值={expected_checksum}。",
        ))

    # 4. 保留位。
    lat_reserved_bad = bool(latitude_code & 0xC00000)
    lon_reserved_bad = bool(longitude_code & 0xC00000)
    if lat_reserved_bad:
        issues.append(_issue(
            "latitude_code", "RESERVED_BITS_ERROR", latitude_code,
            "latitude_code 容器最高 2 位必须为 0。",
        ))
    if lon_reserved_bad:
        issues.append(_issue(
            "longitude_code", "RESERVED_BITS_ERROR", longitude_code,
            "longitude_code 容器最高 2 位必须为 0。",
        ))
    if status_flags & 0xF8:
        issues.append(_issue(
            "status_flags", "RESERVED_BITS_ERROR", status_flags,
            "status_flags 的 bit3-bit7 必须为 0。",
        ))
    if validity_flags & 0x80:
        issues.append(_issue(
            "validity_flags", "RESERVED_BITS_ERROR", validity_flags,
            "validity_flags 的 bit7 必须为 0。",
        ))

    # 5. 有效位。
    validity = {
        "lat_valid": bool(validity_flags & (1 << 0)),
        "lon_valid": bool(validity_flags & (1 << 1)),
        "altitude_valid": bool(validity_flags & (1 << 2)),
        "speed_valid": bool(validity_flags & (1 << 3)),
        "heading_valid": bool(validity_flags & (1 << 4)),
        "vertical_rate_valid": bool(validity_flags & (1 << 5)),
        "callsign_valid": bool(validity_flags & (1 << 6)),
    }
    result.update(validity)

    # 6. 有效位为 0 时，对应占位必须为 0。
    numeric_placeholders = [
        ("latitude_code", "lat_valid", latitude_code),
        ("longitude_code", "lon_valid", longitude_code),
        ("altitude_code", "altitude_valid", altitude_code),
        ("speed_code", "speed_valid", speed_code),
        ("heading_code", "heading_valid", heading_code),
        ("vertical_rate_code", "vertical_rate_valid", vertical_rate_code),
    ]
    for field, valid_name, code in numeric_placeholders:
        if not result[valid_name] and code != 0:
            issues.append(_issue(
                field, "FLAG_VALUE_INCONSISTENCY", code,
                f"{field} 的有效位为 0，但占位整数不是 0。",
            ))

    if not result["callsign_valid"] and any(callsign_bytes):
        issues.append(_issue(
            "callsign", "FLAG_VALUE_INCONSISTENCY", callsign_bytes.hex(),
            "callsign 有效位为 0，但 8 字节占位区不是全 0。",
        ))

    # 7. 恢复状态和来源。
    result["on_ground"] = bool(status_flags & (1 << 0))
    time_source = (
        "last_contact_fallback"
        if status_flags & (1 << 2)
        else "position_time"
    )
    result["timestamp_source"] = time_source
    result["time_source"] = time_source

    # 8. 按 callsign 有效位恢复 ASCII 和补 0。
    if result["callsign_valid"]:
        trimmed = callsign_bytes.rstrip(b"\x00")
        if not trimmed:
            issues.append(_issue(
                "callsign", "FLAG_VALUE_INCONSISTENCY", callsign_bytes.hex(),
                "callsign 有效位为 1，但有效内容为空。",
            ))
        elif b"\x00" in trimmed:
            issues.append(_issue(
                "callsign", "ENCODING_ERROR", callsign_bytes.hex(),
                "callsign 的补 0 只能位于有效 ASCII 内容之后。",
            ))
        else:
            try:
                result["callsign"] = trimmed.decode("ascii")
            except UnicodeDecodeError:
                issues.append(_issue(
                    "callsign", "ENCODING_ERROR", callsign_bytes.hex(),
                    "callsign 有效内容必须是 ASCII。",
                ))

    # 9. 按有效位恢复物理量；无效字段保持 None。
    if result["lat_valid"] and not lat_reserved_bad:
        result["lat"] = latitude_code / LAT_LON_MAX_CODE * 180.0 - 90.0
    if result["lon_valid"] and not lon_reserved_bad:
        result["lon"] = longitude_code / LAT_LON_MAX_CODE * 360.0 - 180.0
    if result["altitude_valid"]:
        result["altitude"] = float(altitude_code - 1000)
    if result["speed_valid"]:
        result["speed"] = speed_code * 0.1
    if result["heading_valid"]:
        result["heading"] = heading_code * 0.01
        if not 0.0 <= result["heading"] < 360.0:
            issues.append(_issue(
                "heading", "OUT_OF_RANGE", result["heading"],
                "有效 heading 解码后必须满足 0 <= heading < 360。",
            ))
    if result["vertical_rate_valid"]:
        result["vertical_rate"] = vertical_rate_code * 0.01 - 327.68

    result["alt_type"] = (
        "geometric"
        if result["altitude_valid"] and (status_flags & (1 << 1))
        else "barometric"
        if result["altitude_valid"]
        else "unknown"
    )

    result["validation_errors"] = [item["problem_type"] for item in issues]
    result["message_valid"] = len(issues) == 0
    return result


# =========================
# M2 输出文件
# =========================

def _to_log_rows(
    issues: list[dict[str, Any]],
    record_no: int,
    target_id: str | None,
    stage: str,
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    rows = []
    for item in issues:
        rows.append({
            "record_no": record_no,
            "target_id": target_id or "",
            "stage": stage,
            "field": item["field"],
            "problem_type": item["problem_type"],
            "value": "" if item["value"] is None else item["value"],
            "description": prefix + item["description"],
        })
    return rows


def _decoded_csv_row(decoded: dict[str, Any]) -> dict[str, Any]:
    row = {field: decoded.get(field) for field in DECODED_FIELDS}
    if isinstance(row["validation_errors"], list):
        row["validation_errors"] = "|".join(row["validation_errors"])
    return row


def _roundtrip_rows(source: dict[str, Any], decoded: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        ("lat", "latitude_code", "lat_valid", 0, 180.0 / LAT_LON_MAX_CODE),
        ("lon", "longitude_code", "lon_valid", 1, 360.0 / LAT_LON_MAX_CODE),
        ("altitude", "altitude_code", "altitude_valid", 2, 1.0),
        ("speed", "speed_code", "speed_valid", 3, 0.1),
        ("heading", "heading_code", "heading_valid", 4, 0.01),
        ("vertical_rate", "vertical_rate_code", "vertical_rate_valid", 5, 0.01),
    ]

    rows = []
    for field, code_field, valid_field, bit, tolerance in specs:
        source_value = source.get(field)
        decoded_value = decoded.get(field)
        source_valid = source_value is not None
        decoded_valid = bool(decoded.get(valid_field))

        if source_valid and decoded_valid and decoded_value is not None:
            error = abs(float(source_value) - float(decoded_value))
            passed = error <= tolerance + 1e-12
            error_tolerance = f"{error:.12g}/{tolerance:.12g}"
        elif not source_valid and not decoded_valid and decoded_value is None:
            passed = True
            error_tolerance = "N/A/N/A"
        else:
            passed = False
            error_tolerance = "N/A/N/A"

        rows.append({
            "field": field,
            "source_value": "" if source_value is None else source_value,
            "source_valid": source_valid,
            "protocol_code": decoded.get(code_field),
            "flag_bit": f"bit{bit}={1 if decoded_valid else 0}",
            "decoded_value": "" if decoded_value is None else decoded_value,
            "decoded_valid": decoded_valid,
            "absolute_error/tolerance": error_tolerance,
            "passed": passed,
        })
    return rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _recalculate_checksum(frame: bytearray) -> None:
    frame[39:41] = calculate_checksum(frame[:39]).to_bytes(2, "big")


def _build_invalid_frame_tests(reference_frame: bytes) -> list[tuple[str, bytes]]:
    """按手册第 5.3 节构造 5 类错误帧。"""
    tests: list[tuple[str, bytes]] = []

    # 1. 长度错误
    tests.append(("长度错误", reference_frame[:-1]))

    # 2. 头字段错误，修改 magic 后重算 checksum，保证只触发头字段检查
    bad_header = bytearray(reference_frame)
    bad_header[0] ^= 0x01
    _recalculate_checksum(bad_header)
    tests.append(("头字段错误", bytes(bad_header)))

    # 3. checksum 错误
    bad_checksum = bytearray(reference_frame)
    bad_checksum[40] ^= 0x01
    tests.append(("checksum错误", bytes(bad_checksum)))

    # 4. 保留位错误，置 validity_flags.bit7
    bad_reserved = bytearray(reference_frame)
    bad_reserved[38] |= 0x80
    _recalculate_checksum(bad_reserved)
    tests.append(("保留位错误", bytes(bad_reserved)))

    # 5. 标志/占位不一致，清除纬度有效位但保留非零 latitude_code
    bad_flag_value = bytearray(reference_frame)
    bad_flag_value[38] &= 0xFE
    _recalculate_checksum(bad_flag_value)
    tests.append(("标志占位不一致", bytes(bad_flag_value)))

    return tests


def run_m2(
    input_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, int]:
    """读取 raw_states.json，生成 M2 要求的 4 个 output 文件。"""
    student_package_root = Path(__file__).resolve().parents[1]
    input_path = input_path or student_package_root / "data" / "raw_states.json"
    output_dir = output_dir or student_package_root / "output"

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    states = payload.get("states")
    if not isinstance(states, list):
        raise ValueError("raw_states.json 顶层 states 必须是数组。")

    frames: list[bytes] = []
    decoded_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    roundtrip_rows: list[dict[str, Any]] = []

    # 教学样例 partner_messages_sample.bin 的序号从 1 开始。
    next_sequence = 1

    for record_no, vector in enumerate(states, start=1):
        parsed = parse_state_vector(vector)
        target_id = parsed.get("target_id")

        validation_rows.extend(_to_log_rows(
            parsed.get("_validation_issues", []),
            record_no,
            target_id,
            "parse",
        ))

        # 必需字段不可用时不能生成正常帧。
        if not parsed["record_valid"]:
            continue

        try:
            frame = encode_position_message(parsed, next_sequence)
        except (TypeError, ValueError, OverflowError) as exc:
            validation_rows.append({
                "record_no": record_no,
                "target_id": target_id or "",
                "stage": "encode",
                "field": "frame",
                "problem_type": "ENCODING_ERROR",
                "value": "",
                "description": str(exc),
            })
            continue

        frames.append(frame)
        next_sequence = (next_sequence + 1) % 65536

        decoded = decode_position_message(frame)
        decoded_rows.append(_decoded_csv_row(decoded))
        roundtrip_rows.extend(_roundtrip_rows(parsed, decoded))
        validation_rows.extend(_to_log_rows(
            decoded.get("_validation_issues", []),
            record_no,
            decoded.get("target_id"),
            "decode",
        ))

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "encoded_messages.bin").write_bytes(b"".join(frames))

    _write_csv(
        output_dir / "decoded_partner_states.csv",
        DECODED_FIELDS,
        decoded_rows,
    )

    # 错误帧只用于验证接收端，不写入 encoded_messages.bin。
    if frames:
        first_test_record_no = len(states) + 1
        for offset, (name, bad_frame) in enumerate(_build_invalid_frame_tests(frames[0])):
            decoded_bad = decode_position_message(bad_frame)
            validation_rows.extend(_to_log_rows(
                decoded_bad.get("_validation_issues", []),
                first_test_record_no + offset,
                decoded_bad.get("target_id"),
                "decode",
                prefix=f"构造错误帧[{name}]：",
            ))

    _write_csv(
        output_dir / "validation_log.csv",
        VALIDATION_LOG_FIELDS,
        validation_rows,
    )
    _write_csv(
        output_dir / "roundtrip_report.csv",
        ROUNDTRIP_FIELDS,
        roundtrip_rows,
    )

    return {
        "input_records": len(states),
        "encoded_frames": len(frames),
        "decoded_frames": len(decoded_rows),
        "validation_events": len(validation_rows),
        "roundtrip_rows": len(roundtrip_rows),
    }


def main() -> int:
    summary = run_m2()
    print(
        "M2 完成："
        f"输入 {summary['input_records']} 条，"
        f"生成 {summary['encoded_frames']} 帧，"
        f"解码 {summary['decoded_frames']} 帧，"
        f"验证事件 {summary['validation_events']} 条，"
        f"往返检查 {summary['roundtrip_rows']} 行。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
