# M2-M6 综合运行说明

## 基本信息

- 姓名：甄元浩
- 学号：51295902187
- GitHub 用户名：HaoHaovo
- Python 版本：3.14.7
- 是否使用 SQLite：是
- M4 候选来源：学校预生成候选
- M1 状态：按当前任务要求暂不提交

## 安装与运行

先按 `environment/README_environment.md` 在课程包根目录建立独立 `.venv`。从空输出目录复现 M2-M6：

```powershell
Remove-Item student_package\output\* -Recurse -Force
.\.venv\Scripts\python.exe student_package\src_skeleton\run_all.py
```

程序不会使用助教检查点。运行结束后执行：

```powershell
.\.venv\Scripts\python.exe environment\run_student_checks.py
.\.venv\Scripts\python.exe environment\check_student_submission.py --strict
```

严格检查中的 M1 两项会按当前阶段安排保留为未完成，其余 M2-M6 项应通过。

## 程序入口与调用顺序

统一入口为 `student_package/src_skeleton/run_all.py`，调用顺序为：

1. `m2_protocol.py`：解析 OpenSky 状态、编码 41 字节帧、解码与接收校验。
2. `m3_tracks.py`：批量解码多时刻帧、建立航迹、提取当前态势并写入 SQLite。
3. `m4_mapping.py`：复制候选、人工规则核验、双来源统一态势映射。
4. `m5_quality.py`：执行四类固定规则，输出告警和质量态势。
5. `run_all.py`：复核所有 CSV、NDJSON、二进制和 SQLite 结果，并生成 `experiment_summary.json`。

## 输入文件

- M2：`data/raw_states.json`、OpenSky 字段字典、TeachingLink 41 字节规范。
- M3：`data/partner_messages_multitime.bin`，共 369 字节、9 帧。
- M4：M3 的 `current_situation.csv`、`data/m4/partner_current_situation.csv`、预生成候选和统一模型。
- M5：`data/m5/anomaly_cases.csv` 与 `data/m5/anomaly_rules.csv`。

## 输出文件

- M2：`encoded_messages.bin`、`decoded_partner_states.csv`、`validation_log.csv`、`roundtrip_report.csv`。
- M3：`decoded_multitime.csv`、`track_table.csv`、`current_situation.csv`、`states.db`。
- M4：`llm_mapping_candidate.csv`、`verified_mapping_table.csv`、`unified_situation.ndjson`。
- M5：`alert_log.csv`、`quality_situation.csv`。
- M6：`experiment_summary.json`；M4/M5 说明和本 README 位于 `docs/` 与学生包根目录。

## 实验结果

- M2：处理 5 条源记录，4 条满足必需字段并编码为 4 个 41 字节帧；4 帧全部成功解码；24 项往返精度检查通过。
- M3：解码 9 帧，9 帧全部可接受；形成 3 个目标、9 条航迹记录和 3 条当前态势；SQLite 写入 9 条记录。
- M4：核验 8 条候选，扩展形成 27 条正式映射；生成 6 条统一态势消息，其中 OpenSky 与 TeachingLink 各 3 条。
- M5：检查 6 条记录，产生 4 条告警：位置缺失、延迟、联合键重复和航向越界各 1 条；HIGH 1 条、MEDIUM 3 条。

## 已知限制

- TeachingLink 是课程自定义教学协议，不对应真实装备或行业标准。
- 输入流按固定 41 字节边界切分；记录并忽略不完整尾帧，不实现失步重同步。
- SQLite 仅用于本地持久化和查询演示，不是必做前置服务。
- M4 使用学校预生成候选；正式结果由权威字段定义和人工核验规则确定。
- 当前按要求暂不完成 M1，因此最终严格检查仍会报告 M1 两项缺失。

## 最终提交信息

- 仓库链接：https://github.com/HaoHaovo/data-link-51295902187-HaoHaovo
- 最终 commit ID：未提交（按当前要求）
- 最后检查日期：2026-08-26
