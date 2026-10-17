# 同类项目调研与取舍（2026-10）

只记录对万测王有直接借鉴意义的做法。星数为 2026-10-18 查询值，仅说明关注度，不代表效果。

| 项目 | 做法 | 借鉴 | 不照搬 |
| --- | --- | --- | --- |
| [qodo-cover](https://github.com/qodo-ai/qodo-cover)（5.6k，2025-06 起停止维护） | 生成测试 → 运行 → 只保留能通过且提高覆盖率的测试，迭代到目标覆盖率 | “生成的测试必须先在当前代码上通过”与我们的参考门槛一致；可加入不稳定用例识别（它只列在路线图里） | 以覆盖率为唯一标准：覆盖率高不等于能抓缺陷 |
| [Mutahunter](https://github.com/codeintegrity-ai/mutahunter)（303） | 用 LLM 生成贴近真实缺陷的变异体，报告存活/杀死/编译失败与费用 | 给 Agent 一个**自检变异工具**：工作中自己生成变异体，看哪些存活，再补测试 | 把变异分当最终结论；我们的最终分只来自 Agent 看不到的评分器 |
| [Keploy](https://github.com/keploy/keploy)（18.6k） | 录制真实流量生成接口测试和依赖模拟，冻结时间保证回放确定 | 后续做“录制 → 回归”工具；时钟注入/冻结作为确定性约定 | eBPF 级抓包，超出当前范围 |
| [SWT-Bench](https://github.com/logic-star-ai/swt-bench)（NeurIPS 2024） | 给出问题描述，要求测试在修复前失败、修复后通过（fail-to-pass） | 新增评测类型 `reproduce`：直接衡量“复现缺陷”的能力 | 绑定真实大仓库，环境成本高；先用自带小项目 |
| [TestGenEval](https://github.com/facebookresearch/testgeneval)（ICLR 2025） | 真实仓库上的测试生成，同时报告覆盖率与变异分 | 变异分作为主指标的依据 | — |
| [ida-pro-mcp](https://github.com/mrexodia/ida-pro-mcp)（13k）/ [GhidraMCP](https://github.com/LaurieWired/GhidraMCP)（11k） | 把反汇编器能力以工具形式交给模型 | 不让模型自己算数（进制转换、偏移用工具）；要求写结论与步骤报告；先读代码再信注释 | 依赖商业或重型反汇编器；我们先做标准库可实现的制品检查 |
| [browser-use](https://github.com/browser-use/browser-use)（118k）/ [playwright-mcp](https://github.com/microsoft/playwright-mcp)（38k） | 基于可访问性树而非截图驱动浏览器 | 浏览器工具返回可访问性快照，减少坐标猜测 | 云端隐身浏览器等与测试无关的能力 |

## 观察

- 没有找到用**统一的隐藏评分器**横向比较测试 Agent 的公开排行榜。现有项目大多自报覆盖率或成功率，很少给变异分或虚假完成率。万测王的差异化正在这里：分数来自 Agent 看不到的评分器，并单独统计虚假完成。
- 研究指出覆盖率、变异分与真实检出能力的相关性有限，编码 Agent 也倾向于写过度模拟的测试（[arXiv 2607.22880](https://arxiv.org/pdf/2607.22880)、[arXiv 2602.00409](https://arxiv.org/pdf/2602.00409)）。评分应继续以“抓住埋入缺陷 + 报告准确”为主。

## 由此排定的工作

1. Agent 自检变异工具（借鉴 Mutahunter），评分时仍以隐藏变异体为准。
2. `reproduce` 评测类型（借鉴 SWT-Bench 的 fail-to-pass）。
3. 不稳定用例识别：同一套测试重复运行，结果不一致则标记（补 qodo-cover 未完成的部分）。
4. 基线对比：同一评测集上跑原生 codex 与万测王，用同一评分器出分，作为“领先”与否的唯一依据。
5. 制品工具扩展：按偏移读取字节、字节/正则搜索、进制换算，避免模型心算。
