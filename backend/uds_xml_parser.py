"""UDS Software Download XML Procedure Parser.

Parses the xfrm-format XML files that define the software download procedure
for an ECU. The XML contains:

- Instance information (vehicle, communication params, ROM info, version)
- Processing rule (the actual download sequence: preparation → unit → complete)
- Activate rule (activation after download)
- Version checking rule
- Error rule (fallback on failure)

Each rule is divided into three phases:
  - rule-preparation: session setup, security access
  - rule-unit: the actual download (requestDownload, transferData, ...)
  - rule-complete: session teardown, ECU reset
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

NS = {
    "xfrm": "http://gitauto.com/xfrm/",
    "information": "http://gitauto.com/information/",
    "build": "http://gitauto.com/build/",
}


@dataclass
class UdsStep:
    """A single UDS service step parsed from XML."""
    service: str                # e.g. "diagnosticSessionControl", "securityAccess", ...
    params: dict[str, Any]     # XML attributes for this step
    sub_steps: list["UdsStep"] = field(default_factory=list)  # nested steps (e.g. requestSeed/sendKey under securityAccess)


@dataclass
class UdsRule:
    """A rule section (preparation / unit / complete) containing steps."""
    preparation: list[UdsStep] = field(default_factory=list)
    unit: list[UdsStep] = field(default_factory=list)
    complete: list[UdsStep] = field(default_factory=list)


@dataclass
class UdsProcedure:
    """Complete parsed UDS software download procedure."""
    # Instance information
    vehicle_model: str = ""
    vehicle_system: str = ""
    update_standard: str = ""
    sw_type: str = ""
    execution_type: str = ""
    gate: str = ""
    is_enable_ota: str = ""
    update_type: str = ""
    redundancy: str = ""

    # Communication info
    network: str = ""
    request_id: int = 0
    response_id: int = 0
    data_bit_rate: str = ""
    p6_time: int = 0  # P6 timeout in ms

    # ROM / binary info
    rom_info: str = ""

    # Version info
    part_number: str = ""
    unit: str = ""
    sw_version_source: str = ""
    sw_version_target: str = ""

    # Rules
    processing_rule: UdsRule = field(default_factory=UdsRule)
    activate_rule: UdsRule = field(default_factory=UdsRule)
    version_checking_rule: UdsRule = field(default_factory=UdsRule)
    error_rule: UdsRule = field(default_factory=UdsRule)

    # Configuration (from startCommunication)
    stmin_tx: int = 0x0A
    p2_can_server_max: int = 50
    p2_star_can_server_max: int = 5000
    nrc78_repetition_timeout: int = 300
    background_stmin_tx: int = 0x01


def _int_hex(value: str) -> int:
    """Parse a hex string like '0x0A' to int."""
    if isinstance(value, str) and value.startswith("0x"):
        return int(value, 16)
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _parse_step(element: ET.Element) -> UdsStep:
    """Parse a single step element (e.g. <xfrm:diagnosticSessionControl>)."""
    # Get the local tag name (strip namespace)
    tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag
    params = dict(element.attrib)

    # Parse sub-steps
    sub_steps = []
    for child in element:
        sub_steps.append(_parse_step(child))

    return UdsStep(service=tag, params=params, sub_steps=sub_steps)


def _parse_rule_section(parent: ET.Element) -> UdsRule:
    """Parse a rule section (rule-preparation, rule-unit, rule-complete)."""
    rule = UdsRule()

    for phase_elem in parent:
        phase_tag = phase_elem.tag.split("}")[-1] if "}" in phase_elem.tag else phase_elem.tag

        steps = []
        for child in phase_elem:
            steps.append(_parse_step(child))

        if phase_tag == "rule-preparation":
            rule.preparation = steps
        elif phase_tag == "rule-unit":
            rule.unit = steps
        elif phase_tag == "rule-complete":
            rule.complete = steps

    return rule


def parse_xml(path: str) -> UdsProcedure:
    """Parse a UDS software download XML file.

    Parameters
    ----------
    path : str
        Path to the XML file

    Returns
    -------
    UdsProcedure
        Parsed procedure data
    """
    tree = ET.parse(path)
    root = tree.getroot()

    proc = UdsProcedure()

    # Find the document element
    doc = root.find("xfrm:document", NS)
    if doc is None:
        # Try without namespace
        doc = root.find(".//document")
    if doc is None:
        raise ValueError("XML에 <document> 요소를 찾을 수 없습니다")

    # ---- Instance information ----
    instance = doc.find("xfrm:instance", NS)
    if instance is None:
        instance = doc.find("instance")

    if instance is not None:
        vehicle_info = instance.find("information:vehicleInfo", NS)
        if vehicle_info is not None:
            proc.vehicle_model = vehicle_info.get("model", "")
            proc.vehicle_system = vehicle_info.get("system", "")

        spec_info = instance.find("information:specInfo", NS)
        if spec_info is not None:
            proc.update_standard = spec_info.get("updateStandard", "")

        sw_type_info = instance.find("information:swTypeInfo", NS)
        if sw_type_info is not None:
            proc.sw_type = sw_type_info.get("swType", "")
            proc.execution_type = sw_type_info.get("executionType", "")

        pkg_info = instance.find("information:packageInfo", NS)
        if pkg_info is not None:
            proc.gate = pkg_info.get("gate", "")
            proc.is_enable_ota = pkg_info.get("isEnableOTA", "")
            proc.update_type = pkg_info.get("updateType", "")
            proc.redundancy = pkg_info.get("redundancy", "")

        comm_info = instance.find("information:commInfo", NS)
        if comm_info is not None:
            proc.network = comm_info.get("network", "")
            proc.request_id = _int_hex(comm_info.get("requestId", "0"))
            proc.response_id = _int_hex(comm_info.get("responseId", "0"))
            proc.data_bit_rate = comm_info.get("dataBitRate", "")
            proc.p6_time = _int_hex(comm_info.get("p6Time", "0"))

        rom_info = instance.find("information:romInfo", NS)
        if rom_info is not None and rom_info.text:
            proc.rom_info = rom_info.text.strip()

        version_info = instance.find("information:versionInfo", NS)
        if version_info is not None:
            part_number_elem = version_info.find("information:partNumber", NS)
            if part_number_elem is not None:
                proc.part_number = part_number_elem.get("target", "")

            unit_elem = version_info.find("information:unit", NS)
            if unit_elem is not None:
                proc.unit = unit_elem.get("target", "")
                sw_version = unit_elem.find("information:swVersion", NS)
                if sw_version is not None:
                    proc.sw_version_source = sw_version.get("source", "")
                    proc.sw_version_target = sw_version.get("target", "")

    # ---- Processing rules ----
    processing_rule = doc.find("xfrm:processing-rule", NS)
    if processing_rule is not None:
        proc.processing_rule = _parse_rule_section(processing_rule)

        # Extract configuration from startCommunication
        for phase in (proc.processing_rule.preparation, proc.processing_rule.unit, proc.processing_rule.complete):
            for step in phase:
                if step.service == "startCommunication":
                    cfg = step.sub_steps[0].params if step.sub_steps else {}
                    proc.stmin_tx = _int_hex(cfg.get("stminTx", "0x0A"))
                    proc.p2_can_server_max = int(cfg.get("p2CanServerMax", "50"))
                    proc.p2_star_can_server_max = int(cfg.get("p2StarCanServerMax", "5000"))
                    proc.nrc78_repetition_timeout = int(cfg.get("NRC78Repetitiontimeout", "300"))
                    proc.background_stmin_tx = _int_hex(cfg.get("background_stminTx", "0x01"))

    # ---- Activate rule ----
    activate_rule = doc.find("xfrm:activate-rule", NS)
    if activate_rule is not None:
        proc.activate_rule = _parse_rule_section(activate_rule)

    # ---- Version checking rule ----
    version_checking_rule = doc.find("xfrm:versionChecking-rule", NS)
    if version_checking_rule is not None:
        proc.version_checking_rule = _parse_rule_section(version_checking_rule)

    # ---- Error rule ----
    error_rule = doc.find("xfrm:error-rule", NS)
    if error_rule is not None:
        proc.error_rule = _parse_rule_section(error_rule)

    return proc


def get_step_params(steps: list[UdsStep], service_name: str) -> list[dict]:
    """Find all steps with the given service name and return their params.

    Searches recursively through sub_steps.
    """
    results = []
    for step in steps:
        if step.service == service_name:
            results.append(step.params)
        if step.sub_steps:
            results.extend(get_step_params(step.sub_steps, service_name))
    return results


def find_step(steps: list[UdsStep], service_name: str) -> Optional[UdsStep]:
    """Find the first step with the given service name (recursive)."""
    for step in steps:
        if step.service == service_name:
            return step
        if step.sub_steps:
            result = find_step(step.sub_steps, service_name)
            if result is not None:
                return result
    return None