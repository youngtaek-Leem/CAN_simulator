"""Unit tests for xlsx_to_script.py's CANStart/CANStop rows (no workbook needed)."""

import pytest

from xlsx_to_script import _build_step, _value_str, ScriptError


def test_canstart_bare_row_needs_no_columns():
    assert _build_step(3, "CANStart", None, None, None) == {"type": "CANStart"}


def test_canstart_with_rxnode_in_d_column():
    assert _build_step(4, "CANStart", "ECU_A", None, None) == {
        "type": "CANStart",
        "RxNode": "ECU_A",
    }


def test_canstop_row_needs_no_columns():
    assert _build_step(5, "CANStop", None, None, None) == {"type": "CANStop"}


def test_unknown_step_type_still_rejected():
    with pytest.raises(ScriptError):
        _build_step(6, "Bogus", None, None, None)


def test_value_str_keeps_hex_but_normalizes_numeric_cells():
    assert _value_str("0x01") == "0x01"
    assert _value_str(" 0x01 ") == "0x01"
    assert _value_str(10) == "10"  # 엑셀 숫자셀 → 10진수 "10"
    assert _value_str(10.0) == "10"
    assert _build_step(7, "CANReq", "M", "S", 10)["Value"] == "10"
