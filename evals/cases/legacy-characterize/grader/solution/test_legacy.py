"""Reference answer used only to validate the grader; never copied into agent workspaces."""

import unittest
from decimal import Decimal

from tariff import quote


class LegacyTariffTests(unittest.TestCase):
    def test_n1_weight_range_and_invalid_values(self):
        for weight in [0, -1, "30.0001", "NaN", "sNaN", "Infinity", "-Infinity", "", None, True]:
            with self.subTest(weight=weight):
                with self.assertRaisesRegex(ValueError, "^bad weight$"):
                    quote(weight)
        self.assertEqual(quote("0.0001")["grams"], 500)
        self.assertEqual(quote(30)["base"], 6190)

    def test_legacy_half_kilogram_steps_and_weight_bands(self):
        rows = [
            ("0.4999", 500, 520),
            ("0.5", 500, 520),
            ("0.5001", 1000, 520),
            ("1", 1000, 520),
            ("1.0001", 1500, 655),
            ("1.5", 1500, 655),
            ("1.5001", 2000, 790),
            ("2.5", 2500, 925),
            ("3", 3000, 1060),
            ("3.0001", 3500, 1155),
            ("3.5", 3500, 1155),
            ("4", 4000, 1250),
            ("29.9999", 30000, 6190),
            ("30", 30000, 6190),
        ]
        for weight, grams, base in rows:
            with self.subTest(weight=weight):
                bill = quote(weight)
                self.assertEqual((bill["grams"], bill["base"], bill["total"]), (grams, base, base))

    def test_legacy_numeric_inputs_and_defaults(self):
        expected = {
            "zone": "local",
            "grams": 1500,
            "base": 655,
            "remote": 0,
            "express": 0,
            "discount": 0,
            "minimum": 0,
            "total": 655,
        }
        for weight in [1.25, "1.25", Decimal("1.25")]:
            with self.subTest(weight=weight):
                self.assertEqual(quote(weight), expected)

    def test_n2_zones_and_legacy_normalization(self):
        for zone, base, remote, total in [
            ("local", 520, 0, 520),
            ("country", 700, 0, 700),
            ("remote", 700, 450, 1150),
        ]:
            with self.subTest(zone=zone):
                bill = quote(1, zone)
                self.assertEqual(
                    (bill["base"], bill["remote"], bill["total"]), (base, remote, total)
                )
                self.assertEqual(quote(1, " " + zone.upper() + "\t"), bill)
        for zone in ["overseas", "", None, 1]:
            with self.subTest(zone=zone):
                with self.assertRaisesRegex(ValueError, "^bad zone$"):
                    quote(1, zone)

    def test_n3_remote_fee_is_fixed_and_not_discounted(self):
        for member, base, discount, total in [
            (False, 700, 0, 1150),
            (True, 700, 84, 1066),
        ]:
            with self.subTest(member=member):
                bill = quote(1, "remote", member=member)
                self.assertEqual(
                    (bill["base"], bill["remote"], bill["discount"], bill["total"]),
                    (base, 450, discount, total),
                )
        self.assertEqual(quote(30, "remote", member=True)["remote"], 450)

    def test_n4_member_cap_and_legacy_discount_rounding(self):
        for weight, base, discount, total in [
            (1.5, 655, 78, 577),
            (2, 790, 94, 696),
            (23.5, 4955, 594, 4361),
            (24, 5050, 600, 4450),
            (30, 6190, 600, 5590),
        ]:
            with self.subTest(weight=weight):
                bill = quote(weight, member=True)
                self.assertEqual(
                    (bill["base"], bill["discount"], bill["total"]), (base, discount, total)
                )
                self.assertEqual(quote(weight)["discount"], 0)

    def test_n5_minimum_and_legacy_application_after_extras(self):
        self.assertEqual(
            quote(1, member=True),
            {
                "zone": "local",
                "grams": 1000,
                "base": 520,
                "remote": 0,
                "express": 0,
                "discount": 62,
                "minimum": 42,
                "total": 500,
            },
        )
        bill = quote(1, member=True, express=True)
        self.assertEqual(
            (bill["express"], bill["discount"], bill["minimum"], bill["total"]), (130, 62, 0, 588)
        )

    def test_legacy_express_rounds_half_up_before_discount(self):
        for weight, express, total in [(1.5, 164, 819), (4, 313, 1563)]:
            with self.subTest(weight=weight):
                bill = quote(weight, express=True)
                self.assertEqual((bill["express"], bill["total"]), (express, total))
        self.assertEqual(quote(4, member=True, express=True)["total"], 1413)
        self.assertEqual(
            quote(4, "remote", member=True, express=True),
            {
                "zone": "remote",
                "grams": 4000,
                "base": 1430,
                "remote": 450,
                "express": 358,
                "discount": 171,
                "minimum": 0,
                "total": 2067,
            },
        )

    def test_n6_integer_cents_and_legacy_independent_results(self):
        bill = quote(4, "remote", member=True, express=True)
        for field in ["base", "remote", "express", "discount", "minimum", "total"]:
            self.assertIs(type(bill[field]), int, field)
        bill["total"] = -1
        self.assertEqual(quote(4, "remote", member=True, express=True)["total"], 2067)

    def test_legacy_flags_require_booleans(self):
        for field in ["member", "express"]:
            for value in [0, 1, "yes", None]:
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "^bad " + field + "$"):
                        quote(1, **{field: value})


if __name__ == "__main__":
    unittest.main()
