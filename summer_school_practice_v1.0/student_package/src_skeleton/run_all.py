from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

import m2_protocol
import m3_tracks
import m4_mapping
import m5_quality


STUDENT_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = STUDENT_PACKAGE_ROOT / "output"
PIPELINE_SUMMARY: dict[str, object] = {}

EXPECTED_CSV_FILES = [
    "decoded_partner_states.csv",
    "validation_log.csv",
    "roundtrip_report.csv",
    "decoded_multitime.csv",
    "track_table.csv",
    "current_situation.csv",
    "llm_mapping_candidate.csv",
    "verified_mapping_table.csv",
    "alert_log.csv",
    "quality_situation.csv",
]


def prepare_output_directory() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def parse() -> None:
    input_path = STUDENT_PACKAGE_ROOT / "data" / "raw_states.json"
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    states = payload.get("states")
    if not isinstance(states, list):
        raise ValueError("raw_states.json 顶层 states 必须是数组。")
    parsed = [m2_protocol.parse_state_vector(vector) for vector in states]
    PIPELINE_SUMMARY["parse"] = {
        "input_records": len(states),
        "valid_records": sum(record.get("record_valid") is True for record in parsed),
    }


def encode() -> None:
    PIPELINE_SUMMARY["m2"] = m2_protocol.run_m2(output_dir=OUTPUT_ROOT)


def decode_validate() -> None:
    summary = PIPELINE_SUMMARY.get("m2")
    if not isinstance(summary, dict):
        raise RuntimeError("M2 尚未运行。")
    encoded_path = OUTPUT_ROOT / "encoded_messages.bin"
    encoded_size = encoded_path.stat().st_size
    if encoded_size % m2_protocol.FRAME_SIZE != 0:
        raise RuntimeError("encoded_messages.bin 不是完整 41 字节帧序列。")
    if summary["encoded_frames"] != summary["decoded_frames"]:
        raise RuntimeError("M2 编码帧数与解码帧数不一致。")
    PIPELINE_SUMMARY["m2_validation"] = {
        "encoded_bytes": encoded_size,
        "frame_size": m2_protocol.FRAME_SIZE,
        "all_generated_frames_decoded": True,
    }


def build_tracks() -> None:
    PIPELINE_SUMMARY["m3"] = m3_tracks.run_m3(output_dir=OUTPUT_ROOT, use_sqlite=True)


def map_unified() -> None:
    PIPELINE_SUMMARY["m4"] = m4_mapping.run_m4(output_dir=OUTPUT_ROOT)


def check_quality() -> None:
    PIPELINE_SUMMARY["m5"] = m5_quality.run_m5(output_dir=OUTPUT_ROOT)


def export_results() -> None:
    csv_counts: dict[str, int] = {}
    for filename in EXPECTED_CSV_FILES:
        path = OUTPUT_ROOT / filename
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            csv_counts[filename] = sum(1 for _ in csv.DictReader(handle))

    ndjson_path = OUTPUT_ROOT / "unified_situation.ndjson"
    unified_rows = [
        json.loads(line)
        for line in ndjson_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in unified_rows):
        raise RuntimeError("unified_situation.ndjson 包含非对象记录。")

    sqlite_path = OUTPUT_ROOT / "states.db"
    with sqlite3.connect(sqlite_path) as connection:
        sqlite_rows = connection.execute("SELECT COUNT(*) FROM state_record").fetchone()[0]

    PIPELINE_SUMMARY["export"] = {
        "csv_row_counts": csv_counts,
        "unified_objects": len(unified_rows),
        "sqlite_rows": sqlite_rows,
        "checkpoint_used": False,
        "python_version": sys.version.split()[0],
    }
    (OUTPUT_ROOT / "experiment_summary.json").write_text(
        json.dumps(PIPELINE_SUMMARY, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_pipeline() -> None:
    prepare_output_directory()
    parse()
    encode()
    decode_validate()
    build_tracks()
    map_unified()
    check_quality()
    export_results()


def main() -> int:
    try:
        run_pipeline()
    except Exception as exc:
        print(exc)
        print("M2-M6 综合流水线运行失败。")
        return 2
    print("M2-M6 综合流水线运行完成。")
    for stage, summary in PIPELINE_SUMMARY.items():
        print(f"[{stage}] {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
