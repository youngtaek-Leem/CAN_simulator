"""CAN Simulator MCP stdio entry (Claude Desktop / Claude Code local).

Usage in claude_desktop_config.json / .mcp.json:
  {
    "mcpServers": {
      "can-simulator": {
        "command": "/abs/path/CAN_simulator/backend/.venv/bin/python",
        "args": ["/abs/path/CAN_simulator/backend/mcp_stdio.py"]
      }
    }
  }

Note: stdio mode runs as its OWN process, so live CAN-bus state is NOT
shared with the uvicorn backend. Prefer the Streamable HTTP mode
(http://127.0.0.1:8000/mcp, served by the backend itself) for any
hardware TX/RX work; use stdio for stateless jobs (DBC inspect, script
drafting). Destructive tools still require confirm=True in both modes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mcp_server
from mcp_server import bind_services, mcp


def _bind_standalone() -> None:
    """Minimal virtual-bus binding (same set as mcp_server __main__)."""
    import isotp_service as _isotp
    from audio_service import AudioService
    from can_log_service import CanLogService
    from can_manager import CanManager
    from dbc_service import DbcService
    from log_service import LogService
    from ota_tester_download_manager import OtaTesterDownloadManager
    from power_supply_service import PowerSupplyService
    from replay_service import ReplayService
    from seedkey_client import SeedKeyService
    from syslog_service import SysLogService
    from syslog_upload_manager import SysLogUploadManager
    from test_runner_service import TestRunnerService
    from tx_scheduler import TxScheduler
    from uds_download_manager import MultiUdsDownloadManager

    base = Path(__file__).resolve().parent
    can = CanManager()
    dbc = DbcService()
    tx = TxScheduler(can, dbc)
    replay = ReplayService(can)
    seed = SeedKeyService()
    bind_services(
        can_manager=can, dbc_service=dbc, tx_scheduler=tx,
        replay_service=replay,
        log_service=LogService(can, base / "can_logs"),
        power_supply_service=PowerSupplyService(),
        audio_service=AudioService(
            base / "uploads" / "testrunner_audio",
            base / "uploads" / "testrunner_golden"),
        syslog_service=SysLogService(), can_log_service=CanLogService(dbc),
        test_runner_service=TestRunnerService(
            can, dbc, tx, replay,
            base / "uploads" / "testrunner_logs",
            base / "testrunner_results"),
        seedkey_service=seed,
        uds_download_manager=MultiUdsDownloadManager(
            can, _isotp.send, _isotp.receive, seed, log_dir=base / "can_logs"),
        ota_tester_manager=OtaTesterDownloadManager(
            can, _isotp.send, _isotp.receive, seed, log_dir=base / "can_logs"),
        syslog_upload_manager=SysLogUploadManager(
            can, _isotp.send, _isotp.receive, seed, log_dir=base / "can_logs"),
        settings={"ws_flush_ms": 30}, run_state={"running": True},
        layout_dir=base / "layouts", base_dir=base,
    )


if not mcp_server._SERVICES:
    _bind_standalone()

if __name__ == "__main__":
    mcp.run(transport="stdio")
