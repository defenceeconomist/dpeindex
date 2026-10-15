"""Launch from any working directory using the environment containing this Python."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mcp_server import server

server.run(transport='stdio')
