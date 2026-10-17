# 最后一页刚好满页时，客户端还会请求一次空页

我们用 `pager.paginate` 分批读取一份固定的整数 ID 列表。客户端根据
`Page.next_cursor` 决定是否继续请求；约定没有后续记录时这个值为 `None`
（接口 JSON 中的 `null`）。

最近发现：当记录总数恰好是页大小的整数倍时，最后一页已经取完了所有记录，
却仍返回非空游标。客户端拿着这个游标继续请求，会多读到一个空页。
如果最后一页没有填满，暂时没有观察到这个问题。

例如，固定数据为 `[1, 2, 3, 4, 5, 6]`，`page_size=3`，使用默认排序：

```python
from pager import paginate

records = [1, 2, 3, 4, 5, 6]
first = paginate(records, page_size=3)
last = paginate(records, page_size=3, cursor=first.next_cursor)
print(last.items, last.next_cursor)
```

第二次调用返回的记录是 `[4, 5, 6]`，此时游标应当为 `None`，实际却不是。
请为这个现象编写可重复运行的 unittest 测试，让它在当前版本上失败、修复后通过。
本报告只涉及正常页大小、默认排序和库自己返回的有效游标，不涉及其它参数的规则。
