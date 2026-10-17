"""Correct payment transition, guarded against duplicate callbacks."""

import sqlite3


def apply_payment(connection: sqlite3.Connection, order_id: str) -> None:
    connection.execute(
        "UPDATE orders SET status = 'paid', payment_count = 1 WHERE id = ? AND payment_count = 0",
        (order_id,),
    )
