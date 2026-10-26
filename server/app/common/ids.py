"""ULID/ID 生成（26 字符，时间有序，§8.3/§8.4）。"""

import os
import time

# Crockford Base32 字母表（去除 I L O U）
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_LEN = 10
_RANDOM_LEN = 16


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_id() -> str:
    """生成 ULID：10 位毫秒时间戳 + 16 位随机，共 26 字符，字典序=时间序。"""
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ms, _TIME_LEN) + _encode(rand, _RANDOM_LEN)


def new_request_id() -> str:
    """requestId 同样使用 ULID（与日志/审计关联）。"""
    return new_id()
