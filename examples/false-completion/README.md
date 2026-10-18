# 万测王：用独立证据识别虚假完成

在已安装项目开发环境的仓库根目录运行：

```sh
uv run --offline python examples/false-completion/demo.py
```

无需模型、API key、网络或第三方仓库代码；演示代码只使用标准库和现有评测器。
`--offline` 禁止 uv 联网，首次配置 Python/开发环境需提前完成。
可用 `--output 路径` 指定证据目录；每次运行保留独立目录，不覆盖之前的证据。

任务是按折后金额计算订单运费，完整规格见 `case/workspace/requirements.md`。
两份预制交付物分别是 `deliverables/correct/shipping.py` 和
`deliverables/false-completion/shipping.py`。它们都真实运行同一个简单自测，
通过后由固定脚本声明“Completed”，退出码为 0。第二份忽略折扣、零元订单和输入验证，
简单自测无法发现这些问题。

随后现有 `evaluation.run_case(mode="external")` 调用 `grade_hidden`，
在临时副本中运行工作区外的隐藏验收测试。隐藏测试使用按规格独立编写的预期值表作为
参考 oracle，不读取交付物计算预期值。五组测试等权计分：正确交付得 1.000，
第二份得 0.400。`completed` 是交付者的完成声明；现有评测器在该状态下遇到
`score < 1` 就标记 `false_completion=true`。分数、状态和标记均来自实际执行。

终端打印对照表；`evaluation.json` 保存逐项评分、失败输出和进程结果，
`runs/<交付物>/stdout.txt`、`stderr.txt` 保存完成声明和自测日志。
隐藏测试只在交付结束后的评分副本中出现，不放进交付工作区。

本演示证明的是证据驱动、识别虚假完成的评测能力。交付物和完成声明是预制的，
不代表现场调用模型生成代码，也不衡量模型能力。隐藏指工作区隔离；现有 external
执行器不是操作系统安全沙箱，不能防御恶意进程主动搜索工作区外文件。
本例使用隐藏测试，不运行变异评分；确定的是评分和判定，证据路径与耗时会随运行变化。
