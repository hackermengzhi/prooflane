"""Seeded defect: acknowledges payment without transitioning the order status."""

import sqlite3


def apply_payment(connection: sqlite3.Connection, order_id: str) -> None:
    connection.execute(
        "UPDATE orders SET payment_count = 1 WHERE id = ? AND payment_count = 0",
        (order_id,),
    )
