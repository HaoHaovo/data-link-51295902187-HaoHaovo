# M5 异常结果说明

- 批次时间：`1710000120`。
- 四类必做规则：位置缺失、延迟、`target_id + timestamp` 联合键重复、航向越界均已运行。
- 输入记录：6 条；告警总数：4 条。
- 按类型统计：`POSITION_MISSING=1`、`DATA_DELAYED=1`、`DUPLICATE_RECORD=1`、`HEADING_OUT_OF_RANGE=1`。
- 按等级统计：`HIGH=1`、`MEDIUM=3`。
- 正常记录 `780abc` 未被误报，状态为 `NORMAL`。
- `heading=360` 不满足 `0 <= heading < 360`，触发越界告警；`heading` 为空时不触发航向越界规则。
- 重复判断使用 `target_id + timestamp` 联合键；同一目标不同时间不会被判重。
- 字段缺失属于业务数据质量问题；`message_valid=false` 表示帧或结构校验失败；来源真实性不由 TeachingLink 的 `message_valid` 保证。
