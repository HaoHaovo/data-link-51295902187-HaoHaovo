# M4 AI 辅助映射核验说明

## 候选来源

- 候选来源：学校预生成候选。
- 候选文件：`student_package/reference/pre_generated_mapping_candidate.csv`。
- 权威依据：`source_field_definitions.md`、两个字段字典、`teaching_message_spec.md` 和 `unified_model.json`。
- 大模型或预生成候选只用于提出待核验项，正式映射由程序中的确定性规则执行。

## 发现并修正的问题

1. 候选把 `latitude_code` 映射到了 `position.lon`，把 `longitude_code` 映射到了 `position.lat`。正式映射分别修正为 `position.lat` 和 `position.lon`。
2. 高度候选遗漏了物理偏置。`altitude_code` 必须恢复为 `code - 1000` 米。
3. 呼号不能只去除补零；必须先检查 `validity_flags.bit6`。有效位为 0 时统一值为 `null`。
4. `status_flags.bit2` 表示时间来源回退，不代表时间无效。正式映射写入 `quality.time_source`；时间是否有效由正整数时间戳判断。
5. `message_valid` 只表示记录或教学帧通过结构与协议检查，不代表数据来源真实、可信或具备安全完整性。
6. 正式表补全了位置、运动、地面状态、高度来源和质量字段，共形成 27 条人工核验规则。

## 样例验证

- 正常样例：目标 `780abc` 的两种来源均恢复为纬度 `31.250381767840807`、经度 `121.49366891233183`、高度 `9900 m`、航向 `88°`，字段层次和单位一致。
- 真实零值：目标 `000001` 的零高度、零速度、零垂直速度在有效位为 1 时均保留为数值 `0`，没有被误判为缺失。
- 字段缺失：目标 `780def` 的经纬度和速度有效位为 0，两种来源均映射为 `null`，同时 `quality.position_valid=false`；高度、航向等仍可保留。
- 时间来源：`position_time` 与 `last_contact_fallback` 只描述来源，不直接改变 `quality.time_valid`。

## 不应由大模型自行决定的内容

位宽、字节序、定点比例、物理偏置、标志位语义、空值策略、单位、必需字段和接收判据必须以协议和字段定义为准。候选置信度不能替代人工核验，也不能把候选直接当作正式映射答案。
