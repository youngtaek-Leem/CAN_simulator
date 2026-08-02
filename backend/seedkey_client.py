"""HKMC Advanced SeedKey PC client wrapper.

Wraps HKMC_AdvancedSeedKey_Win32.dll (32-bit) / HKMC_AdvancedSeedKey_x64.dll
(64-bit), per HKMC_ASK_Client.h and the vendor's Advanced SeedKey manual, so
UDS SecurityAccess (service 0x27) in uds_download_manager.py can compute a
real key instead of uds_core.generate_key()'s dummy zero-key stub.

Windows only: the DLL is a Windows PE binary and cannot be loaded via ctypes
on any other OS. When no DLL is loaded (not uploaded yet, or running on a
non-Windows dev machine), callers fall back to the dummy stub -- see
uds_download_manager._execute_security_access().
"""

from __future__ import annotations

import ctypes
import os
import platform
import threading
from typing import Optional

SEEDKEY_SUCCESS = 0


class VersionInfo(ctypes.Structure):
    _fields_ = [
        ("vendorID", ctypes.c_uint16),
        ("moduleID", ctypes.c_uint16),
        ("majorVersion", ctypes.c_uint8),
        ("minorVersion", ctypes.c_uint8),
        ("patchVersion", ctypes.c_uint8),
    ]

    def as_dict(self) -> dict:
        return {
            "vendor_id": self.vendorID,
            "module_id": self.moduleID,
            "version": f"{self.majorVersion}.{self.minorVersion}.{self.patchVersion}",
        }


class SeedKeyError(Exception):
    pass


class AdvancedSeedKeyClient:
    """Loads one HKMC_AdvancedSeedKey_*.dll and exposes ASK_KeyGenerate.

    Exported functions used (__cdecl, confirmed via disassembly of the
    shipped DLLs by the vendor's own reference client):
      SEEDKEY_RT ASK_KeyGenerate(const uint8 *seed_buffer_8byte, uint8 *key_buffer_8byte)
      void       vGetVersionInfo(VERSION_INFO *pVersionInfo)
    """

    def __init__(self, dll_path: str):
        if platform.system() != "Windows":
            raise OSError(
                "HKMC_AdvancedSeedKey DLL은 Windows PE 바이너리라 Windows에서만 "
                f"로드할 수 있습니다 (현재 OS: {platform.system()})."
            )
        if not os.path.isfile(dll_path):
            raise FileNotFoundError(dll_path)

        is_64bit = platform.architecture()[0] == "64bit"
        basename = os.path.basename(dll_path).lower()
        if "x64" in basename and not is_64bit:
            raise OSError("이 DLL은 64비트용입니다. 32비트 Python에서는 로드할 수 없습니다.")
        if "win32" in basename and is_64bit:
            raise OSError("이 DLL은 32비트용입니다. 64비트 Python에서는 로드할 수 없습니다.")

        self._dll = ctypes.CDLL(dll_path)
        self._dll.ASK_KeyGenerate.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
        ]
        self._dll.ASK_KeyGenerate.restype = ctypes.c_int
        self._dll.vGetVersionInfo.argtypes = [ctypes.POINTER(VersionInfo)]
        self._dll.vGetVersionInfo.restype = None

        self.dll_path = dll_path

    def generate_key(self, seed: bytes) -> bytes:
        if len(seed) != 8:
            raise ValueError(f"seed는 정확히 8바이트여야 합니다 (받은 길이: {len(seed)})")
        seed_buf = (ctypes.c_uint8 * 8).from_buffer_copy(seed)
        key_buf = (ctypes.c_uint8 * 8)()
        ret = self._dll.ASK_KeyGenerate(seed_buf, key_buf)
        if ret != SEEDKEY_SUCCESS:
            raise SeedKeyError(f"ASK_KeyGenerate 실패 (SEEDKEY_RT={ret})")
        return bytes(key_buf)

    def get_version(self) -> VersionInfo:
        info = VersionInfo()
        self._dll.vGetVersionInfo(ctypes.byref(info))
        return info


class SeedKeyService:
    """Holds the currently-loaded SeedKey DLL client. One shared algorithm
    per running instance of the tool (not per CAN-SWDL slot) -- SecurityAccess
    steps across all 3 slots use whichever DLL is loaded here."""

    def __init__(self):
        self._client: Optional[AdvancedSeedKeyClient] = None
        self._filename: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        with self._lock:
            return self._client is not None

    def load(self, dll_path: str, filename: str) -> dict:
        client = AdvancedSeedKeyClient(dll_path)
        with self._lock:
            self._client = client
            self._filename = filename
        return self.status()

    def status(self) -> dict:
        with self._lock:
            client, filename = self._client, self._filename
        if client is None:
            return {"loaded": False, "filename": None, "version": None}
        try:
            version = client.get_version().as_dict()
        except Exception:
            version = None
        return {"loaded": True, "filename": filename, "version": version}

    def generate_key(self, seed: bytes) -> bytes:
        """Real vendor algorithm. Raises RuntimeError if no DLL is loaded --
        callers should check `.loaded` first and fall back to
        uds_core.generate_key() themselves (see _execute_security_access)."""
        with self._lock:
            client = self._client
        if client is None:
            raise RuntimeError("SeedKey DLL이 로드되지 않았습니다")
        return client.generate_key(seed)
