# Troubleshooting

| 问题 | 明确下一步 |
| --- | --- |
| not found | 核对 `old_text` 是否与图片文字完全一致，并提供更精确的 `location_hint` 后重试。 |
| multiple matches | 展示候选预览和数量；用户选编号后，从 JSON 报告映射 `candidate_number` 与 `replacement_index`，在对应 replacement 设置该编号及 `scope=one` 后重试。只有用户明确要求时才设置 `scope=all`。 |
| invalid candidate number | 保留原图，重新读取最新 JSON 报告；确认编号存在且属于对应 `replacement_index`，再重写请求。 |
| replacement does not fit | 缩短 `new_text`，或要求用户确认更大的目标区域后重试。 |
| pixels changed outside | 停止发布结果并保留原图；缩小或重新确认目标框后重试。 |
| OCR model unavailable | 检查 PaddleOCR 及模型是否可用；恢复模型访问后重新运行同一请求。 |
