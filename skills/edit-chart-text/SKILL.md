---
name: edit-chart-text
description: 替换科学图表、地图和工程图 PNG/JPG/JPEG 中用户指定的文字或数字，同时保持周边像素不变。当用户要求替换一个或全部出现位置时使用。
---

# Edit Chart Text

按以下流程执行局部文字替换：

1. 要求提供输入图片。若图片缺失，只询问用户上传图片或提供路径。
2. 从当前请求提取 `old_text`、`new_text`、`scope` 和 `location_hint`；绝不复用示例或历史请求中的值。
3. 若缺少 `old_text` 或 `new_text` 中任一值，只询问一个简洁问题以补齐缺失值。
4. 仅当用户明确说“全部”时设置 `scope=all`。若文字可能出现多处且用户未说明范围，设置 `scope=ask`。将单处明确目标设置为 `scope=one`，并用 `location_hint` 描述位置。
5. 在输入图片旁写入 UTF-8 JSON 请求文件，包含 `image_path` 和 `replacements`；绝不覆盖原图。
6. 运行 `edit-chart-text --request <request.json>`。
7. 按退出码处理结果：`0` 表示成功；`2` 表示参数或请求无效，修正后重试；`3` 表示需要用户确认；`4` 表示处理失败。
8. 遇到歧义时，展示 `<stem>_candidates.png`，报告候选数量，并询问用户要修改候选编号还是全部。模糊匹配只能进入确认流程，绝不自动修改。
9. 将 `candidate_number` 视为本次 OCR 候选序列的全局 1-based 编号，而不是报告行号。用户选择编号后，从 `<stem>_edit-report.json` 查找该编号及其 `replacement_index`。重写请求，在对应 replacement 中设置该 `candidate_number` 与 `scope=one`，再运行命令；即使删减其他 replacements 也保留该全局编号。
10. 成功时，展示 `<stem>_edited.png`，并链接 `<stem>_edit-report.json` 报告。
11. 失败时保留原图；仅在需要诊断时读取 [references/troubleshooting.md](references/troubleshooting.md)。
