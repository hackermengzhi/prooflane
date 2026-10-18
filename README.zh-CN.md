<p align="center">
  <img src="docs/assets/mascot.svg" width="200" alt="万测王吉祥物：举着放大镜的小猫头鹰鸮鸮">
</p>

<h1 align="center">万测王 · Prooflane</h1>

<p align="center"><strong>会写测试、会写脚本，而且每句结论都拿得出证据的 Agent。</strong></p>

<p align="center">需求 → 验收 → 执行证据 → 缺陷 → 修复 → 回归</p>

<p align="center">
  <a href="README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <a href="https://github.com/hackermengzhi/prooflane/stargazers"><img src="https://img.shields.io/github/stars/hackermengzhi/prooflane?style=flat" alt="GitHub stars"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/hackermengzhi/prooflane" alt="License"></a>
</p>

---

万测王把需求、代码和测试现场连成一条工作流。交给它一个目标，由领导、产品、开发、测试四个角色协作，完成分析、编写、执行与验证，交付代码、测试和可追溯的报告。

吉祥物 **鸮鸮** 是一只举着放大镜的小猫头鹰，替每次交付盯紧细节。

## 把这些工作交给万测王

- **从需求到测试**：梳理验收标准，设计边界、状态和异常场景，编写并运行测试。
- **从目标到自动化**：读取项目，制作脚本，处理分页、重试、超时与重复执行。
- **从失败到复现**：关联请求、日志和执行结果，记录缺陷，验证修复与回归。
- **从一次通过到稳定可靠**：使用变异测试检查用例质量，重复运行套件识别不稳定测试，保存环境快照。
- **从接口到浏览器与调试器**：组合文件、代码、进程、HTTP、浏览器及调试工具完成任务。
- **从一次会话到长任务**：步数用完自动续跑，中断后用 `--resume` 接着做；发现记入按工作区共享、引用观察证据的笔记，不重复研究。详见 [长任务说明](docs/long-tasks.md)。

## 快速开始

准备 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)，克隆仓库：

```sh
git clone https://github.com/hackermengzhi/prooflane.git
cd prooflane
uv sync --extra browser --group dev
uv run playwright install chromium --only-shell
```

先体验无需模型的完整业务演示：

```sh
uv run prooflane demo
```

它会执行接口与浏览器测试，对比缺陷版和修复版，输出可打开的 HTML 报告。已安装 Chrome 时可使用 `--browser-channel chrome`。

## 接入模型，开始第一个任务

支持 Responses 与 Chat Completions 接口。通过环境变量配置模型服务：

```sh
export PROOFLANE_LLM_URL=https://api.example.com/v1/responses
export PROOFLANE_LLM_MODEL=your-model-id
export PROOFLANE_LLM_API_KEY=your-api-key

uv run prooflane harness '根据 requirements.md 为 service.py 编写接口测试并报告缺陷' \
  --workspace YOUR_PROJECT --allow-process
```

`YOUR_PROJECT` 替换为项目目录；`--allow-process` 启用本机程序执行。Chat Completions 服务将地址改为完整的 `/chat/completions` 端点。

也可使用 [TOML 配置文件](examples/model-config.toml)，设置 `PROOFLANE_CONFIG` 指向它。配置支持模型选择、推理强度、请求超时及退避重试。

## 一支围绕目标工作的团队

| 角色 | 负责交付 |
| --- | --- |
| 领导 | 拆解目标、分派任务、协调反馈、汇总结果 |
| 产品 | 需求清单、业务澄清与验收标准 |
| 开发 | 功能实现、自动化脚本与问题修复 |
| 测试 | 测试设计、执行证据、缺陷报告与回归验证 |

角色按任务往返协作：测试发现问题，交回开发处理；需求不清楚，交给产品澄清。执行记录、工具观察和验收结果随任务保存，方便查收与复盘。

[了解角色协作](docs/harness.md) · [查看架构](docs/architecture.md)

## 选择适合的入口

| 命令 | 使用场景 |
| --- | --- |
| `prooflane harness` | 让四个角色协作完成目标 |
| `prooflane agent` | 单 Agent 完成代码、测试或自动化任务 |
| `prooflane run` | 从需求审查、设计、执行、调查或回归阶段切入 |
| `prooflane demo` | 体验下单支付的完整测试闭环 |
| `prooflane eval` | 用隐藏评分器评估 Agent 的实际交付 |
| `prooflane eval-compare` | 并排比较不同 Agent 的评测结果 |
| `prooflane models` | 查看模型服务提供的模型 |

## 先试一个自动化项目

农场样例提供离线配方，制作收获脚本并验证成熟地块选择、重复执行和错误处理：

```sh
uv run prooflane agent '制作农场收获自动化并验证重复运行' \
  --workspace examples/farm-automation \
  --recipe examples/farm-automation/recipe.json --allow-process
```

[农场自动化教程](examples/farm-automation/README.md) · [阶段调度](docs/dispatcher.md) · [LLDB、GDB、Frida 与 Cheat Engine Lua 工具](docs/debugging.md)

## 用实际交付衡量能力

评测覆盖接口测试、浏览器测试、库存同步、日志分析、会话管理、旧代码特征测试和缺陷复现。通过隐藏验收、变异评分和缺陷报告评分，比较不同模型、Agent 及版本的表现。

```sh
uv run prooflane eval --reference  # 校验参考答案与评分流程
uv run prooflane eval             # 运行配置的模型与角色团队
```

结果保存在 `.prooflane/eval-runs/`。业务演示报告保存在 `.prooflane/runs/`，通用任务及角色团队报告分别保存在 `.prooflane/agent-runs/` 和 `.prooflane/harness-runs/`。

[评测与对比指南](docs/evaluation.md) · [后续路线图](docs/roadmap.md)

## 参与开发

```sh
uv run ruff check .
uv run ruff format --check .
uv run python -m unittest discover -s tests -v
PROOFLANE_TEST_BROWSER=1 uv run python -m unittest discover -s tests -v
```

开发遵循 [八项代码质量原则](AGENTS.md)。

## Star 趋势

<a href="https://www.star-history.com/#hackermengzhi/prooflane&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date&theme=dark">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date">
    <img alt="Star 趋势图" src="https://api.star-history.com/svg?repos=hackermengzhi/prooflane&type=Date">
  </picture>
</a>

## 许可证

[Apache License 2.0](LICENSE) · Copyright Cherry · [NOTICE](NOTICE)
