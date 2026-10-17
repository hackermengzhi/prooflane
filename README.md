<p align="center">
  <img src="docs/assets/mascot.svg" width="200" alt="万测王吉祥物：举着放大镜的小猫头鹰鸮鸮">
</p>

<h1 align="center">万测王 · Prooflane</h1>

<p align="center"><strong>会写测试、会写脚本，而且每句结论都拿得出证据的 Agent。</strong></p>

<p align="center">需求 → 验收 → 执行证据 → 缺陷 → 修复 → 回归</p>

---

万测王是一个面向**软件测试**和**自动化脚本制作**的 Agent。它读需求、设计用例、写测试和脚本、真实运行、分辨失败原因，最后交出一份能逐条核对的报告。

它的信条只有一句：**没有执行证据，就不算完成。** Agent 不能自己宣布成功，必须由独立的验收实际跑过；评测时，连分数都由它看不到的评分器给出。

吉祥物是一只叫**鸮鸮**的小猫头鹰。猫头鹰夜里也看得清，眼睛最尖，鸮鸮拿着放大镜盯着每一个细节，替发布把关。

## 它能做什么

| 命令 | 做什么 | 是否需要模型 |
| --- | --- | --- |
| `prooflane harness` | 领导、产品、开发、测试四个角色协作完成测试或自动化目标 | 需要 |
| `prooflane agent` | 单个 Agent 用文件、代码、进程、HTTP、浏览器工具完成任务 | 需要（或用离线配方） |
| `prooflane eval` | 在埋了缺陷的用例上实测 Agent，由隐藏评分器打分 | 需要（`--reference` 不需要） |
| `prooflane demo` | 下单支付演示：从需求到回归的完整测试闭环 | 不需要 |
| `prooflane run` | 按目标从需求审查、设计、执行、排查、回归任一阶段切入 | 可选 |
| `prooflane models` | 列出已配置模型服务提供的模型 | 需要配置 |

## 快速开始

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```sh
uv sync --extra browser --group dev
uv run playwright install chromium --only-shell

uv run prooflane demo              # 离线跑完整测试闭环，输出 HTML 报告
uv run prooflane eval --reference  # 离线验证评测集和评分器
```

已安装 Chrome 时可用 `uv run prooflane demo --browser-channel chrome` 省去浏览器下载。

## 接入模型

万测王只读取你**明确指定**的配置，不会自动扫描 `.env` 或本机其他配置。支持 OpenAI 风格的 **Responses** 和 **Chat Completions** 两种接口，兼容各类中转服务。

**方式一：配置文件**（推荐，格式与 Codex 的模型配置相近）

```toml
# my-model.toml
model = "gpt-5.5"
model_provider = "relay"
reasoning_effort = "medium"      # 可选

[model_providers.relay]
base_url = "https://api.example.com/v1"
wire_api = "responses"           # 或 "chat"
env_key = "RELAY_API_KEY"        # 存放密钥的环境变量名
timeout = 180                    # 可选，单次请求秒数
max_retries = 4                  # 可选，429/5xx/超时自动退避重试
```

```sh
export PROOFLANE_CONFIG=my-model.toml RELAY_API_KEY=...
uv run prooflane models
```

密钥只放在环境变量里；配置文件中出现 `api_key` 会被直接拒绝。完整示例见 [examples/model-config.toml](examples/model-config.toml)。

**方式二：环境变量**

```sh
export PROOFLANE_LLM_URL=https://api.example.com/v1/responses   # 或 .../chat/completions
export PROOFLANE_LLM_MODEL=gpt-5.5
export PROOFLANE_LLM_API_KEY=...
```

模型输出截断、拒答、返回非 JSON，或者重试次数用完，都会让任务阻塞并留下记录，绝不会悄悄换成离线结果。

## 角色协作 Harness

```sh
uv run prooflane harness '根据 requirements.md 为 service.py 编写接口测试并报告缺陷' \
  --workspace YOUR_PROJECT --allow-process
```

| 角色 | 职责 | 工具 |
| --- | --- | --- |
| 领导 | 拆解目标、委派、查收；不能自己宣布完成 | 无执行工具 |
| 产品 | 逐条梳理编号需求，按交付物制定一次性验收 | 读文件、代码结构 |
| 开发 | 实现脚本或修复，逐条落实退出码、分页、重试等细节 | 读写文件、代码结构、进程 |
| 测试 | 按等价类、边界值、状态与错误路径设计用例；失败先复核再定性 | 新建测试与报告文件、进程、HTTP、浏览器、验收 |

测试角色只能新建文件或修改自己创建的文件，不能改被测代码。领导只有在测试角色对当前状态跑过验收、全部通过之后才能交付。详见 [Harness 分工与边界](docs/harness.md)。

## 用评测集衡量聪明程度

“聪明”要能量出来。`prooflane eval` 让 Agent 在三个埋了缺陷的用例上实际工作，结束后才运行评分器：

| 用例 | 场景 | 评分方式 |
| --- | --- | --- |
| `api-coupon` | 购物车 HTTP 接口，埋了 3 个缺陷 | 拿 Agent 的测试去跑 10 个变异版本 + 核对缺陷报告 |
| `web-signup` | 注册页面，埋了 2 个缺陷 | 同上，用浏览器测试 |
| `script-inventory` | 库存同步脚本 | 7 条隐藏验收测试 |

- 测试必须在正确实现上通过，否则视为误报，不得分。
- Agent 宣布完成、评分却不满分，记为一次**虚假完成**，这是最需要压低的指标。
- 评分在工作区副本上进行，Agent 改了被测代码也影响不了分数。

**当前分数**：尚未取得真实模型的成绩。所有用例的参考答案都能拿满分，空交付得 0 分。详见 [评测集说明](docs/evaluation.md)。

## 下单支付演示

`prooflane demo` 导入七条需求、生成十个受控场景，启动原版服务执行接口和浏览器测试，关联请求、日志与数据库证据，再对预先提供的修复版用同一计划回归，最后输出 HTML 和 JSON 报告。预期结果：原版 **8 通过、2 失败**，修复版 **10 通过**，两个失败指向同一个预设支付缺陷。

- 数据：`.prooflane/state.db` 保存任务状态和事件；`.prooflane/runs/<task-id>/` 保存需求快照、计划、请求、截图、日志和报告。
- 退出码：成功 `0`，失败或阻塞 `2`，用户中断 `130`。
- `--no-browser` 只跑接口部分，浏览器部分记为阻塞，不会宣告闭环成功。

演示服务只监听本机地址，使用一次性数据库，没有真实交易。按阶段切入的用法见 [调度说明](docs/dispatcher.md)，离线的非支付样例见 [农场自动化](examples/farm-automation/README.md)。

## 安全边界

- 文件工具限定在工作区内；HTTP 和浏览器只能访问 `--allow-origin` 明确允许的地址。
- `process.run` 需要 `--allow-process`，以当前用户权限运行，**不是沙箱**。
- `quality.mutate` 需要 `--allow-process`，在临时副本上先跑基线，再检查最多 30 个自拟变异体，返回杀死率与存活列表。
- 角色权限是应用层约束，不是操作系统隔离。
- 外部文档、网页、日志、代码一律当作待分析的数据，不当作指令。
- 报告和工具输出可能包含项目数据，默认只保存在本地。

## 已实现与规划

**已实现**：角色协作 harness、通用 Agent 工具循环、隐藏评分评测集、Responses/Chat 双接口模型接入（含重试和模型列表）、下单支付完整闭环、按阶段调度和证据复用。所有涉及模型的流程目前都用契约桩配合真实工具做过测试。

**尚未实现**：真实模型的评测成绩、执行沙箱、崩溃续跑、RAG、OCR、桌面控制、多 Agent 并行、Web 工作台。路线图见 [docs/roadmap.md](docs/roadmap.md)，架构见 [docs/architecture.md](docs/architecture.md)。

## 参与开发

```sh
uv run ruff check .
uv run ruff format --check .
uv run python -m unittest discover -s tests -v
# 浏览器集成测试
PROOFLANE_TEST_BROWSER=1 uv run python -m unittest discover -s tests -v
```

开发约定见 [AGENTS.md](AGENTS.md)。项目尚未选择开源许可证。


调试扩展：开发与测试角色可调用 LLDB、GDB、Frida 适配器，以及 Cheat Engine Lua 导出工具。各平台安装和实际验证范围见 [调试工具说明](docs/debugging.md)。Cheat Engine 当前是脚本导出，不是已完成的实时控制桥接。
