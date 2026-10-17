"""Reference reproduction of the full final page returning an extra cursor."""

import unittest

from pager import paginate


class PaginationReproductionTests(unittest.TestCase):
    def test_full_final_page_has_no_next_cursor(self):
        records = [1, 2, 3, 4, 5, 6]
        first = paginate(records, page_size=3)
        self.assertEqual(first.items, [1, 2, 3])
        self.assertIsNotNone(first.next_cursor)

        last = paginate(records, page_size=3, cursor=first.next_cursor)
        self.assertEqual(last.items, [4, 5, 6])
        self.assertIsNone(last.next_cursor)


if __name__ == "__main__":
    unittest.main()
