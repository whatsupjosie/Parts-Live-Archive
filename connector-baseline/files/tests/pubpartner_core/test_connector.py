"""Real subprocess and MCP handshake with the standalone connector extra."""
import asyncio
import hashlib
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from pubpartner.cartridge import init_cartridge


async def exchange(source, query):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "pubpartner.connector", str(source)]
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        info = await session.initialize()
        assert "activates" in info.instructions
        assert [tool.name for tool in (await session.list_tools()).tools] == ["pp_brief"]
        result = await session.call_tool("pp_brief", {"query": query})
        assert not result.isError
        return result.content[0].text


def test_connector_exchange():
    import pytest
    pytest.importorskip("mcp.server.fastmcp", reason="requires the optional connector SDK")
    with TemporaryDirectory() as temp:
        root = init_cartridge(Path(temp) / "character", "Studio Guest",
                              "Enjoys a rehearsal.", "Measured", "Plain")
        (root / "memories.jsonl").write_text(
            '{"text":"The rehearsal is on Tuesday.","memory_type":"project"}\n',
            encoding="utf-8",
        )
        archive = Path(temp) / "character.zip"
        with ZipFile(archive, "w") as output:
            for member in root.iterdir():
                output.write(member, "character/" + member.name)
        digest = hashlib.sha256(archive.read_bytes()).digest()
        for source in (root, archive):
            brief = asyncio.run(exchange(source, "rehearsal"))
            assert "# Studio Guest" in brief and "Measured" in brief
            assert "The rehearsal is on Tuesday." in brief
        assert hashlib.sha256(archive.read_bytes()).digest() == digest
