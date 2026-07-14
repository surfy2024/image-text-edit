# edit-chart-text 子串替换设计

## 背景与目标

当前 `edit-chart-text` 只按完整 OCR 文本匹配和重绘。用户请求 `HZ → CS` 时，OCR 候选实际是 `HZ25-4DPP` 等完整标签，短字符串无法进入安全编辑流程。Skill 层把请求临时展开为完整标签仍可能因长标签 OCR 分组变化而失败。

本次扩展增加显式、严格的子串替换模式。用户可以请求把所有标签中的字面子串 `HZ` 改为 `CS`，管线负责从完整 OCR 标签计算完整目标标签，并继续沿用候选绑定、事务发布、像素边界检查和编辑后 OCR 验证。现有请求默认保持完整文本匹配，不改变行为。

## 方案选择

采用“子串匹配、完整标签重建”方案：

- 用显式 `match_mode="substring"` 启用子串语义。
- 在完整 OCR 候选中定位旧子串，计算完整目标标签。
- 修复和重绘完整候选框，并验证完整目标标签。

不采用按字符宽度估算局部像素框的方案，因为比例字体、中英文混排和旋转文本会使字符边界不可靠。不采用仅由 Skill 展开请求的方案，因为它无法稳定处理长标签，也不能把子串 occurrence 纳入候选令牌与事务审计。

## 请求合同

`Replacement` 增加：

- `match_mode`: `"exact" | "substring"`，默认 `"exact"`。
- `substring_occurrence`: 可选的从 1 开始的正整数。

约束如下：

- 子串匹配区分大小写，使用字面文本；不支持正则、大小写折叠或模糊匹配。
- `substring_occurrence` 仅允许与 `match_mode="substring"`、`scope="one"`、`candidate_number`、`candidate_polygon`、`candidate_token` 和 `confirmation_report_path` 同时使用。
- 子串模式拒绝 `old_text == new_text`。
- exact 模式继续使用现有完整匹配与模糊确认规则。
- substring 模式没有 fuzzy fallback；不存在字面子串时返回 `not_found`。

未知模式、非正整数 occurrence 或非法字段组合必须在请求解析边界抛出带 replacement 索引的 `ValueError`。

## 匹配与候选确认

对于每个合法 OCR 候选：

1. 在候选完整文本中查找所有不重叠的字面 `old_text` occurrence。
2. 恰好出现一次时，计算完整 `target_label`，并按现有 `scope` 规则决定 `ready` 或确认。
3. 出现多次时，不自动编辑；为每个 occurrence 生成独立候选记录。
4. `scope=all` 只自动选择每条标签中 occurrence 唯一的所有合法候选。只要存在多 occurrence、越界或冲突候选，整批请求保持零编辑并返回确认报告。

多 occurrence 确认请求必须指定 `substring_occurrence`。确认仍绑定同一 replacement 顺序、候选编号和 polygon。

候选令牌签名载荷增加：

- `match_mode`
- 完整 `source_label`
- 完整 `target_label`
- `substring_occurrence`

任一字段、报告路径、源图、replacement 顺序、候选编号或 polygon 发生变化时，确认失效并要求重新获取候选。

## 重绘、验证与事务

子串模式不直接估算字符级像素框。管线使用原完整 OCR polygon 修复区域，并绘制计算后的完整 `target_label`。这样保留平台编号、`FPSO`、中文后缀和其他未请求文本。

所有现有安全机制保持有效：

- 候选 polygon 必须完全位于图像内。
- 所有计划修复框必须先做冲突检查。
- 批量编辑在单个源图锁和不可变快照上执行。
- 批准框外像素必须完全不变。
- 图像模式与 alpha 必须保持。
- 输出和报告继续使用唯一 run ID 与 no-replace 原子发布。
- 任一编辑失败时不发布图片，只提交失败报告。

编辑后 OCR 对子串模式验证：

- 完整 `target_label` 在绑定目标几何内恰好出现一次。
- 完整 `source_label` 不再出现。
- 如果 `new_text` 本身包含 `old_text`，不要求旧子串全局消失；完整标签验证仍必须通过。

## 报告与 Skill 工作流

每条 edit/candidate 记录同时包含：

- 用户请求的 `old_text` 和 `new_text`
- `match_mode`
- `source_label` 和 `target_label`
- `substring_occurrence`（适用时）
- 现有候选编号、token、polygon、样式、像素差异和 post-OCR 字段

更新 `SKILL.md`：

- 当用户明确要求修改完整 OCR 标签内部的片段时，写入 `match_mode="substring"`。
- 只有用户明确说“全部”时使用 `scope="all"`。
- 多 occurrence 二次请求必须从同一候选记录原样复制 occurrence、token、编号和 polygon。
- 不得把 exact 与 substring 候选字段混用。

`agents/openai.yaml` 仅在现有描述无法覆盖子串替换触发场景时更新。

## 测试与验收

按照 TDD 逐层增加测试：

1. 请求解析：默认 exact、显式 substring、occurrence 和所有非法组合。
2. 匹配：唯一子串、多个候选、同标签多 occurrence、区分大小写、无 fuzzy fallback。
3. 令牌：模式、源标签、目标标签或 occurrence 被篡改时安全失败。
4. 管线：完整标签重建、`scope=all`、确认闭环、冲突回滚、批准框外不变、post-OCR 成败。
5. 回归：现有 exact 模式测试全部通过。
6. Skill 校验：UTF-8 模式下 `quick_validate.py` 通过。
7. 真实图片：对用户提供的 `010.jpg` 运行新子串请求，要求：
   - 四条 `HZ...` 标签全部变为对应 `CS...` 标签；
   - `HYSY115FPSO` 变为 `HYSYFPSO`；
   - `南海奋进FPSO` 变为 `NHXWFPSO`；
   - 六条 edit 的像素边界与编辑后 OCR 验证全部通过；
   - 原图哈希保持不变。

实现完成后把已验证运行时文件同步到 `C:\Users\飞翔无限\.codex\skills\edit-chart-text`，刷新 editable CLI，并核对源码与安装副本哈希一致。

## 最终安全审查补强

- 二次确认时，当前 OCR 的完整源标签、派生目标标签和 occurrence 必须与已签名候选记录完全一致；仅有 polygon 几何重合不足以授权编辑，任何文本漂移都必须重新确认。
- `scope=all` 必须保持全有或全无：只要有一个按同一匹配模式和置信度阈值本应匹配的候选因 polygon 不安全而被拒，整批不得部分编辑，也不得为不安全候选生成签名记录。
- 人工确认完整标签只允许用于 substring、scope=one 的二次确认。`confirmed_source_label` 与 `confirmed_target_label` 必须成对提供，目标必须严格等于仅替换已绑定 occurrence 后的结果。候选 HMAC 继续绑定 OCR 原始完整标签；最终审计同时记录人工视觉真值、OCR 原始标签和 override 标记，post-OCR 验证人工目标且拒绝人工源或 OCR 源任一残留。
