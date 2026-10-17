# 制作农场自动化脚本

这个非支付样例验证通用 Agent 工具可以制作代码并运行独立验收。业务逻辑只存在于样例需求、测试与配方中，通用 Agent 内核没有农场场景名称或固定断言。

在仓库根目录运行：

```sh
uv run prooflane agent '制作农场收获自动化并验证重复运行' --workspace examples/farm-automation --recipe examples/farm-automation/recipe.json --allow-process
```

离线配方包含预写的脚本模板，运行时实际写入 automation.py、检查语法并启动验收程序。它用于复现工具执行，不代表模型现场创作了代码。

显式配置模型端点后，去掉 --recipe 即可让模型使用同样的工具自主读取需求、提出验收、编写及修改脚本：

```sh
uv run prooflane agent '读取 requirements.md，制作农场收获自动化，使用现有 test_acceptance.py 验证，不修改验收文件' --workspace examples/farm-automation --allow-process
```

验收覆盖成熟地块选择、未成熟地块不变、重复运行和接口失败。测试只连接自己启动的回环服务。没有真实桃源账号、协议数据、交易或在线操作。

实际桃源项目的协议客户端、状态快照、种植决策和循环管理可作为后续适配对象；当前样例不宣称已兼容真实游戏协议。
