import hashlib
import struct
import sys
import tempfile
import unittest
from importlib.util import MAGIC_NUMBER
from pathlib import Path

from prooflane.tools import MAX_FILE_BYTES, ToolPolicy, Tools


class ReverseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.tools = Tools(ToolPolicy(self.workspace), self.root / "evidence")

    def inspect(self, content, tool="artifact.identify", **arguments):
        source = self.workspace / "sample"
        source.write_bytes(content)
        result = self.tools.call(tool, {"path": "sample", **arguments})
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(source.read_bytes(), content)
        return result["data"]

    def test_identifies_formats_and_records_complete_file_hash(self):
        elf = b"\x7fELF\x02\x01\x01" + bytes(57)
        mach = struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0)
        pe = b"MZ" + bytes(58) + struct.pack("<I", 64) + b"PE\0\0" + bytes(20)
        samples = [
            (elf, {"format": "elf", "bits": 64, "byteorder": "little"}),
            (mach, {"format": "mach-o", "bits": 64, "architectures": ["arm64"]}),
            (pe, {"format": "pe", "is_pe": True}),
            (MAGIC_NUMBER + bytes(12), {"format": "python-bytecode"}),
            (b"PK\x05\x06" + bytes(18), {"format": "zip"}),
            (b"\x1f\x8b\x08" + bytes(7), {"format": "gzip"}),
            (b"%PDF-1.7\n", {"format": "pdf"}),
            ("万测王\nTest\t1\r\n".encode(), {"format": "utf-8-text"}),
            (b"\x00\xff\x01", {"format": "unknown"}),
        ]
        for content, expected in samples:
            with self.subTest(format=expected["format"]):
                result = self.inspect(content)
                for key, value in expected.items():
                    self.assertEqual(result[key], value)
                self.assertEqual(result["size"], len(content))
                self.assertEqual(result["bytes_read"], len(content))
                self.assertEqual(result["sha256"], hashlib.sha256(content).hexdigest())
                self.assertEqual(result["sha256_scope"], "file")
                self.assertFalse(result["truncated"])

    def test_elf_word_sizes_and_byte_orders(self):
        for elf_class, bits in [(1, 32), (2, 64)]:
            for encoding, byteorder in [(1, "little"), (2, "big")]:
                with self.subTest(bits=bits, byteorder=byteorder):
                    header = b"\x7fELF" + bytes([elf_class, encoding, 1]) + bytes(9)
                    result = self.inspect(header)
                    self.assertEqual(result["bits"], bits)
                    self.assertEqual(result["byteorder"], byteorder)

    def test_thin_mach_word_sizes_and_byte_orders(self):
        for endian, byteorder in [("<", "little"), (">", "big")]:
            for magic, bits, words in [(0xFEEDFACE, 32, 7), (0xFEEDFACF, 64, 8)]:
                with self.subTest(bits=bits, byteorder=byteorder):
                    content = struct.pack(endian + "I" * words, magic, 7, *([0] * (words - 2)))
                    result = self.inspect(content)
                    self.assertEqual(result["format"], "mach-o")
                    self.assertEqual(result["bits"], bits)
                    self.assertEqual(result["byteorder"], byteorder)
                    self.assertEqual(result["architectures"], ["x86"])

    def test_fat_mach_architectures_in_both_byte_orders_and_table_sizes(self):
        for endian in ["<", ">"]:
            for magic, entry_format in [(0xCAFEBABE, "5I"), (0xCAFEBABF, "IIQQII")]:
                with self.subTest(endian=endian, magic=magic):
                    content = struct.pack(endian + "II", magic, 2)
                    for cpu in [0x01000007, 0x0100000C]:
                        fields = [cpu, 0, 0, 0, 0]
                        if magic == 0xCAFEBABF:
                            fields.append(0)
                        content += struct.pack(endian + entry_format, *fields)
                    result = self.inspect(content)
                    self.assertEqual(result["format"], "mach-o")
                    self.assertEqual(result["architectures"], ["x86_64", "arm64"])

    def test_java_class_sharing_fat_magic_is_not_reported_as_mach_o(self):
        # Major version 65 (Java 21) occupies the fat architecture-count field.
        content = struct.pack(">IHH", 0xCAFEBABE, 0, 65) + bytes(2000)
        self.assertEqual(self.inspect(content)["format"], "unknown")

    def test_pe_mz_header_does_not_imply_pe_signature(self):
        for content in [b"MZ", b"MZ" + bytes(62), b"MZ" + bytes(58) + struct.pack("<I", 1)]:
            with self.subTest(content=content):
                result = self.inspect(content)
                self.assertEqual(result["format"], "pe")
                self.assertFalse(result["is_pe"])

    def test_pe_signature_outside_read_limit_is_unverified(self):
        content = b"MZ" + bytes(58) + struct.pack("<I", MAX_FILE_BYTES)
        content += bytes(MAX_FILE_BYTES - len(content)) + b"PE\0\0"
        result = self.inspect(content)
        self.assertEqual(result["format"], "pe")
        self.assertIsNone(result["is_pe"])
        self.assertTrue(result["truncated"])

    def test_python_version_is_only_reported_for_known_magic(self):
        result = self.inspect(MAGIC_NUMBER + bytes(12))
        self.assertEqual(result["python_version"], f"{sys.version_info.major}.{sys.version_info.minor}")
        result = self.inspect(b"\x00\x0d\r\n" + bytes(12))
        self.assertEqual(result["format"], "python-bytecode")
        self.assertNotIn("python_version", result)
        self.assertEqual(self.inspect(b"ab\r\n")["format"], "utf-8-text")

    def test_malformed_headers_and_empty_files_are_unknown(self):
        samples = [
            b"", b"\xff", b"\x00", b"\x7fELF", b"\x7fELF" + bytes(12),
            b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
            struct.pack(">II", 0xCAFEBABE, 0),
            struct.pack(">II", 0xCAFEBABE, 0xFFFFFFFF),
            struct.pack(">II", 0xCAFEBABE, 2) + bytes(20),
        ]
        for content in samples:
            with self.subTest(content=content):
                self.assertEqual(self.inspect(content)["format"], "unknown")

    def test_strings_defaults_filter_ascii_runs_and_count_all_matches(self):
        content = b"abc\0version 1.0\xffhttps://example.test\nerror\x7fxy\tend!"
        result = self.inspect(content, "artifact.strings")
        self.assertEqual(result["strings"], ["version 1.0", "https://example.test", "error", "end!"])
        self.assertEqual(result["total"], 4)
        self.assertFalse(result["limit_reached"])
        self.assertFalse(result["truncated"])

    def test_strings_custom_minimum_and_limit_preserve_total(self):
        content = b"ab\0abcd\0abcde\0abcdef"
        result = self.inspect(content, "artifact.strings", min_length=5, limit=1)
        self.assertEqual(result["strings"], ["abcde"])
        self.assertEqual(result["total"], 2)
        self.assertTrue(result["limit_reached"])
        result = self.inspect(content, "artifact.strings", min_length=10)
        self.assertEqual(result["strings"], [])
        self.assertEqual(result["total"], 0)

    def test_strings_default_limit_is_200(self):
        result = self.inspect(b"abcd\0" * 205, "artifact.strings")
        self.assertEqual(len(result["strings"]), 200)
        self.assertEqual(result["total"], 205)
        self.assertTrue(result["limit_reached"])

    def test_large_files_only_inspect_prefix_and_label_hash_scope(self):
        prefix = b"abcd\0" + bytes(MAX_FILE_BYTES - 5)
        content = prefix + b"outside-the-prefix"
        for tool in ["artifact.identify", "artifact.strings"]:
            with self.subTest(tool=tool):
                result = self.inspect(content, tool)
                self.assertTrue(result["truncated"])
                self.assertEqual(result["size"], len(content))
                self.assertEqual(result["bytes_read"], MAX_FILE_BYTES)
                if tool == "artifact.identify":
                    self.assertEqual(result["sha256_scope"], "prefix")
                    self.assertEqual(result["sha256"], hashlib.sha256(prefix).hexdigest())
                else:
                    self.assertEqual(result["strings"], ["abcd"])
                    self.assertEqual(result["total"], 1)

    def test_utf8_prefix_can_end_inside_a_multibyte_character(self):
        content = b"a" * (MAX_FILE_BYTES - 1) + "万".encode()
        result = self.inspect(content)
        self.assertEqual(result["format"], "utf-8-text")
        self.assertTrue(result["truncated"])

    def test_unreadable_sources_return_normal_results(self):
        for path in ["missing", "."]:
            for tool in ["artifact.identify", "artifact.strings"]:
                with self.subTest(path=path, tool=tool):
                    result = self.tools.call(tool, {"path": path})
                    self.assertEqual(result["status"], "succeeded")
                    self.assertIn("error", result["data"])
                    if tool == "artifact.identify":
                        self.assertEqual(result["data"]["format"], "unknown")
                    else:
                        self.assertEqual(result["data"]["strings"], [])
                        self.assertEqual(result["data"]["total"], 0)

    def test_workspace_escape_absolute_paths_and_external_symlinks_are_blocked(self):
        outside = self.root / "outside"
        outside.write_bytes(b"external secret")
        (self.workspace / "linked").symlink_to(outside)
        for path in ["../outside", str(outside), "linked"]:
            for tool in ["artifact.identify", "artifact.strings"]:
                with self.subTest(path=path, tool=tool):
                    result = self.tools.call(tool, {"path": path})
                    self.assertEqual(result["status"], "blocked")
                    self.assertNotIn("external secret", str(result))

    def test_invalid_string_options_are_blocked(self):
        (self.workspace / "sample").write_bytes(b"abcd")
        for option in ["min_length", "limit"]:
            for value in [0, -1, True, 1.5, "4"]:
                with self.subTest(option=option, value=value):
                    result = self.tools.call("artifact.strings", {"path": "sample", option: value})
                    self.assertEqual(result["status"], "blocked")

    def test_catalog_exposes_both_read_only_tools_and_arguments(self):
        catalog = self.tools.catalog()
        self.assertEqual(set(catalog["artifact.identify"]["arguments"]), {"path"})
        self.assertEqual(
            set(catalog["artifact.strings"]["arguments"]), {"path", "min_length", "limit"}
        )


if __name__ == "__main__":
    unittest.main()
