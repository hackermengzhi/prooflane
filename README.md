# Prooflane

需求驱动、证据可追溯的全流程测试 Agent 平台。

从需求文档审查、测试设计、接口与集成测试，到端到端、全链路验证和变更回归，围绕同一条证据链协作：

**需求 → 验收标准 → 测试用例 → 执行证据 → 缺陷 → 修复 → 回归结果**

## 当前状态

项目初始化阶段。已实现基于 SQLite 的本地任务与证据登记 CLI；尚未接入模型、浏览器、网络采集、OCR 或测试执行器。下面的平台能力均为设计目标，不能视为现成功能。

## 运行骨架

需要 Python 3.11+，当前无需安装第三方依赖。在仓库根目录运行：

```sh
PYTHONPATH=src python3 -m prooflane init
PYTHONPATH=src python3 -m prooflane create '验证下单到支付流程'
PYTHONPATH=src python3 -m prooflane list
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

默认数据库保存在 `.prooflane/state.db`，不会提交到仓库。所有命令支持 `--db PATH`。

## 平台范围

| 阶段 | 目标 |
| --- | --- |
| 需求审查 | 发现歧义、矛盾、遗漏，形成可验证的验收标准 |
| 测试设计 | 正常、边界、异常、权限、状态流转与测试数据 |
| 接口与集成 | 接口契约、服务协作、数据一致性、依赖故障 |
| 端到端 | 浏览器或客户端的真实业务流程 |
| 全链路 | 关联请求、日志、追踪、消息与数据库证据 |
| 回归 | 需求和代码变更分析、用例选择、结果比较 |

## 设计与计划

- [编码原则与开发约定](AGENTS.md)
- [架构](docs/architecture.md)
- [阶段计划](docs/roadmap.md)
- [演示业务与验收标准](examples/checkout/requirements.md)

项目尚未选择开源许可证；公开与许可证选择另行确定。
