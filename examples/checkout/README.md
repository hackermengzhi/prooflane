# 下单支付演示

唯一的需求源文件位于 [`src/prooflane/demo/requirements.md`](../../src/prooflane/demo/requirements.md)，随安装包提供。

在仓库根目录运行：

```sh
uv sync --extra browser --group dev
uv run playwright install chromium --only-shell
uv run prooflane demo
```

如果已安装 Google Chrome，可以省去浏览器下载：

```sh
uv run prooflane demo --browser-channel chrome
```

演示会启动原版服务并执行测试，记录失败，再启动预先提供的修复版，在独立数据库上执行同一计划。每次运行使用独立目录，退出时关闭所启动的服务和浏览器。

预期结果：原版 8 通过、2 失败；修复版 10 通过。两个失败场景对应同一个预设缺陷，不能据此声称发现两个独立缺陷。

CLI 会输出本次报告位置。报告、截图、请求、日志、数据库快照和文件摘要均留在本地 `.prooflane/runs/<task-id>/`，不提交到 Git。
