# Troubleshooting

| 问题 | 明确下一步 |
| --- | --- |
| not found | 核对 `old_text` 是否与图片文字一致后重试。仅把 `location_hint` 当作人工诊断上下文；不要把它当作自动筛选条件。 |
| multiple matches | 展示候选预览和数量；用户选编号后，从 CLI 返回的 JSON 报告同一记录复制 `candidate_token`、`candidate_number`、`polygon` 与 `replacement_index`，顶层设置 `confirmation_report_path`，保持 replacement 顺序并在对应项设置 `scope=one` 后重试。只有用户明确要求时才设置 `scope=all`。 |
| invalid candidate fingerprint | 保留原图并读取最新 JSON 报告；确认编号与 polygon 成对来自同一记录且属于对应 `replacement_index`，再重写请求。 |
| replacement does not fit | 缩短 `new_text`，或要求用户确认更大的目标区域后重试。 |
| pixels changed outside | 停止发布结果并保留原图；缩小或重新确认目标框后重试。 |
| OCR model unavailable | CLI 应以退出码 4 返回简洁错误；若提示模型托管平台不可用，检查本地模型缓存或网络后重试。Windows 中文用户路径相关错误见下方 ASCII 缓存设置。 |

## Windows 中文路径下的 Paddle 模型缓存

若 PaddleX/Paddle 在中文用户目录中报模型 JSON 无法解析、`Permission denied`，或错误路径指向 `.paddlex` / `.cache\paddle`，为当前 PowerShell 进程选择一个**可写且全 ASCII** 的缓存根目录。不要硬编码 `C:\tmp`；可使用团队约定的缓存盘，或先把允许写入的目录临时映射成 ASCII 盘符。

```powershell
# 示例中的 O: 必须先映射到你有写权限的真实目录。
$env:PADDLE_PDX_CACHE_HOME = 'O:\ocr-cache\paddlex'
$env:PADDLE_HOME = 'O:\ocr-cache\paddle'
$env:XDG_CACHE_HOME = 'O:\ocr-cache\xdg'
$env:PADDLE_EXTENSION_DIR = 'O:\ocr-cache\paddle-extension'
```

`PADDLE_PDX_CACHE_HOME` 控制 PaddleX 官方 OCR 模型，其他变量覆盖 Paddle 自身的 home/cache/扩展缓存。若错误仍明确指向用户目录下的 `.cache\paddle`，仅在运行 OCR 的子进程中把 `USERPROFILE` 指向同一 ASCII 缓存根目录下的临时 profile，完成后恢复原值。不要全局永久修改 `USERPROFILE`。模型下载完成后可复用缓存；集成测试结束应删除测试专用缓存和盘符映射。
## 真实 OCR 集成测试

默认测试套件排除 `integration`，不会初始化或下载 OCR 模型。配置好上面的 ASCII 缓存后，显式运行：

```powershell
$env:RUN_REAL_OCR = '1'
python -m pytest -m integration tests/test_real_fixture.py
```

运行结束后删除测试专用模型缓存和 pytest 临时目录。

## 默认 CPU 与 GPU 引擎

默认安装包含兼容的 PaddlePaddle 3.x CPU 引擎。需要 GPU 时，应按 Paddle 官方兼容矩阵卸载 CPU wheel 并安装与本机 CUDA 匹配的 `paddlepaddle-gpu`，不要同时保留两个引擎包。

## 同源锁超时

同一源图的 digest、OCR、编辑、验证和发布由跨进程锁串行化。若报告提示等待源图锁超时，确认没有仍在运行的编辑进程后重试；不要删除或覆盖任何带其他 `run_id` 的产物。
