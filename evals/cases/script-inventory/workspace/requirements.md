# 库存同步脚本需求

制作命令行脚本 `sync.py`，把本地盘点结果同步到仓库库存接口。

```sh
python sync.py --base-url http://127.0.0.1:8000 --csv inventory.csv [--dry-run]
```

- **S1 本地数据**：CSV 带表头 `sku,qty`，`qty` 为非负整数。同一 sku 出现多行时数量累加。CSV 格式错误时退出码为 2，标准错误输出说明原因，且不得调用任何修改接口。
- **S2 远端库存分页**：`GET /stock?cursor=<游标>` 返回 `{"items": [{"sku": "A1", "qty": 3}], "next": "游标或 null"}`。首次请求不带 cursor；必须读完所有页。
- **S3 差异**：对本地与远端出现过的每个 sku 计算 `delta = 本地数量 − 远端数量`（缺失一侧视为 0），只处理 delta ≠ 0 的 sku，按 sku 字母序处理。
- **S4 调整**：每个差异发送 `POST /adjust`，请求体 `{"sku": "A1", "delta": -2, "idempotency_key": "<sku>:<delta>"}`。
- **S5 重试**：任何请求遇到 HTTP 503 时最多重试 2 次（总共 3 次尝试）；其他 4xx/5xx 不重试。仍失败时退出码为 1。
- **S6 试运行**：`--dry-run` 只读取和计算，不发送 `POST /adjust`。
- **S7 输出**：成功时退出码 0，标准输出为一个 JSON 对象 `{"adjustments": [{"sku": "A1", "delta": -2}], "dry_run": false}`，顺序同 S3。

运行环境只有 Python 标准库。
