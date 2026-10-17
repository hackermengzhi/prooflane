# 调度 Agent 与任意阶段切入

调度器接受目标、切入点、停止阶段和既有运行资料。每一步依据已完成阶段及真实观察选择工作；执行前由程序补齐依赖，阶段执行器负责断言和证据。`demo` 也通过同一调度器运行，不再维护另一条固定流水线。

## 当前支持的切入点

| 阶段 | 输入 | 产出 |
| --- | --- | --- |
| review | 编号需求文档 | 版本与行号、待澄清项 |
| design | 已审查需求 | 受控场景计划 |
| execute | 需求与计划 | 原版执行结果及证据 |
| investigate | 原版结果及证据 | 失败观察与证据引用 |
| regress | 计划、原版结果与调查记录 | 修复版的新执行与比较结果 |

没有前置材料时会从必要阶段开始补齐；材料足够时直接切入。显式切入点表示重做该阶段，并使依赖它的下游输出失效。只执行一个阶段时不会冒充完成整个闭环。

```sh
# 只审需求，不启动业务服务
uv run prooflane run '只做需求审查' --requirements src/prooflane/demo/requirements.md

# 从执行阶段进入；缺少计划时自动补齐
uv run prooflane run '执行测试' --entry execute --until execute --requirements src/prooflane/demo/requirements.md --browser-channel chrome

# 使用上一条命令输出目录替换 RUN_DIRECTORY
uv run prooflane run '调查失败原因' --entry investigate --from-run RUN_DIRECTORY
uv run prooflane run '修复后回归' --entry regress --from-run RUN_DIRECTORY --browser-channel chrome
```

指定 `--until` 可明确停止阶段，避免自然语言歧义。不能把切入点设在停止阶段之后。

## 两种调度方式

`--scheduler rules` 为默认离线调度器。它使用简单关键词识别目标并解析依赖，提供可重复验证的基线；不声称有模型推理能力。无法识别目标时要求显式指定终点，不擅自执行整套流程。

`--scheduler llm` 使用已显式配置的模型端点理解目标，并在每一步选择阶段、解释原因。它可以请求尚未就绪的阶段，运行时会解析缺失依赖后执行实际需要的工作；原请求和实际阶段都会记录。模型能看到已完成阶段、最近决策以及失败/阻塞观察，无权修改测试结果。模型不能提前完成、重复已完成阶段、选择目标范围外的阶段，或把明确的完整闭环缩减成需求审查。最多允许十二次阶段决策。

```sh
uv run prooflane run '检查这些验收标准，先停在用例设计' --scheduler llm --planner llm --until design --requirements src/prooflane/demo/requirements.md
```

`--scheduler` 控制调度推理，`--planner` 控制需求审查和测试计划的内容生成。二者分别配置；选择 LLM 后，缺少配置或模型失败会阻塞，不自动降级。当前模型路由使用本地契约桩验证，未测试真实供应商上的推理质量。

## 既有证据的复用

`--from-run` 创建有来源关联的新任务，不修改原运行。导入前检查所有文件的摘要、路径、需求版本、计划及结果一致性。当前执行器、被测业务或相关执行依赖变化时，旧执行失效；只保留仍适用的需求与计划。存在阻塞的原版证据不能证明完整闭环，需要重新执行。修复版回归无论旧资料中是否存在，始终新跑。

每次决策保存到 `dispatch.json` 和 SQLite 阶段事件；报告展示目标、切入点、停止阶段、选择原因及复用说明。文件摘要不是签名，不能证明资料来自可信第三方；只应导入自己确认来源的运行目录。

## 当前边界

支持的是五个已注册阶段与下单演示契约，而非任意软件的所有测试类型。专长执行器目前是确定性 Python 工具，不是各自独立运行的模型 Agent。调查生成真实失败观察，不冒充通用自动根因分析。未支持任意外部日志或测试脚本的直接导入、自动安装环境依赖、并行 Agent 协作以及强制崩溃后的进程续跑。
