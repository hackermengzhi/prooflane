# 调试工具适配

开发、测试角色可调用 `debug.capabilities` 和 `debug.run`，领导只委派。运行调试需要显式启用 `--allow-process`。命令脚本必须位于任务工作区；目标使用工作区程序路径或显式本地 PID。调试器和脚本以当前用户权限运行，不是沙箱。

| 后端 | 接入方式 | 本轮验证 |
| --- | --- | --- |
| LLDB | 原生命令文件、加载程序或附加 PID | 本机真实加载程序并执行 target list 通过；未宣称所有进程均可附加 |
| GDB | 原生命令文件、程序或 PID，禁用初始化和自动加载 | 本机未安装，执行时明确阻塞 |
| Frida | 本地 PID、工作区 JavaScript、独立会话、有限观察时长和清理 | 可选 Python 依赖，尚未实测进程附加 |
| Cheat Engine | 导出工作区 Lua 脚本及摘要 | 导出流程测试通过；未自动控制 GUI 或建立实时桥接，导出结果为 blocked/not_executed |

安装 Frida Python 依赖：`uv sync --extra debug`。命令行 Frida 已安装不代表当前 Python 环境具备该模块。LLDB/GDB 需系统安装；Cheat Engine 需另行安装并在其 Lua 控制台执行导出脚本。系统拒绝附加时保留实际错误，不尝试绕过保护。

工具调用示例（JSON 参数）：

```json
{"backend":"lldb","target":"target","script":"inspect.lldb","timeout":15}
```

Frida 参数示例：`{"backend":"frida","pid":1234,"script":"modules.js","seconds":2,"timeout":15}`。PID 必须替换成自己的授权测试进程。`examples/debugging/` 提供自有 C 测试程序、断点与变量观察命令、Frida 模块枚举脚本和 CE Lua 控制台样例。

LLDB/GDB 原生命令可设置断点、查看栈和内存；Frida 脚本可使用其动态插桩接口。当前不提供自动值扫描、指针链搜索、持续修改或跨平台统一内存数据模型；具体操作取决于原生脚本和操作系统权限。工具退出码、输出和脚本摘要进入证据目录，调试调用会使已有验收结论失效。

参考：[LLDB CLI](https://lldb.llvm.org/man/lldb.html)、[GDB 手册](https://sourceware.org/gdb/current/onlinedocs/gdb)、[Frida JavaScript API](https://frida.re/docs/javascript-api/)、[Cheat Engine 脚本接口](https://cheatengine.org/help/Scriptengine.htm)。
