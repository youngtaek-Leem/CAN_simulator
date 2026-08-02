"""
UDS (Unified Diagnostic Services) core module.

Provides PDU builders for UDS request messages and response parsers.
All builders return bytearray objects ready for ISO-TP transmission.

Supported services:
  - 0x10 DiagnosticSessionControl
  - 0x11 EcuReset
  - 0x22 ReadDataByIdentifier
  - 0x27 SecurityAccess
  - 0x28 CommunicationControl
  - 0x31 RoutineControl
  - 0x34 RequestDownload
  - 0x36 TransferData
  - 0x37 RequestTransferExit
  - 0x3E TesterPresent
  - 0x85 ControlDTCSetting
"""

from __future__ import annotations

from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Session type constants
# ---------------------------------------------------------------------------

SESSION_DEFAULT = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED = 0x03

# ---------------------------------------------------------------------------
# Routine control type constants
# ---------------------------------------------------------------------------

ROUTINE_START = 0x01
ROUTINE_STOP = 0x02
ROUTINE_REQUEST_RESULTS = 0x03

# ---------------------------------------------------------------------------
# ECU reset mode constants
# ---------------------------------------------------------------------------

RESET_HARD = 0x01
RESET_KEY_OFF_ON = 0x02
RESET_SOFT = 0x03

# ---------------------------------------------------------------------------
# Security access constants
# ---------------------------------------------------------------------------

SECURITY_REQUEST_SEED = 0x01
SECURITY_SEND_KEY = 0x02

# ---------------------------------------------------------------------------
# Dummy key generation (override with .dll or custom logic)
# ---------------------------------------------------------------------------


def generate_key(seed: bytes) -> bytearray:
    """Generate a dummy key (zero key) for security access.
    
    In production, replace with real key-generation logic (e.g. DLL call).
    """
    return bytearray(b'\x00' * 8)


# ---------------------------------------------------------------------------
# Exception class
# ---------------------------------------------------------------------------


class UdsError(Exception):
    """UDS error with optional NRC code."""

    def __init__(self, message: str, nrc: int = 0) -> None:
        super().__init__(message)
        self.nrc = nrc


# ---------------------------------------------------------------------------
# CAN ID helpers
# ---------------------------------------------------------------------------

CAN_EXT_FLAG = 0x80000000
PADDING_BYTE = 0xCC


def ext_id(raw_id: int) -> int:
    """Return an extended CAN ID (29-bit) suitable for isotp_service."""
    if raw_id > 0x1FFFFFFF:
        raise ValueError(f"CAN ID too large: 0x{raw_id:08X}")
    return raw_id


# ---------------------------------------------------------------------------
# ISO-TP addressing helpers
# ---------------------------------------------------------------------------

def phys_req(base_id: int) -> int:
    """Return physical request CAN ID (TA=PHYS)."""
    return ext_id(base_id)


def func_req(base_id: int) -> int:
    """Return functional request CAN ID (TA=FUNC)."""
    return ext_id(base_id | 0x100)


# ---------------------------------------------------------------------------
# TesterPresent helper
# ---------------------------------------------------------------------------

def build_tester_present(suppress_pos_rsp: bool = False) -> bytearray:
    """UDS TesterPresent (0x3E)."""
    subfunc = 0x80 if suppress_pos_rsp else 0x00
    return bytearray([0x3E, subfunc])


# ---------------------------------------------------------------------------
# Service 0x10 - DiagnosticSessionControl
# ---------------------------------------------------------------------------

def build_diagnostic_session(session_type: int) -> bytearray:
    """UDS DiagnosticSessionControl (0x10).

    Only rejects values that don't fit in the single-byte subFunction --
    real test procedures (e.g. the OTA Tester reference XML's VersionCheck
    hook uses 0x81) legitimately use ECU-manufacturer-specific session types
    outside the standard 0x01/0x02/0x03/0x7F set, and the ECU itself is the
    correct arbiter of whether it accepts an unusual value."""
    if not 0x00 <= session_type <= 0xFF:
        raise ValueError(f"Invalid session_type: 0x{session_type:X} (must fit in one byte)")
    return bytearray([0x10, session_type])


# ---------------------------------------------------------------------------
# Service 0x22 - ReadDataByIdentifier
# ---------------------------------------------------------------------------

def build_read_data_by_id(did: int) -> bytearray:
    """Build UDS ReadDataByIdentifier request (0x22)."""
    did = int(did)
    return bytearray([0x22, (did >> 8) & 0xFF, did & 0xFF])


# ---------------------------------------------------------------------------
# Service 0x27 - SecurityAccess
# ---------------------------------------------------------------------------

def build_security_access_request_seed(access_mode: int = 0x01) -> bytearray:
    """Request seed. ``access_mode`` is the securityAccessType sub-function
    (an odd value per ISO 14229-1, e.g. 0x01, 0x03, 0x11...); it must match
    whatever level the XML's requestSeed step (or the caller) specifies, or
    the ECU will reject it as a level it never offered a seed for."""
    return bytearray([0x27, access_mode])


def build_security_access_send_key(key: bytes, access_mode: int = 0x02) -> bytearray:
    """Send key. ``access_mode`` is the securityAccessType sub-function (an
    even value, one greater than the matching requestSeed level, e.g. 0x02,
    0x04, 0x12...) and must match the level used for the seed request above."""
    if len(key) != 8:
        raise ValueError(f"Key must be 8 bytes, got {len(key)}")
    result = bytearray([0x27, access_mode])
    result.extend(key)
    return result


# ---------------------------------------------------------------------------
# Service 0x28 - CommunicationControl
# ---------------------------------------------------------------------------

def build_communication_control(
    control_type: int, communication_type: int
) -> bytearray:
    """UDS CommunicationControl (0x28)."""
    return bytearray([0x28, control_type, communication_type])


# ---------------------------------------------------------------------------
# Service 0x31 - RoutineControl
# ---------------------------------------------------------------------------

def build_routine_control(
    routine_control_type: int,
    routine_identifier: int,
    option_record: Optional[bytes] = None,
) -> bytearray:
    """UDS RoutineControl (0x31)."""
    result = bytearray(
        [0x31, routine_control_type,
         (routine_identifier >> 8) & 0xFF,
         routine_identifier & 0xFF]
    )
    if option_record:
        result.extend(option_record)
    return result


# ---------------------------------------------------------------------------
# Service 0x34 - RequestDownload
# ---------------------------------------------------------------------------

def build_request_download(
    data_format_identifier: int,
    address_and_length_format_identifier: int,
    memory_address: int,
    memory_size: int,
) -> bytearray:
    """UDS RequestDownload (0x34)."""
    addr_len = (address_and_length_format_identifier >> 4) & 0xF
    size_len = address_and_length_format_identifier & 0xF

    result = bytearray([0x34, data_format_identifier,
                         address_and_length_format_identifier])
    result.extend(memory_address.to_bytes(addr_len, 'big'))
    result.extend(memory_size.to_bytes(size_len, 'big'))
    return result


# ---------------------------------------------------------------------------
# Service 0x36 - TransferData
# ---------------------------------------------------------------------------

def build_transfer_data(
    block_sequence_number: int, data_block: bytes
) -> bytearray:
    """UDS TransferData (0x36)."""
    result = bytearray([0x36, block_sequence_number & 0xFF])
    result.extend(data_block)
    return result


# ---------------------------------------------------------------------------
# Service 0x37 - RequestTransferExit
# ---------------------------------------------------------------------------

def build_request_transfer_exit() -> bytearray:
    """UDS RequestTransferExit (0x37)."""
    return bytearray([0x37])


# ---------------------------------------------------------------------------
# Service 0x11 - EcuReset
# ---------------------------------------------------------------------------

def build_ecu_reset(reset_mode: int) -> bytearray:
    """UDS EcuReset (0x11)."""
    return bytearray([0x11, reset_mode])


# ---------------------------------------------------------------------------
# Service 0x85 - ControlDTCSetting
# ---------------------------------------------------------------------------

def build_control_dtc_setting(dtc_setting_type: int) -> bytearray:
    """UDS ControlDTCSetting (0x85)."""
    return bytearray([0x85, dtc_setting_type])


# ---------------------------------------------------------------------------
# Negative Response (NR) helpers
# ---------------------------------------------------------------------------

NR_CODES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceedNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7F: "serviceNotSupportedInActiveSession",
    0x85: "dtcSettingTypeNotSupported",
}


def is_positive_response(response: bytes, service_id: int) -> bool:
    """Check if response is a positive UDS response."""
    if not response:
        return False
    return response[0] == (service_id + 0x40)


def parse_negative_response(response: bytes) -> Tuple[int, str]:
    """Parse a negative response (0x7F), returning (nrc, description)."""
    if len(response) < 3 or response[0] != 0x7F:
        return 0, "not_negative"
    nrc = response[2]
    return nrc, NR_CODES.get(nrc, f"unknown (0x{nrc:02X})")


def is_response_pending(response: bytes) -> bool:
    """Check if response is Negative Response 0x78 (responsePending)."""
    if len(response) < 3 or response[0] != 0x7F:
        return False
    return response[2] == 0x78


def parse_did_response(response: bytes) -> bytes:
    """Extract data payload from ReadDataByIdentifier positive response."""
    if not response or response[0] != 0x62:
        return b""
    return response[3:] if len(response) > 3 else b""


# ---------------------------------------------------------------------------
# Response data parsing
# ---------------------------------------------------------------------------

def parse_security_access_seed(response: bytes) -> bytes:
    """Extract seed from SecurityAccess positive response."""
    if len(response) < 10 or response[0] != 0x67:
        return b""
    return response[2:10]


def parse_routine_control_response(response: bytes) -> bytes:
    """Extract data from RoutineControl response."""
    if not response or response[0] != 0x71:
        return b""
    return response[4:] if len(response) > 4 else b""


# ---------------------------------------------------------------------------
# Timing constants (defaults, overridden by project folder or user params)
# ---------------------------------------------------------------------------

DEFAULT_TIMING = {
    "p2can_server_max": 50,
    "p2_star_can_server_max": 5000,
    "st_min_tx": 0x0A,
    "nrc78_repetition_timeout": 300,
    "p6_time": 1950,
}


# ---------------------------------------------------------------------------
# Request sending utility
# ---------------------------------------------------------------------------

def _to_hex_string(data: bytes) -> str:
    """Format bytes as hex string (no spaces)."""
    return data.hex().upper()


# ---------------------------------------------------------------------------
# Compatibility aliases for uds_download_manager.py
# ---------------------------------------------------------------------------

# Session control alias
build_session_control = build_diagnostic_session

# Security access aliases
build_seed_request = build_security_access_request_seed


def build_send_key(access_mode: int, key: bytes) -> bytearray:
    """Send key with access mode byte (e.g. 0x12)."""
    if len(key) != 8:
        raise ValueError(f"Key must be 8 bytes, got {len(key)}")
    result = bytearray([0x27, access_mode, *key])
    return result


# Transfer exit alias
build_transfer_exit = build_request_transfer_exit


def parse_response(response: bytes) -> dict:
    """Parse a UDS response, returning a dict with 'positive', 'sid', 'nrc', 'data'."""
    result: dict = {"positive": False, "sid": 0, "nrc": 0, "data": b""}
    if not response:
        return result

    byte0 = response[0]
    if byte0 == 0x7F and len(response) >= 3:
        result["positive"] = False
        result["sid"] = response[1] if len(response) > 1 else 0
        result["nrc"] = response[2] if len(response) > 2 else 0
        return result

    result["positive"] = True
    result["data"] = response
    return result


def parse_request_download_response(response: bytes) -> dict:
    """Parse RequestDownload positive response payload."""
    info = {"length_format": 0, "max_length": 0}
    if len(response) >= 2 and response[0] == 0x74:
        lfi = response[1] & 0x0F
        if lfi == 0:
            lfi = 2
        start = 2
        end = start + lfi
        if end <= len(response):
            info["length_format"] = lfi
            info["max_length"] = int.from_bytes(response[start:end], byteorder="big")
    return info