"""UDS (ISO 14229) protocol core.

Provides PDU builders and response parsers for the UDS services needed
for software download over CAN:

- DiagnosticSessionControl  (0x10)
- ECUReset                  (0x11)
- SecurityAccess            (0x27)
- RoutineControl            (0x31)
- RequestDownload           (0x34)
- TransferData              (0x36)
- RequestTransferExit       (0x37)

Also integrates the ASK (Authentication Seed & Key) client for SecurityAccess.
On macOS/Linux the ASK function returns a mock key; on Windows it uses the
HKMC AdvancedSeedKey DLL via reference/ask_client.py.
"""

import platform
from typing import Optional

# UDS Service IDs
SID_SESSION_CONTROL = 0x10
SID_ECU_RESET = 0x11
SID_SECURITY_ACCESS = 0x27
SID_ROUTINE_CONTROL = 0x31
SID_REQUEST_DOWNLOAD = 0x34
SID_TRANSFER_DATA = 0x36
SID_REQUEST_TRANSFER_EXIT = 0x37

# Positive Response SID (SID + 0x40)
PR_SID_MASK = 0x40

# Negative Response
SID_NEGATIVE_RESPONSE = 0x7F

# Negative Response Codes (NRC)
NRC_GENERAL_REJECT = 0x10
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED = 0x70
NRC_TRANSFER_DATA_SUSPENDED = 0x71
NRC_GENERAL_PROGRAMMING_FAILURE = 0x72
NRC_WRONG_BLOCK_SEQUENCE_COUNTER = 0x73
NRC_RESPONSE_PENDING = 0x78

# Session types
SESSION_DEFAULT = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED = 0x03

# Security access modes
SECURITY_REQUEST_SEED = 0x11
SECURITY_SEND_KEY = 0x12

# Routine control types
ROUTINE_START = 0x01
ROUTINE_STOP = 0x02
ROUTINE_REQUEST_RESULTS = 0x03

# ECU reset types
RESET_HARD = 0x01
RESET_KEY_OFF_ON = 0x02
RESET_SOFT = 0x03
RESET_ENABLE_RAPID_POWER_SHUTDOWN = 0x04
RESET_DISABLE_RAPID_POWER_SHUTDOWN = 0x05


class UdsError(Exception):
    """UDS protocol error with NRC."""
    def __init__(self, message: str, nrc: Optional[int] = None):
        super().__init__(message)
        self.nrc = nrc


# ---------------------------------------------------------------------------
# PDU Builders
# ---------------------------------------------------------------------------


def build_session_control(session_type: int) -> bytes:
    """Build DiagnosticSessionControl request."""
    return bytes([SID_SESSION_CONTROL, session_type])


def build_ecu_reset(reset_type: int) -> bytes:
    """Build ECUReset request."""
    return bytes([SID_ECU_RESET, reset_type])


def build_seed_request(access_mode: int = SECURITY_REQUEST_SEED) -> bytes:
    """Build SecurityAccess request for seed."""
    return bytes([SID_SECURITY_ACCESS, access_mode])


def build_send_key(access_mode: int, key: bytes) -> bytes:
    """Build SecurityAccess request to send key."""
    return bytes([SID_SECURITY_ACCESS, access_mode]) + key


def build_routine_control(
    control_type: int,
    routine_id: int,
    option_record: bytes = b"",
) -> bytes:
    """Build RoutineControl request.

    Parameters
    ----------
    control_type : int
        0x01 = start, 0x02 = stop, 0x03 = request results
    routine_id : int
        2-byte routine identifier
    option_record : bytes
        Optional additional data
    """
    return bytes([SID_ROUTINE_CONTROL, control_type, (routine_id >> 8) & 0xFF, routine_id & 0xFF]) + option_record


def build_request_download(
    data_format_identifier: int,
    addr_length_format: int,
    memory_address: int,
    memory_size: int,
) -> bytes:
    """Build RequestDownload request.

    Parameters
    ----------
    data_format_identifier : int
        DFI byte (e.g. 0x00 = no compression, no encryption)
    addr_length_format : int
        ALFI byte: upper nibble = address bytes, lower nibble = size bytes
    memory_address : int
        Start address for download
    memory_size : int
        Number of bytes to download
    """
    addr_bytes = (addr_length_format >> 4) & 0x0F
    size_bytes = addr_length_format & 0x0F

    pdu = bytes([SID_REQUEST_DOWNLOAD, data_format_identifier, addr_length_format])
    pdu += memory_address.to_bytes(addr_bytes, 'big')
    pdu += memory_size.to_bytes(size_bytes, 'big')
    return pdu


def build_transfer_data(block_sequence_number: int, data: bytes) -> bytes:
    """Build TransferData request."""
    return bytes([SID_TRANSFER_DATA, block_sequence_number]) + data


def build_transfer_exit() -> bytes:
    """Build RequestTransferExit request."""
    return bytes([SID_REQUEST_TRANSFER_EXIT])


# ---------------------------------------------------------------------------
# Response Parsers
# ---------------------------------------------------------------------------


def parse_response(data: bytes) -> dict:
    """Parse a UDS response.

    Returns
    -------
    dict with keys:
        - sid: the response SID (request SID + 0x40)
        - positive: True if positive response
        - nrc: negative response code (if positive=False)
        - data: remaining payload bytes after SID
    """
    if not data:
        raise UdsError("빈 응답 데이터")

    sid = data[0]

    if sid == SID_NEGATIVE_RESPONSE:
        if len(data) < 3:
            raise UdsError("Negative Response 데이터 길이 부족")
        request_sid = data[1]
        nrc = data[2]
        return {
            "sid": request_sid,
            "positive": False,
            "nrc": nrc,
            "data": data[3:],
        }

    # Positive response: SID + 0x40
    request_sid = sid & ~PR_SID_MASK if (sid & PR_SID_MASK) else sid
    return {
        "sid": request_sid,
        "positive": True,
        "nrc": None,
        "data": data[1:],
    }


def parse_seed_response(data: bytes) -> bytes:
    """Extract seed bytes from a SecurityAccess positive response."""
    result = parse_response(data)
    if not result["positive"]:
        raise UdsError(f"Seed 요청 실패 (NRC=0x{result['nrc']:02X})", result["nrc"])
    # Response: SID+0x40, accessMode, seed...
    if len(result["data"]) < 1:
        raise UdsError("Seed 응답에 seed 데이터 없음")
    return result["data"][1:]  # skip accessMode byte


def parse_request_download_response(data: bytes) -> dict:
    """Parse RequestDownload positive response.

    Returns
    -------
    dict with:
        - max_length: maximum number of bytes per TransferData block
    """
    result = parse_response(data)
    if not result["positive"]:
        raise UdsError(f"RequestDownload 실패 (NRC=0x{result['nrc']:02X})", result["nrc"])
    if len(result["data"]) < 2:
        raise UdsError("RequestDownload 응답 데이터 길이 부족")
    len_format = result["data"][0]
    max_length_bytes = len_format & 0x0F
    if max_length_bytes == 0:
        max_length = 0  # no limit
    elif len(result["data"]) < 1 + max_length_bytes:
        raise UdsError("RequestDownload 응답에 maxLength 데이터 부족")
    else:
        max_length = int.from_bytes(result["data"][1:1 + max_length_bytes], 'big')
    return {"max_length": max_length}


# ---------------------------------------------------------------------------
# ASK (SeedKey) Integration
# ---------------------------------------------------------------------------


def generate_key(seed: bytes) -> bytes:
    """Generate a key from a seed using the platform-appropriate method.

    On Windows: uses the HKMC AdvancedSeedKey DLL via reference/ask_client.py.
    On macOS/Linux: returns a mock key (all zeros) for development/testing.

    Parameters
    ----------
    seed : bytes
        8-byte seed from the ECU

    Returns
    -------
    bytes
        8-byte key
    """
    if platform.system() == "Windows":
        try:
            from reference.ask_client import AdvancedSeedKeyClient
            client = AdvancedSeedKeyClient()
            return client.generate_key(seed)
        except Exception as exc:
            raise UdsError(f"ASK KeyGenerate 실패: {exc}")
    else:
        # macOS/Linux: mock key for development
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("macOS/Linux: Mock key(zero-filled) 사용 중. 실제 ECU 보안 액세스는 Windows DLL에서만 가능합니다.")
        return b"\x00" * 8


# ---------------------------------------------------------------------------
# Convenience: send UDS request and receive response via ISO-TP
# ---------------------------------------------------------------------------


def send_and_receive(
    isotp_send_fn,
    isotp_receive_fn,
    tx_id: int,
    rx_id: int,
    request: bytes,
    timeout_s: float = 1.0,
    fc_id: Optional[int] = None,
    is_extended_id: bool = False,
) -> dict:
    """Send a UDS request via ISO-TP and receive the response.

    Parameters
    ----------
    isotp_send_fn : callable
        Function to send ISO-TP data (signature: send(tx_id, fc_id, data, ...))
    isotp_receive_fn : callable
        Function to receive ISO-TP data (signature: receive(rx_id, tx_id, ...))
    tx_id : int
        CAN ID to send request on
    rx_id : int
        CAN ID to receive response on
    request : bytes
        UDS request PDU
    timeout_s : float
        Response timeout
    fc_id : int, optional
        Flow Control ID (defaults to rx_id if not given)
    is_extended_id : bool
        Whether to use 29-bit extended IDs

    Returns
    -------
    dict
        Parsed UDS response
    """
    if fc_id is None:
        fc_id = rx_id

    # Send request
    isotp_send_fn(
        tx_id, fc_id, request,
        is_extended_id=is_extended_id,
        fc_timeout_s=timeout_s,
    )

    # Receive response
    response = isotp_receive_fn(
        rx_id, tx_id,
        timeout_s=timeout_s,
        is_extended_id=is_extended_id,
    )

    return parse_response(response)