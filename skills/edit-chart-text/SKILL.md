---
name: edit-chart-text
description: 替换科学图表、地图和工程图 PNG/JPG/JPEG 中用户指定的文字或数字，同时保持周边像素不变。当用户要求替换一个或全部出现位置时使用。
---

# Edit Chart Text

按以下流程执行局部文字替换：

1. 要求提供输入图片；若图片缺失，只询问上传图片或提供路径。
2. 每次从当前请求提取 `old_text`、`new_text`、`scope`、`location_hint` 和 `match_mode`，绝不复用示例或历史值。缺少旧文字或新文字时先补齐。
3. 仅当用户明确说“全部”时设置 `scope=all`；多处且未说明范围时用 `scope=ask`；单处明确目标用 `scope=one`。`location_hint` 仅作人工诊断上下文。
   - 用户要求修改完整 OCR 标签内部的字面片段时，设置 `match_mode=substring`；否则使用默认的 `match_mode=exact`。
   - 对 `substring` 执行区分大小写的字面匹配；不要使用正则表达式或模糊匹配。
   - 同一完整标签内多次出现 `old_text` 且用户未指定有效位置时，不要猜测 `substring_occurrence`；返回候选并要求确认。
4. 在输入图片旁写 UTF-8 JSON 请求文件；相对 `image_path` 以请求文件目录为基准。运行 `edit-chart-text --request <request.json>`，绝不覆盖原图。
5. 只使用 CLI 输出及 JSON 报告中的实际产物路径。每次运行都有随机 `run_id`，输出、预览和报告名均唯一；不得推断或依赖固定文件名。
6. 退出码：`0` 成功，`2` 请求无效，`3` 需要确认，`4` 处理失败。
7. 退出码 3 时，展示 CLI 返回的 candidates 路径，并读取 CLI 返回的 report 路径。报告的 `run_id`、`source_sha256` 和候选 `candidate_token` 将 `candidate_number`、`polygon`、`match_mode`、`source_label`、`target_label` 及存在时的 `substring_occurrence` 绑定到本次确认。
8. 二次请求必须保持 `replacements` 原顺序，在顶层加入 `confirmation_report_path`，并从同一候选记录原样复制确认字段 `candidate_token`、`candidate_number`、`polygon`（写入 `candidate_polygon`），以及候选记录存在时的 `substring_occurrence`；对应 replacement 设置 `scope=one`。不得拼接来自不同候选的字段，也不得只传编号。
9. 若报告过期、原图内容变化、replacement 顺序/文字变化或 token/number/polygon 不匹配，停止编辑并重新获取候选报告。当前 OCR 仅允许对已绑定 polygon 做轻微几何漂移匹配。
10. 成功时展示 CLI 返回的 output 路径并链接 report 路径。报告是该次事务最后的提交标记，包含样式、允许框、像素差异和编辑后 OCR 校验。
11. 失败时保留原图；仅在需要诊断时读取 [references/troubleshooting.md](references/troubleshooting.md)。
