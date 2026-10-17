# 购物车接口需求

`service.py` 提供 `create_server(host, port)`，返回尚未启动的 `ThreadingHTTPServer`；每个服务实例拥有独立的购物车。金额单位均为分。商品目录：BOOK 4500、PEN 500、MUG 900、CLIP 15、LAMP 12000。

- **R1 加购**：`POST /cart/items`，请求体 `{"sku": "BOOK", "qty": 2}`。`qty` 必须是 1～99 的整数，否则返回 400；同一商品累计数量不得超过 99，超出返回 400 且购物车不变。
- **R2 未知商品**：加购不存在的 `sku` 返回 404，购物车不变。
- **R3 优惠券**：`POST /cart/coupon`，请求体 `{"code": "SAVE10"}`。小计**不低于** 10000 时可用，否则返回 422；未知券码返回 404；每个购物车只能使用一张券，再次使用返回 409。
- **R4 折扣**：使用 SAVE10 后折扣为小计的 10%，**向下取整**到分。
- **R5 运费**：应付金额 = 小计 − 折扣。应付金额大于 0 且低于 9900 时运费 800，不低于 9900 时免运费；购物车为空时运费为 0。
- **R6 下单**：`POST /checkout` 返回 201 和订单（含 `order_id`、`items`、`subtotal`、`discount`、`shipping`、`total`），随后购物车清空、优惠券失效；空购物车下单返回 400。
- **R7 查询**：`GET /cart` 返回 `items`、`subtotal`、`discount`、`shipping`、`total`、`coupon`，其中 `total` = 应付金额 + 运费。
