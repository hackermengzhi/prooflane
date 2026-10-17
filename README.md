# Prooflane

需求驱动、证据可追溯的全流程测试 Agent 平台。

**需求 → 验收场景 → 执行证据 → 缺陷记录 → 修复版本 → 回归结果**

## 当前可运行的闭环

第一条业务链为本地下单支付演示：导入七条需求、生成十个受控场景，启动原版服务，执行接口和浏览器测试，关联请求、日志与数据库证据，再启动预先提供的修复版，以同一计划回归，输出 HTML 和 JSON 报告。

真实运行预期：原版 **8 通过、2 失败**；修复版 **10 通过**。两个失败场景指向同一个预设支付状态缺陷。这证明演示闭环能够发现并验证该缺陷，不代表通用自主测试平台已经完成。

## 快速开始

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。在仓库根目录执行：

```sh
uv sync --extra browser --group dev
uv run playwright install chromium --only-shell
uv run prooflane demo
```

已安装 Chrome 时可以省去浏览器下载，直接运行：

```sh
uv run prooflane demo --browser-channel chrome
```

命令输出本次报告的绝对路径。用浏览器打开 `report.html`，查看需求来源、待澄清项、原版与修复版结果、截图和证据链接。每次执行使用独立输出目录，自动关闭自己启动的服务与浏览器。

- 数据库：`.prooflane/state.db`，保存任务状态、阶段事件和报告引用。
- 制品：`.prooflane/runs/<task-id>/`，含需求快照、计划、请求、截图、数据库快照、服务日志、源码摘要及报告。
- 退出码：完整闭环成功为 `0`；失败或阻塞为 `2`；用户中断为 `130`。
- `--no-browser` 只运行接口部分，浏览器记为阻塞，不能宣告完整闭环成功。
- `--requirements PATH` 支持指定需求文件，但当前执行器仅接受已审查的演示业务条目；业务条目变化会阻塞，防止使用旧断言给新需求背书。

这些本地运行资料和凭据不会提交到仓库。演示服务只监听回环地址；使用一次性数据库和演示账户，没有真实交易。它不是可部署的订单或支付系统。

## 调度 Agent：按目标从不同阶段切入

使用 `prooflane run` 提交目标。支持 review、design、execute、investigate、regress 五个阶段，`--entry` 指定切入点，`--until` 指定停止阶段，`--from-run` 导入并校验已有资料。缺少前置条件时先补齐；已有有效证据可复用；指定重做的阶段会使下游结果失效。只审需求时不会启动被测服务，回归始终新跑修复版。

```sh
uv run prooflane run '只做需求审查' --requirements src/prooflane/demo/requirements.md
```

默认 `--scheduler rules` 为可复现的离线依赖调度；`--scheduler llm` 可用模型理解目标、选择下一阶段并说明原因。`--planner llm` 另行启用模型需求审查与计划生成。真实模型的推理质量尚未验收；模型不能越过前置条件或自行宣布测试成功。

详见 [调度方式、阶段切入与证据复用](docs/dispatcher.md)。

## 模型规划（可选）

默认 `--planner fixture` 使用明确标注的离线样例计划和人工编写的审查问题，不调用模型。

使用 `--planner llm` 时，显式配置 `.env.example` 所列的三个环境变量：完整 Chat Completions 端点、模型名及可选 API key。程序不会自动读取 `.env` 或扫描本机其他配置；此模式会把需求内容发往你配置的端点。

```sh
uv run prooflane demo --planner llm --browser-channel chrome
```

模型输出 JSON 测试计划与需求审查问题。模型只能选择现有受控场景，不能提交任意代码、删掉必测场景或改变断言。输出经过本地校验；缺配置、拒绝、截断、网络故障或无效计划都会阻塞，不会悄悄切换为离线结果。

目前模型适配器已通过本地 HTTP 契约桩测试，尚未验证真实模型供应商。当前采用 JSON mode，并在本地验证业务约束；不声称供应商保证 schema。接口参考：[官方结构化输出说明](https://developers.openai.com/api/docs/guides/structured-outputs)。浏览器实现参考：[Playwright Library](https://playwright.dev/python/docs/library) 与 [Network](https://playwright.dev/python/docs/network)。

## 开发验证

```sh
uv run ruff check .
uv run ruff format --check .
uv run python -m unittest discover -s tests -v
```

完整浏览器集成测试：

```sh
PROOFLANE_TEST_BROWSER=1 PROOFLANE_BROWSER_CHANNEL=chrome uv run python -m unittest discover -s tests -v
```

使用已下载 Chromium 时省略 `PROOFLANE_BROWSER_CHANNEL`。CI 覆盖 Python 3.11–3.13 的核心检查，并单独运行 Chromium 闭环。

## 范围与后续

已经实现：需求编号及原文行号、版本快照、受控计划、HTTP 执行、浏览器操作和请求采集、SQLite 状态验证、日志关联、任务状态机、正常中断清理、回归比较、可检查报告和文件摘要。

尚未实现：任意需求的自主用例生成、独立模型专长 Agent 的并行协作、RAG、OCR、通用代码影响分析、分布式追踪、崩溃续跑、自动代码修复及可视化工作台。当前的全链路限于本地页面、HTTP、单服务数据库与日志。修复代码是演示预先提供的版本。

- [编码原则与开发约定](AGENTS.md)
- [架构](docs/architecture.md)
- [阶段计划](docs/roadmap.md)
- [演示说明](examples/checkout/README.md)
- [需求源文件](src/prooflane/demo/requirements.md)

项目尚未选择开源许可证；公开与许可证选择另行确定。
