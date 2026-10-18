"""Calculate shipping from the payable amount in integer cents."""

FREE_SHIPPING_FROM = 5000
SHIPPING_FEE = 500


def shipping_fee(subtotal: int, discount: int = 0) -> int:
    if type(subtotal) is not int or type(discount) is not int:
        raise ValueError("Amounts must be integer cents")
    if subtotal < 0 or not 0 <= discount <= subtotal:
        raise ValueError("Amounts must satisfy 0 <= discount <= subtotal")
    payable = subtotal - discount
    return SHIPPING_FEE if 0 < payable < FREE_SHIPPING_FROM else 0
