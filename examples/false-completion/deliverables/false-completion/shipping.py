"""An incomplete delivery that passes the happy-path self-check."""

FREE_SHIPPING_FROM = 5000
SHIPPING_FEE = 500


def shipping_fee(subtotal: int, discount: int = 0) -> int:
    return 0 if subtotal >= FREE_SHIPPING_FROM else SHIPPING_FEE
