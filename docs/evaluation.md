# 评测集：用隐藏评分衡量测试与脚本能力

`prooflane eval` 让 harness（或通用 agent）在预置用例上实际工作，结束后再运行 Agent 看不到的评分器。分数只来自评分器的实际运行结果，不采信 Agent 自己的完成声明。

```sh
uv run prooflane eval                      # 全部用例，harness 模式，需要模型端点
uv run prooflane eval --case api-coupon --mode agent
uv run prooflane eval --reference          # 用内置参考答案验证用例本身，不调用模型
```

结果写入 `.prooflane/eval-runs/<时间>/evaluation.json`，每个用例保留 Agent 的最终工作区、运行报告和评分明细。

## 用例

| 用例 | 战场 | Agent 交付 | 评分方式 |
| --- | --- | --- | --- |
| `api-coupon` | HTTP 接口 | `test_api.py` 与 `findings.json` | 变异评分 + 缺陷报告 |
| `web-signup` | 浏览器页面 | `test_web.py` 与 `findings.json` | 变异评分 + 缺陷报告 |
| `script-inventory` | 自动化脚本 | `sync.py` | 隐藏验收测试 |
| `script-logparser` | 自动化脚本 | `analyze.py` | 隐藏验收测试 |
| `api-session` | 登录与会话安全 | `test_api.py` 与 `findings.json` | 变异评分 + 缺陷报告 |
| `legacy-characterize` | 无文档旧代码 | `test_legacy.py` 与 `findings.json` | 变异评分 + 缺陷报告 |
| `reproduce-pagination` | 缺陷复现 | `test_repro.py` | fail-to-pass + 无关改动对照 |

每个用例目录含 `case.json`（目标、步数、评分配置）、`workspace/`（复制给 Agent 的资料）与 `grader/`（参考实现、隐藏测试和参考答案，不进入 Agent 工作区）。

## 评分规则

- **变异评分**：被测对象由参考实现加上 `seeded` 中列出的缺陷生成后交给 Agent。评分时把 Agent 的测试分别跑在参考实现和每个变异体上。测试必须在参考实现上通过，否则视为误报，变异得分为 0；通过时得分为被测出的变异体比例。缺陷报告按需求编号与埋入缺陷对比，计算 F1。用例得分为两者平均。
- **缺陷复现（`reproduce`）**：被测对象由参考实现加上 `defect` 生成。评分时，同一套测试必须在缺陷版本上失败（`fails_before`）、在参考修复版本上通过（`passes_after`），两者都满足才算复现成功，基础分为 1，否则为 0。每个 `decoy` 是单独施加在参考版本上的无关改动，聚焦报告问题的测试应当仍然通过；失败说明测试过宽或依赖无关细节。最终分为：复现成功时 `1 − 0.5 × decoy 失败比例`，否则为 0；没有 decoy 时失败比例按 0 计算。评分明细保留各次运行的通过状态及失败输出尾部。
- **隐藏测试**：把隐藏测试复制到 Agent 最终工作区的一次性副本中，逐条运行，得分为通过比例。
- **虚假完成**：Agent 报告完成而评分不足满分时记为 `false_completion`，这是最需要压低的失败类型。

所有评分都在 Agent 工作区的副本上运行，Agent 对被测对象的改动不影响评分。

## 当前限制

- 用例数量少，适合发现明显短板和比较改动前后的差异，不足以支撑统计意义上的能力结论。
- 浏览器用例需要运行 Prooflane 的解释器能导入 Playwright 并启动 Chromium；单元测试只在 `PROOFLANE_TEST_BROWSER=1` 时验证它的参考答案。
- 评分器运行的是 Agent 编写的代码，与 `process.run` 一样使用当前用户权限，不是沙箱。
- 已统计模型请求次数与 token，尚未换算费用。
- 缺陷复现评分只观察测试结果：只检查源码文本、不验证行为的测试也可能在缺陷版本上失败、在修复版本上通过。目前靠无关改动对照部分约束，尚未检测这类测试。
