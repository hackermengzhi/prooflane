# 订单运费规格

实现 `shipping.py` 中的 `shipping_fee(subtotal, discount=0)`，返回运费（整数分）。

- `subtotal` 和 `discount` 必须是非负整数，不接受布尔值；非法类型或数值均抛出 `ValueError`。
- 折扣不能超过订单原价，即 `discount <= subtotal`。
- 按折后金额 `subtotal - discount` 判断运费：零元订单运费为 0；
  大于 0 且不足 5000 分时收取 500 分；达到 5000 分免运费。
- 不修改输入，不访问文件或网络。

`self_test.py` 仅是简单自测，不代表完整验收。交付需要满足以上全部规格。
