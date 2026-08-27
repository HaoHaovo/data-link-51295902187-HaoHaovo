from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from m2_protocol import DECODED_FIELDS, FRAME_SIZE, decode_position_message


TRACK_FIELDS = [
    "target_id", "timestamp", "message_seq", "track_sequence_no",
    "lat", "lon", "altitude", "speed", "heading",
]

CURRENT_FIELDS = [
    "target_id", "callsign", "latest_time", "lat", "lon", "altitude",
    "speed", "heading", "vertical_rate", "on_ground", "track_length",
    "alt_type", "time_source", "message_valid",
]

SQLITE_COLUMNS = [
    "target_id", "callsign", "timestamp", "timestamp_source", "message_seq",
    "lat", "lon", "altitude", "alt_type", "speed", "heading",
    "vertical_rate", "on_ground", "status_flags", "validity_flags",
    "message_valid", "source",
]


def decode_message_stream(data: bytes, frame_size: int = FRAME_SIZE) -> list[dict[str, Any]]:
    """按固定帧长批量解码；忽略不完整尾帧，但不影响完整帧。"""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data 必须是 bytes 或 bytearray。")
    if not isinstance(frame_size, int) or isinstance(frame_size, bool) or frame_size <= 0:
        raise ValueError("frame_size 必须是正整数。")
    if frame_size != FRAME_SIZE:
        raise ValueError(f"TeachingLink 固定帧长必须为 {FRAME_SIZE} 字节。")

    raw = bytes(data)
    complete_length = len(raw) - len(raw) % frame_size
    records: list[dict[str, Any]] = []
    for offset in range(0, complete_length, frame_size):
        decoded = decode_position_message(raw[offset : offset + frame_size])
        decoded["frame_no"] = offset // frame_size + 1
        records.append(decoded)
    return records


def save_records_to_sqlite(records: list[dict[str, Any]], db_path: str) -> None:
    """保存接收记录；Python 的 None 由 sqlite3 写为 SQL NULL。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = """
    CREATE TABLE IF NOT EXISTS state_record (
        record_id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id TEXT,
        callsign TEXT NULL,
        timestamp INTEGER,
        timestamp_source TEXT,
        message_seq INTEGER,
        lat REAL NULL,
        lon REAL NULL,
        altitude REAL NULL,
        alt_type TEXT NULL,
        speed REAL NULL,
        heading REAL NULL,
        vertical_rate REAL NULL,
        on_ground INTEGER,
        status_flags INTEGER,
        validity_flags INTEGER,
        message_valid INTEGER,
        source TEXT
    );
    """
    placeholders = ", ".join("?" for _ in SQLITE_COLUMNS)
    column_sql = ", ".join(SQLITE_COLUMNS)
    values = []
    for record in records:
        row = []
        for column in SQLITE_COLUMNS:
            value = record.get(column)
            if column in {"on_ground", "message_valid"} and value is not None:
                value = int(bool(value))
            row.append(value)
        values.append(tuple(row))

    with sqlite3.connect(path) as connection:
        connection.execute(schema)
        connection.execute("DELETE FROM state_record")
        connection.executemany(
            f"INSERT INTO state_record ({column_sql}) VALUES ({placeholders})",
            values,
        )
        connection.commit()


def _is_acceptable(record: dict[str, Any]) -> bool:
    return (
        record.get("message_valid") is True
        and isinstance(record.get("target_id"), str)
        and isinstance(record.get("timestamp"), int)
        and not isinstance(record.get("timestamp"), bool)
    )


def build_tracks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """仅使用可接受记录，按 target_id 分组并按 timestamp 升序编号。"""
    acceptable = [record for record in records if _is_acceptable(record)]
    acceptable.sort(
        key=lambda item: (
            item["target_id"],
            item["timestamp"],
            item.get("message_seq") if isinstance(item.get("message_seq"), int) else -1,
        )
    )

    counters: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for record in acceptable:
        target_id = record["target_id"]
        counters[target_id] = counters.get(target_id, 0) + 1
        rows.append({
            "target_id": target_id,
            "timestamp": record["timestamp"],
            "message_seq": record.get("message_seq"),
            "track_sequence_no": counters[target_id],
            "lat": record.get("lat"),
            "lon": record.get("lon"),
            "altitude": record.get("altitude"),
            "speed": record.get("speed"),
            "heading": record.get("heading"),
        })
    return rows


def build_current_situation(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个目标保留时间最新的可接受记录，并统计其有效航迹长度。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if _is_acceptable(record):
            grouped.setdefault(record["target_id"], []).append(record)

    rows: list[dict[str, Any]] = []
    for target_id in sorted(grouped):
        track = sorted(
            grouped[target_id],
            key=lambda item: (
                item["timestamp"],
                item.get("message_seq") if isinstance(item.get("message_seq"), int) else -1,
            ),
        )
        latest = track[-1]
        rows.append({
            "target_id": target_id,
            "callsign": latest.get("callsign"),
            "latest_time": latest["timestamp"],
            "lat": latest.get("lat"),
            "lon": latest.get("lon"),
            "altitude": latest.get("altitude"),
            "speed": latest.get("speed"),
            "heading": latest.get("heading"),
            "vertical_rate": latest.get("vertical_rate"),
            "on_ground": latest.get("on_ground"),
            "track_length": len(track),
            "alt_type": latest.get("alt_type", "unknown"),
            "time_source": latest.get("time_source") or latest.get("timestamp_source"),
            "message_valid": latest.get("message_valid"),
        })
    return rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _decoded_row(record: dict[str, Any]) -> dict[str, Any]:
    row = {field: record.get(field) for field in DECODED_FIELDS}
    errors = row.get("validation_errors")
    if isinstance(errors, list):
        row["validation_errors"] = "|".join(errors)
    return row


def run_m3(
    input_path: Path | None = None,
    output_dir: Path | None = None,
    *,
    use_sqlite: bool = True,
) -> dict[str, int]:
    student_root = Path(__file__).resolve().parents[1]
    input_path = input_path or student_root / "data" / "partner_messages_multitime.bin"
    output_dir = output_dir or student_root / "output"

    raw = input_path.read_bytes()
    records = decode_message_stream(raw)
    tracks = build_tracks(records)
    current = build_current_situation(records)

    _write_csv(output_dir / "decoded_multitime.csv", DECODED_FIELDS, [_decoded_row(r) for r in records])
    _write_csv(output_dir / "track_table.csv", TRACK_FIELDS, tracks)
    _write_csv(output_dir / "current_situation.csv", CURRENT_FIELDS, current)
    if use_sqlite:
        save_records_to_sqlite(records, str(output_dir / "states.db"))

    return {
        "input_bytes": len(raw),
        "complete_frames": len(records),
        "accepted_frames": sum(_is_acceptable(record) for record in records),
        "targets": len(current),
        "track_rows": len(tracks),
        "ignored_tail_bytes": len(raw) % FRAME_SIZE,
    }


def main() -> int:
    summary = run_m3()
    print(
        "M3 完成："
        f"解码 {summary['complete_frames']} 帧，"
        f"接受 {summary['accepted_frames']} 帧，"
        f"形成 {summary['targets']} 个目标、{summary['track_rows']} 条航迹记录，"
        f"忽略尾字节 {summary['ignored_tail_bytes']}。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
