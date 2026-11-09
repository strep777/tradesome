"""本地密钥保险箱。

优先使用 Windows DPAPI（CryptProtectData）：密文与当前 Windows 用户绑定，
换用户或换机器都解不开，不需要用户再记一个主密码。
DPAPI 不可用时降级为 PBKDF2 + 机器标识派生密钥，并在界面上明确提示这是弱保护。

文件格式（JSON）：
    {"version": 1, "mode": "dpapi"|"fallback", "salt": "...", "data": "..."}
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import platform
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Dict, List, Optional

from ..core.errors import SecurityError
from ..core.logging import get_logger

LOGGER = get_logger("security.vault")

VAULT_VERSION = 1
VAULT_FILENAME = "secrets.dat"
PBKDF2_ROUNDS = 200_000


# --------------------------------------------------------------------------- DPAPI


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _take_blob(blob: _DataBlob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        if blob.pbData:
            ctypes.windll.kernel32.LocalFree(blob.pbData)


def _dpapi_available() -> bool:
    if os.name != "nt":
        return False
    try:
        protected = _dpapi_protect(b"tradesome-probe")
        return _dpapi_unprotect(protected) == b"tradesome-probe"
    except Exception:  # noqa: BLE001 - 探测失败即视为不可用
        return False


def _dpapi_protect(data: bytes) -> bytes:
    blob_in = _blob(data)
    blob_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise SecurityError("DPAPI 加密失败（错误码 %s）" % ctypes.get_last_error())
    return _take_blob(blob_out)


def _dpapi_unprotect(data: bytes) -> bytes:
    blob_in = _blob(data)
    blob_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise SecurityError("DPAPI 解密失败（可能不是同一 Windows 用户创建的密钥）")
    return _take_blob(blob_out)


# --------------------------------------------------------------------------- 降级方案


def _machine_identity() -> bytes:
    parts = [platform.node() or "unknown", os.environ.get("USERNAME", "user")]
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                parts.append(str(guid))
        except Exception:  # noqa: BLE001 - 读不到就少一项，不影响可用性
            pass
    return "|".join(parts).encode("utf-8", "replace")


def _derive_key(salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", _machine_identity(), salt,
                               PBKDF2_ROUNDS, dklen=32)


def _keystream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _fallback_encrypt(plain: bytes, salt: bytes) -> bytes:
    key = _derive_key(salt)
    stream = _keystream(key, len(plain))
    body = bytes(a ^ b for a, b in zip(plain, stream))
    mac = hmac.new(key, body, hashlib.sha256).digest()
    return mac + body


def _fallback_decrypt(payload: bytes, salt: bytes) -> bytes:
    key = _derive_key(salt)
    mac, body = payload[:32], payload[32:]
    expected = hmac.new(key, body, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise SecurityError("密钥文件校验失败，可能被修改过")
    stream = _keystream(key, len(body))
    return bytes(a ^ b for a, b in zip(body, stream))


# --------------------------------------------------------------------------- 保险箱


class SecretVault:
    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = bool(enabled)
        self.mode = "dpapi" if _dpapi_available() else "fallback"
        self._cache: Optional[Dict[str, str]] = None

    # 基本信息

    def available(self) -> bool:
        return self.enabled

    @property
    def weak_protection(self) -> bool:
        return self.mode != "dpapi"

    def description(self) -> str:
        if not self.enabled:
            return "密钥保存已关闭，API Key 只留在当前会话内存里"
        if self.mode == "dpapi":
            return "已使用 Windows DPAPI 加密，密文仅当前 Windows 用户可解开"
        return "当前环境不支持 DPAPI，使用机器绑定弱加密；建议不要保存重要密钥"

    # 读写

    def keys(self) -> List[str]:
        return sorted(self._load().keys())

    def get(self, key: str) -> Optional[str]:
        if not self.enabled:
            return None
        return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        if not self.enabled:
            raise SecurityError("密钥保存已关闭，无法写入")
        if not key or not str(key).strip():
            raise SecurityError("密钥名不能为空")
        data = self._load()
        data[str(key)] = str(value)
        self._save(data)

    def delete(self, key: str) -> bool:
        data = self._load()
        if key not in data:
            return False
        data.pop(key)
        self._save(data)
        return True

    def clear(self) -> None:
        self._save({})

    # 内部

    def _load(self) -> Dict[str, str]:
        if self._cache is not None:
            return dict(self._cache)
        if not self.path.exists():
            self._cache = {}
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            mode = raw.get("mode", "fallback")
            salt = base64.b64decode(raw.get("salt", ""))
            payload = base64.b64decode(raw.get("data", ""))
            plain = _dpapi_unprotect(payload) if mode == "dpapi" else \
                _fallback_decrypt(payload, salt)
            data = json.loads(plain.decode("utf-8"))
            if not isinstance(data, dict):
                data = {}
        except SecurityError:
            raise
        except Exception as exc:  # noqa: BLE001 - 文件损坏时给出明确提示
            raise SecurityError("密钥文件无法读取：%s" % exc)
        self._cache = {str(k): str(v) for k, v in data.items()}
        return dict(self._cache)

    def _save(self, data: Dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plain = json.dumps(data, ensure_ascii=False).encode("utf-8")
        salt = os.urandom(16)
        payload = _dpapi_protect(plain) if self.mode == "dpapi" else \
            _fallback_encrypt(plain, salt)
        raw = {
            "version": VAULT_VERSION,
            "mode": self.mode,
            "salt": base64.b64encode(salt).decode("ascii"),
            "data": base64.b64encode(payload).decode("ascii"),
        }
        text = json.dumps(raw, ensure_ascii=False, indent=2)
        fd, temp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except Exception:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        self._cache = dict(data)
        LOGGER.info("密钥保险箱已更新，共 %d 项", len(data))


def default_vault(root: Path, enabled: bool = True) -> SecretVault:
    return SecretVault(Path(root) / "var" / VAULT_FILENAME, enabled=enabled)
