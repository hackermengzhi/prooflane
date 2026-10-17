"""Bounded, read-only inspection of artifact bytes without loading target code."""

import codecs
import hashlib
import re
import stat
import sys
from importlib.util import MAGIC_NUMBER
from pathlib import Path

MACH_HEADERS = {
    b"\xfe\xed\xfa\xce": ("big", 32),
    b"\xce\xfa\xed\xfe": ("little", 32),
    b"\xfe\xed\xfa\xcf": ("big", 64),
    b"\xcf\xfa\xed\xfe": ("little", 64),
}
FAT_HEADERS = {
    b"\xca\xfe\xba\xbe": ("big", 20),
    b"\xbe\xba\xfe\xca": ("little", 20),
    b"\xca\xfe\xba\xbf": ("big", 32),
    b"\xbf\xba\xfe\xca": ("little", 32),
}
MACH_ARCHITECTURES = {
    7: "x86",
    0x01000007: "x86_64",
    12: "arm",
    0x0100000C: "arm64",
    0x0200000C: "arm64_32",
    18: "ppc",
    0x01000012: "ppc64",
}
# Java class files share the 0xCAFEBABE magic; their major version (>= 45) sits where a
# fat header keeps its architecture count, so a real universal binary stays below this.
MAX_FAT_ARCHITECTURES = 20
ASCII_STRINGS = re.compile(rb"[\x20-\x7e]+")


def read_prefix(source: Path, max_bytes: int) -> tuple[bytes | None, dict]:
    try:
        info = source.stat()
        if not stat.S_ISREG(info.st_mode):
            raise OSError("Expected a regular file")
        with source.open("rb") as stream:
            content = stream.read(max_bytes)
    except OSError as error:
        return None, {"bytes_read": 0, "truncated": False, "error": str(error)}
    return content, {
        "size": info.st_size,
        "bytes_read": len(content),
        "truncated": info.st_size > len(content),
    }


def mach_header(content: bytes) -> dict:
    magic = content[:4]
    if magic in MACH_HEADERS:
        byteorder, bits = MACH_HEADERS[magic]
        if len(content) < (32 if bits == 64 else 28):
            return {"format": "unknown"}
        cpu = int.from_bytes(content[4:8], byteorder)
        return {
            "format": "mach-o",
            "bits": bits,
            "byteorder": byteorder,
            "architectures": [MACH_ARCHITECTURES.get(cpu, f"cpu-{cpu:#x}")],
        }
    byteorder, entry_size = FAT_HEADERS[magic]
    if len(content) < 8:
        return {"format": "unknown"}
    count = int.from_bytes(content[4:8], byteorder)
    if count == 0 or count > MAX_FAT_ARCHITECTURES or count > (len(content) - 8) // entry_size:
        return {"format": "unknown"}
    architectures = []
    for index in range(count):
        offset = 8 + index * entry_size
        cpu = int.from_bytes(content[offset : offset + 4], byteorder)
        architectures.append(MACH_ARCHITECTURES.get(cpu, f"cpu-{cpu:#x}"))
    return {"format": "mach-o", "byteorder": byteorder, "architectures": architectures}


def identify_header(content: bytes, truncated: bool) -> dict:
    magic = content[:4]
    if magic == b"\x7fELF":
        if len(content) < 16 or content[4] not in (1, 2) or content[5] not in (1, 2):
            return {"format": "unknown"}
        return {
            "format": "elf",
            "bits": 32 if content[4] == 1 else 64,
            "byteorder": "little" if content[5] == 1 else "big",
        }
    if magic in MACH_HEADERS or magic in FAT_HEADERS:
        return mach_header(content)
    if content.startswith(b"MZ"):
        result = {"format": "pe", "is_pe": False}
        if len(content) >= 64:
            offset = int.from_bytes(content[60:64], "little")
            if offset >= 64 and offset + 4 <= len(content):
                result["is_pe"] = content[offset : offset + 4] == b"PE\0\0"
            elif offset >= 64 and truncated:
                # The signature lies beyond the inspected prefix, so it is unverified.
                result["is_pe"] = None
        return result
    if magic == MAGIC_NUMBER or (
        len(content) >= 4 and content[2:4] == b"\r\n" and content[1] in (0x0C, 0x0D, 0x0E)
    ):
        result = {"format": "python-bytecode"}
        if magic == MAGIC_NUMBER:
            result["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}"
        return result
    if magic in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return {"format": "zip"}
    if content.startswith(b"\x1f\x8b\x08"):
        return {"format": "gzip"}
    if content.startswith(b"%PDF-"):
        return {"format": "pdf"}
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(content, final=not truncated)
    except UnicodeDecodeError:
        return {"format": "unknown"}
    if text and all(character.isprintable() or character in "\t\r\n" for character in text):
        return {"format": "utf-8-text"}
    return {"format": "unknown"}


def identify_artifact(source: Path, max_bytes: int) -> dict:
    content, result = read_prefix(source, max_bytes)
    if content is None:
        return {**result, "format": "unknown"}
    result.update(identify_header(content, result["truncated"]))
    result["sha256"] = hashlib.sha256(content).hexdigest()
    result["sha256_scope"] = "prefix" if result["truncated"] else "file"
    return result


def inspect_strings(source: Path, max_bytes: int, min_length: int = 4, limit: int = 200) -> dict:
    if type(min_length) is not int or min_length < 1:
        raise ValueError("min_length must be a positive integer")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    content, result = read_prefix(source, max_bytes)
    strings = []
    total = 0
    if content is not None:
        for match in ASCII_STRINGS.finditer(content):
            if match.end() - match.start() < min_length:
                continue
            total += 1
            if len(strings) < limit:
                strings.append(match.group().decode("ascii"))
    return {**result, "strings": strings, "total": total, "limit_reached": total > len(strings)}
