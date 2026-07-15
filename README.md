# Image Text Edit / 科学图表文字无痕替换

[中文](#中文说明) | [English](#english)

`edit-chart-text` is a conservative command-line tool and Codex Skill for replacing user-specified text or numbers in scientific charts, maps, and engineering drawings. It uses OCR to locate targets, edits only approved regions, preserves the source image, and writes a JSON audit report for every run.

`edit-chart-text` 是一个面向科学图表、地图和工程图的保守型命令行工具与 Codex Skill。它通过 OCR 定位目标，只修改经过确认的局部区域，不覆盖原图，并为每次运行生成 JSON 审计报告。

## 中文说明

### 功能特点

- 支持 PNG、JPG、JPEG 图片。
- 支持完整文字精确替换，以及完整 OCR 标签内部的字面子串替换。
- 支持替换一个位置、全部位置，或在存在歧义时生成候选供人工确认。
- 自动检测 OCR 目标、估计原文字样式、修复背景并重绘新文字。
- 原图永不覆盖；每次运行使用唯一 `run_id` 生成结果图、候选预览和报告。
- 对越界目标、模糊匹配、过期确认和非目标区域像素变化采取严格拒绝策略。

### 系统要求

- Python 3.11 或更高版本
- Windows、macOS 或 Linux
- 首次执行 OCR 时可能需要联网下载 PaddleOCR/PaddlePaddle 模型

本项目主要在 Windows + PowerShell 环境中验证。

### 安装

克隆仓库：

```bash
git clone https://github.com/surfy2024/image-text-edit.git
cd image-text-edit
```

建议使用虚拟环境。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .\skills\edit-chart-text
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./skills/edit-chart-text
```

确认安装成功：

```bash
edit-chart-text --help
```

### 使用方法

工具接收一个 UTF-8 JSON 请求文件。请求文件建议与输入图片放在同一目录；相对 `image_path` 将以请求文件所在目录为基准解析。

#### 1. 精确替换完整标签

创建 `request.json`：

```json
{
  "image_path": "chart.png",
  "replacements": [
    {
      "old_text": "P10",
      "new_text": "P40",
      "scope": "one",
      "match_mode": "exact",
      "location_hint": "图中左上区域"
    }
  ]
}
```

运行：

```bash
edit-chart-text --request request.json
```

#### 2. 替换标签中的字面子串

例如把所有 `HZ` 前缀替换为 `CS`，同时保留标签其余部分：

```json
{
  "image_path": "chart.png",
  "replacements": [
    {
      "old_text": "HZ",
      "new_text": "CS",
      "scope": "all",
      "match_mode": "substring"
    }
  ]
}
```

`substring` 为区分大小写的字面匹配，不使用正则表达式或模糊匹配。

#### 3. 一次请求多个替换

```json
{
  "image_path": "chart.png",
  "replacements": [
    {
      "old_text": "HZ",
      "new_text": "CS",
      "scope": "all",
      "match_mode": "substring"
    },
    {
      "old_text": "南海奋进",
      "new_text": "NHXW",
      "scope": "one",
      "match_mode": "substring",
      "location_hint": "图中央 FPSO 标签"
    }
  ]
}
```

### 范围参数

| `scope` | 含义 |
|---|---|
| `one` | 只替换一个明确目标；存在多个候选时要求确认 |
| `all` | 替换全部可信匹配，仅在用户明确要求“全部”时使用 |
| `ask` | 先生成候选报告，由用户选择目标 |

### 候选确认

当命令返回退出码 `3` 时，编辑尚未提交。请打开 CLI 输出所指向的候选预览和 JSON 报告，核对完整标签、位置和候选编号。二次确认请求必须原样携带报告中的 `candidate_token`、`candidate_number`、`polygon` 等绑定字段；不要只传候选编号，也不要混用不同报告的字段。

### 输出与退出码

CLI 会打印本次运行的真实输出路径。不要依赖固定文件名，因为每次运行都会生成新的随机 `run_id`。

| 退出码 | 含义 |
|---:|---|
| `0` | 编辑成功 |
| `2` | 请求格式或参数无效 |
| `3` | 需要人工确认候选 |
| `4` | OCR、编辑或验证失败；原图保持不变 |

成功报告包含源图摘要、匹配目标、样式、允许修改区域、像素差异及编辑后 OCR 校验结果。

### 运行测试

```bash
python -m pip install -e "./skills/edit-chart-text[test]"
python -m pytest skills/edit-chart-text/tests
```

真实 OCR 集成测试可能下载模型，需显式启用：

```bash
python -m pytest -m integration skills/edit-chart-text/tests
```

### 使用限制

- `old_text` 和 `new_text` 都必须是非空字符串，因此当前 CLI 不支持将文字直接替换为空字符串来实现删除。
- 低清晰度、严重倾斜、复杂纹理背景或 OCR 无法可靠识别的目标可能需要人工确认，或被安全策略拒绝。
- 工具不会为了“尽量成功”而自动采用模糊匹配。

更多诊断建议见 [`skills/edit-chart-text/references/troubleshooting.md`](skills/edit-chart-text/references/troubleshooting.md)。

---

## English

### Features

- Supports PNG, JPG, and JPEG images.
- Replaces complete OCR labels or literal substrings inside labels.
- Supports one target, all trusted matches, or confirmation-first candidate selection.
- Detects targets with OCR, estimates the original style, repairs the background, and redraws the requested text.
- Never overwrites the source image; every run produces uniquely named artifacts and a JSON report.
- Strictly rejects out-of-bounds targets, ambiguous fuzzy matches, stale confirmations, and pixel changes outside approved regions.

### Requirements

- Python 3.11 or newer
- Windows, macOS, or Linux
- Internet access may be required on the first OCR run to download PaddleOCR/PaddlePaddle models

The project is primarily validated on Windows with PowerShell.

### Installation

Clone the repository:

```bash
git clone https://github.com/surfy2024/image-text-edit.git
cd image-text-edit
```

Using a virtual environment is recommended.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .\skills\edit-chart-text
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./skills/edit-chart-text
```

Verify the installation:

```bash
edit-chart-text --help
```

### Usage

The CLI accepts a UTF-8 JSON request file. Keep the request next to the source image when possible. A relative `image_path` is resolved from the request file's directory.

#### 1. Replace a complete label

Create `request.json`:

```json
{
  "image_path": "chart.png",
  "replacements": [
    {
      "old_text": "P10",
      "new_text": "P40",
      "scope": "one",
      "match_mode": "exact",
      "location_hint": "upper-left area of the chart"
    }
  ]
}
```

Run:

```bash
edit-chart-text --request request.json
```

#### 2. Replace a literal substring

The following request changes every trusted `HZ` prefix to `CS` while preserving the rest of each label:

```json
{
  "image_path": "chart.png",
  "replacements": [
    {
      "old_text": "HZ",
      "new_text": "CS",
      "scope": "all",
      "match_mode": "substring"
    }
  ]
}
```

Substring matching is case-sensitive and literal. It does not use regular expressions or fuzzy matching.

#### 3. Apply multiple replacements

```json
{
  "image_path": "chart.png",
  "replacements": [
    {
      "old_text": "HZ",
      "new_text": "CS",
      "scope": "all",
      "match_mode": "substring"
    },
    {
      "old_text": "南海奋进",
      "new_text": "NHXW",
      "scope": "one",
      "match_mode": "substring",
      "location_hint": "central FPSO label"
    }
  ]
}
```

### Scope values

| `scope` | Meaning |
|---|---|
| `one` | Replace one specific target; request confirmation if multiple candidates exist |
| `all` | Replace all trusted matches; use only when the user explicitly requests every occurrence |
| `ask` | Generate candidates first and wait for the user to select a target |

### Candidate confirmation

Exit code `3` means no final edit has been committed. Open the candidate preview and JSON report printed by the CLI, then verify the complete label, position, and candidate number. A follow-up confirmation request must copy the report-bound `candidate_token`, `candidate_number`, `polygon`, and related fields exactly. Do not confirm by number alone or combine fields from different reports.

### Outputs and exit codes

Use the artifact paths printed by the CLI. Do not assume fixed filenames: every run has a random `run_id`.

| Exit code | Meaning |
|---:|---|
| `0` | Edit completed successfully |
| `2` | Invalid request or arguments |
| `3` | Candidate confirmation required |
| `4` | OCR, editing, or validation failed; the source image remains unchanged |

Successful reports include the source digest, matched target, detected style, allowed edit region, pixel differences, and post-edit OCR validation.

### Tests

```bash
python -m pip install -e "./skills/edit-chart-text[test]"
python -m pytest skills/edit-chart-text/tests
```

Real OCR integration tests may download models and must be enabled explicitly:

```bash
python -m pytest -m integration skills/edit-chart-text/tests
```

### Limitations

- Both `old_text` and `new_text` must be non-empty strings, so the current CLI cannot delete text by replacing it with an empty value.
- Low-resolution, heavily rotated, or textured targets may require confirmation or be rejected by the safety checks.
- The tool does not automatically accept fuzzy matches merely to maximize completion.

See [`skills/edit-chart-text/references/troubleshooting.md`](skills/edit-chart-text/references/troubleshooting.md) for troubleshooting details.

## License

No license file is currently included. Add a license before redistributing the project as an open-source package.
