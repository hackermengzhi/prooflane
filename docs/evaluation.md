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

## 与其他 Agent 对比

`external` 模式让命令行 Agent 在相同用例上工作，随后复用相同的隐藏评分器：

```sh
uv run prooflane eval --mode external --agent-command "codex exec --skip-git-repo-check -s workspace-write -"
# 只比较指定用例，并设置每例超时（秒，默认 1800）
uv run prooflane eval --mode external --case script-logparser --agent-timeout 300 --agent-command "python /absolute/path/to/agent.py"
```

`--agent-command` 必填，使用 `shlex.split` 解析为参数列表，不经过 shell；路径带空格时需在命令字符串内加引号，管道和重定向不会展开。每例先准备工作区，再以该目录为当前目录启动命令。目标原文通过 stdin 传入，并同时设置 `PROOFLANE_GOAL`（目标文本）和 `PROOFLANE_WORKSPACE`（工作区绝对路径）。

外部 Agent 继承当前环境，以当前用户权限运行，**不是沙箱**；工作目录不构成文件访问边界。进程管理与现有工具一样支持 macOS/Linux；用例的 `allow_process` 只约束万测王自己的工具，不约束外部 Agent；超时或命令退出后清理本次进程组。评分只观察最终工作区，不依赖 Agent 内部实现。

退出码 0 记为 `completed`，非 0 或启动失败记为 `failed`，超时记为 `timeout`；即使失败或超时也继续评分。`completed` 但评分不足 1 仍记为 `false_completion`。外部执行的 `steps` 为 0，`usage`（包括汇总）为 `null`。

每例目录保留完整的 `stdout.txt`、`stderr.txt`。结果 JSON 的 `external` 字段记录 argv、退出码（`returncode`）、执行耗时（`seconds`，不含评分）、是否超时（`timed_out`）、日志路径，以及各日志最多最后 3000 字节的 UTF-8 解码文本；汇总记录 `mode: external` 和原样的 `agent_command`。比较时应选择同一批用例，并记录各 Agent 使用的模型、版本和超时配置。

先后运行两个 Agent，再用 `eval-compare` 读取已有结果并排比较。以下示例在同一份 `evals/cases` 上选择同两个用例；运行前配置 harness 模型端点及 Codex，并记录万测王、Codex 和各自模型的名称与版本，以及超时配置。两次评测之间保持用例和隐藏评分器不变：

```sh
uv run prooflane eval --mode harness --cases evals/cases --case api-coupon --case script-logparser --output .prooflane/compare/harness
uv run prooflane eval --mode external --cases evals/cases --case api-coupon --case script-logparser --agent-command "codex exec --skip-git-repo-check -s workspace-write -" --output .prooflane/compare/codex
# 展开各输出目录下的结果；多次评测时可改用 details 中的具体路径
uv run prooflane eval-compare .prooflane/compare/harness/*/evaluation.json .prooflane/compare/codex/*/evaluation.json
uv run prooflane eval-compare .prooflane/compare/harness/*/evaluation.json .prooflane/compare/codex/*/evaluation.json --json
```

`eval-compare` 至少接收两个结果路径。默认表格按用例名排序、以 Agent 为列，分数保留三位小数，缺失用例显示 `-`，末行显示各次评测原有的均值。标签优先使用 `agent_command`，否则使用 `mode`，重名追加序号。`--json` 输出完整结构：`labels` 为标签列表，`cases` 中每项的 `runs` 按标签保存 `score` 与 `false_completion`（缺失时均为 `null`），`summary` 按标签保存原有的 `mean_score`、`false_completions` 和 `case_count`，`per_case_best` 按用例名列出所有并列最高分标签。缺失用例不参与最高分比较。对比只读取结果，不重新评分或检查评分器是否相同；用例集合不同时，均值仍来自各次评测自己的用例集合，不能直接据此排名。

## 当前限制

- 用例数量少，适合发现明显短板和比较改动前后的差异，不足以支撑统计意义上的能力结论。
- 浏览器用例需要运行 Prooflane 的解释器能导入 Playwright 并启动 Chromium；单元测试只在 `PROOFLANE_TEST_BROWSER=1` 时验证它的参考答案。
- 评分器运行的是 Agent 编写的代码，与 `process.run` 一样使用当前用户权限，不是沙箱。
- 已统计模型请求次数与 token，尚未换算费用。
- 缺陷复现评分只观察测试结果：只检查源码文本、不验证行为的测试也可能在缺陷版本上失败、在修复版本上通过。目前靠无关改动对照部分约束，尚未检测这类测试。
