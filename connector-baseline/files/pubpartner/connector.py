"""Local PubPartner connector. An MCP client launches this module over stdio.

Run from the repository with the standalone PubPartner dependencies and
mcp>=1.29,<2 installed. Configure the client to launch this interpreter with
arguments -m pubpartner.connector and the absolute path to the chosen cartridge.
Selecting the cartridge locally limits which character the client can request.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .cartridge import Cartridge, load_cartridge
from .prompt_assembler import assemble_prompt


def serve(cartridge: Cartridge, transport: str = "stdio", port: int = 8768) -> None:
    server = FastMCP(
        "PubPartner", host="127.0.0.1", port=port, stateless_http=True, json_response=True,
        instructions=(
            "When the user activates this cartridge, call pp_brief for character "
            "and relevant memory before replying. The user can set it aside at any time."
        ),
    )

    if transport == "streamable-http" and os.getenv("PUBPARTNER_LAUNCH_ID"):
        from starlette.responses import PlainTextResponse

        @server.custom_route("/ready", methods=["GET"])
        async def ready(request):
            return PlainTextResponse(os.environ["PUBPARTNER_LAUNCH_ID"])

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def pp_brief(query: str = "") -> str:
        """Get the selected character's voice, tendencies and relevant memories."""
        return assemble_prompt(cartridge, query=query).text

    server.run(transport=transport)


def tunnel_command(source: Path, tunnel_id: str | None) -> list[str]:
    binary = shutil.which("tunnel-client")
    if not binary:
        raise ValueError("Install OpenAI tunnel-client and add it to PATH")
    if not tunnel_id or not (os.getenv("CONTROL_PLANE_API_KEY") or os.getenv("OPENAI_API_KEY")):
        raise ValueError("Configure the tunnel ID and its runtime credential first")
    child = shlex.join([sys.executable, "-m", "pubpartner.connector", str(source)])
    if "\n" in child or "\r" in child:
        raise ValueError("Tunnel command paths must fit on one line")
    command = [binary, "run", "--control-plane.tunnel-id", tunnel_id,
               "--mcp.command", child]
    return command


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cartridge", type=Path)
    parser.add_argument("--transport", choices=("stdio", "http", "openai-tunnel"), default="stdio")
    parser.add_argument("--port", type=int, default=8768, help="Loopback HTTP port")
    parser.add_argument("--tunnel-id", default=os.getenv("CONTROL_PLANE_TUNNEL_ID"))
    args = parser.parse_args()
    source = args.cartridge.resolve()
    if not source.exists():
        parser.error("The selected cartridge does not exist")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if args.transport == "openai-tunnel":
        try:
            command = tunnel_command(source, args.tunnel_id)
        except ValueError as error:
            parser.error(str(error))
        raise SystemExit(subprocess.run(command, check=False).returncode)
    transport = "streamable-http" if args.transport == "http" else "stdio"
    if source.is_file() and source.suffix == ".zip":
        # Own the extracted copy for the lifetime of this client connection.
        with TemporaryDirectory(prefix="pubpartner_connector_") as temp:
            with ZipFile(source) as archive:
                archive.extractall(temp)
            entries = list(Path(temp).iterdir())
            root = entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(temp)
            with load_cartridge(root) as cartridge:
                serve(cartridge, transport, args.port)
    else:
        with load_cartridge(source) as cartridge:
            serve(cartridge, transport, args.port)


if __name__ == "__main__":
    main()
