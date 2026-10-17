# 农场自动化验收

目标：制作一个通过 HTTP 接口收获成熟地块的命令行脚本。

1. 脚本名为 automation.py，接受 --base-url 参数。
2. GET /plots 返回 {"plots": [{"id": "p1", "state": "ready"}]}。
3. 只处理 ready 地块；growing 和 empty 地块保持不变。
4. POST /harvest 发送 plot_id 和稳定的 idempotency_key，成功响应包含 harvested=true。
5. 重复运行不得重复收获；标准输出为 JSON，包含本次 harvested 地块列表。
6. 发生接口错误时退出码非零，不得把失败报告成成功。

这里是可独立运行的本地农场接口样例，不是真实桃源协议。test_acceptance.py 启动本地服务，并用独立断言验证脚本行为。
