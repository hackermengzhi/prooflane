# 长任务：跨会话记忆、续跑与不重复研究

一个测试或自动化任务常常超过单次会话的步数预算，也可能被 Ctrl-C、模型故障或进程崩溃打断。万测王用三层状态让任务持续推进，同时不把旧结论当成新证据：

| 层 | 内容 | 作用范围 | 失效方式 |
| --- | --- | --- | --- |
| 步骤历史 | 每步的动作、原因、目标和结果状态；完整观察可按编号取回 | 当前会话，续跑时继承最近部分 | 不失效，但只是线索 |
| 发现账本 | 事实、缺陷、已排除假设、环境知识、待验证假设、待办 | 同一工作区的所有任务 | 依赖文件摘要变化即标为过期；可被新笔记替换 |
| 任务事件 | 锁定的验收、测试角色文件归属、结束时的工作区指纹 | 一条续跑链 | 验收不可替换；文件被他人改过即释放归属 |

## 会话内：遮蔽旧观察，保留可取回的指针

模型每轮看到：

- `recent_steps`：最近 6 步（角色协作为 8 步）的完整记录，带步骤编号；
- `history`：更早步骤的大纲（动作、原因、工具、目标路径或命令、结果状态），最多 60 条，超出部分计数；
- `memory`：本工作区最新 40 条有效笔记，含是否过期、是否本任务所写；
- `history.recall(step)`：取回本会话任意一步的完整记录。

这对应 [The Complexity Trap](https://arxiv.org/abs/2508.21433) 的结论：只遮蔽旧观察、保留动作，效果与 LLM 摘要相当而成本约减半；也对应 [Memex](https://arxiv.org/abs/2603.04257) 的做法：上下文里放精简摘要，完整证据留在外部并可按指针取回。大纲是确定性生成的，不额外调用模型，也不会在摘要时丢掉或编造细节。

## 跨会话：发现账本

角色（或通用 Agent）用 `notes.record` 记录值得保留的发现，用 `notes.search` 检索更早的笔记：

| 类型 | 含义 | 是否必须引用步骤 |
| --- | --- | --- |
| `fact` | 观察到的项目事实 | 是 |
| `defect` | 已确认的产品缺陷 | 是 |
| `ruled_out` | 已被观察否定的假设 | 是 |
| `environment` | 如何安装、启动、运行 | 是 |
| `hypothesis` | 尚未验证的猜测 | 否 |
| `todo` | 剩余工作 | 否 |

引用必须指向本会话中一次真实的工具观察或验收步骤，不能引用笔记本身，记录时保存该步的工具与结果状态。笔记可以列出最多 5 个依赖文件并保存其摘要；之后文件变化，笔记显示为 `stale`，需要重新确认。`replaces` 用于纠正或完成旧笔记，旧笔记随之退出有效列表。

账本按工作区绝对路径隔离，保存在 `.prooflane/state.db`，不在工作区里，被测项目的文件无法伪造笔记。评测每次把用例复制到新目录，因此不同评测运行不会共享账本。笔记和取回不改变工作区，所以不会使当前验收结论失效；它们也不能充当验收检查。

这借鉴了 Anthropic 关于长任务 harness 与上下文工程的结构化笔记做法：状态写在上下文之外，下一次会话读回。不同的是，万测王要求观察类笔记附带证据引用，并在依赖文件变化时标记过期。

## 续跑与多会话推进

```sh
# 接着做一个 blocked、cancelled 或主进程已退出的任务
uv run prooflane harness --resume TASK_ID --workspace YOUR_PROJECT --allow-process
# 每个会话 30 步，步数用完就自动续跑，最多 5 个会话
uv run prooflane harness '为 service.py 编写接口测试并报告缺陷' --workspace YOUR_PROJECT --allow-process --sessions 5
```

续跑新建任务并记录来源，原任务不变。新会话从 SQLite 事件重建：原目标、锁定的验收、测试角色的文件归属、上一会话的错误原因、最近步骤与更早步骤的大纲，以及上次结束后工作区新增、修改、删除了哪些文件。旧会话的验收结论一律作废，交付前必须在当前状态下重新验收。

`--sessions` 只在会话因步数用完而阻塞时继续；模型阻塞、配置缺失、验收失败等原因会停下来交给用户。

主进程被强制结束后，任务停在 active。续跑会先核对原进程：同一主机上记录的进程号已不存在，才把旧任务标为 blocked 并续跑；进程号仍存在（可能被复用）或主机不同则拒绝，宁可不续跑，也不让两个会话同时改一个工作区。崩溃的会话没有结束时的工作区指纹，此时工作区变化显示为未知。

## 衡量效果

`prooflane eval --sessions K` 把每个用例的步数预算平均拆成 K 个会话，步数用完时自动续跑，结果记录实际会话数和总步数。和 `--sessions 1` 用同一评分器比较，得分差就是每次续跑损失的能力，虚假完成数应保持不变。目前只用契约桩验证了机制，尚未取得真实模型上的对比数据。

## 尚未实现

- 主进程崩溃后 `process.run` 留下的孤儿进程组不会自动回收：进程组号可能被复用，自动结束有误杀风险。
- 环境配方（安装、启动命令）只能以 `environment` 笔记记录，续跑不会自动重放有副作用的准备步骤。
- 历史大纲由规则生成，没有按子任务折叠（如 [Context-Folding](https://arxiv.org/abs/2510.11967)），也没有在上下文接近上限时由模型压缩。
- 笔记检索是全词子串匹配，不是语义检索。
- 跨会话证据仍保存在各自的运行目录，`history.recall` 只能取回本会话的步骤。

## 参考

- [The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management](https://arxiv.org/abs/2508.21433), 2025.
- [Memex(RL)](https://arxiv.org/abs/2603.04257), 2026：精简上下文摘要加完整外部档案的指针。
- [Scaling Long-Horizon LLM Agent via Context-Folding](https://arxiv.org/abs/2510.11967), 2025.
- [ReSum: Unlocking Long-Horizon Search Intelligence via Context Summarization](https://arxiv.org/abs/2509.13313), 2025.
- [MEM1: Learning to Synergize Memory and Reasoning for Efficient Long-Horizon Agents](https://arxiv.org/abs/2506.15841), 2025.
- [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)：检查点续跑，副作用不重放。
- [OpenHands SDK conversation persistence](https://docs.openhands.dev/sdk/guides/convo-persistence)：会话事件日志与续跑前的兼容性校验。
