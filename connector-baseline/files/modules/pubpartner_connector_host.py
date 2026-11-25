"""Owner-operated, fixed-target MCP launcher for a running Reach host."""
import asyncio
import io
import json
import tempfile
import zipfile

import sqlite3
from importlib.util import find_spec
import os
from pathlib import Path
import shutil
import sys
import secrets

import httpx


class ConnectorHost:
    def __init__(self, data_dir):
        self.selected = Path(data_dir) / "secure" / "connector" / "selected.zip"
        self.tunnel_record = self.selected.with_name("workspace-tunnel.json")
        self.process = None
        self.route = None
        self.starting = False
        self.lock = asyncio.Lock()

    def source(self):
        if self.selected.is_file():
            return self.selected
        configured = os.getenv("PUBPARTNER_CONNECTOR_CARTRIDGE")
        return Path(configured) if configured else None

    async def select(self, payload):
        async with self.lock:
            if self.status()["running"]:
                raise ValueError("Stop the running connector before selecting another cartridge.")
            await asyncio.to_thread(self.validate, payload)
            self.selected.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.selected.parent, delete=False) as tmp:
                tmp.write(payload)
            try:
                os.replace(tmp.name, self.selected)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
            return self.status()

    @staticmethod
    def validate(payload):
        from pubpartner.cartridge import load_cartridge
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            files = [entry for entry in entries if not entry.is_dir()]
            if len(entries) > 129 or sum(entry.file_size for entry in entries) > 16 * 1024 * 1024:
                raise ValueError("Choose a cartridge up to 16 MiB unpacked.")
            parents = {Path(entry.filename).parent for entry in files}
            if len(parents) != 1:
                raise ValueError("Choose one folder of cartridge files.")
            folder = parents.pop()
            if (len(folder.parts) > 1 or len({entry.filename for entry in entries}) != len(entries)
                    or any(".." in entry.filename or "\\" in entry.filename or ":" in entry.filename
                           or entry.filename.startswith("/") or (entry.is_dir() and Path(entry.filename) != folder)
                           for entry in entries)):
                raise ValueError("Choose a cartridge with at most one outer folder.")
            with tempfile.TemporaryDirectory() as scratch:
                archive.extractall(scratch)
                try:
                    with load_cartridge(Path(scratch) / folder) as cartridge:
                        cartridge.memory.count()
                except sqlite3.Error:
                    raise ValueError("The cartridge memory database could not be read.") from None

    def tunnel_id(self):
        configured = os.getenv("CONTROL_PLANE_TUNNEL_ID")
        if configured:
            return configured
        if self.tunnel_record.is_file():
            try:
                record = json.loads(self.tunnel_record.read_text())
            except (UnicodeError, ValueError, OSError):
                return None
            if not isinstance(record, dict):
                return None
            if record.get("workspace") == os.getenv("PUBPARTNER_OPENAI_WORKSPACE_ID"):
                return record.get("id")
        return None

    async def create_tunnel(self):
        # Reserve creation before the external call. A lost response must not
        # create a second workspace resource when the owner retries.
        workspace = os.environ["PUBPARTNER_OPENAI_WORKSPACE_ID"]
        self.tunnel_record.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.tunnel_record.open("x") as handle:
                json.dump({"workspace": workspace}, handle)
        except FileExistsError:
            raise ValueError("Check the workspace tunnel list and set CONTROL_PLANE_TUNNEL_ID to resume.") from None
        proc = await asyncio.create_subprocess_exec(
            shutil.which("tunnel-client"), "admin", "tunnels", "create", "--name", "PubPartner",
            "--description", "Owner-selected PubPartner cartridge", "--workspace-id", workspace, "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        output, _ = await proc.communicate()
        if proc.returncode:
            raise ValueError("Creation was not confirmed. Check the workspace tunnel list before retrying.")
        tunnel_id = json.loads(output).get("id")
        if not isinstance(tunnel_id, str) or not tunnel_id:
            raise ValueError("Check the workspace tunnel list for the new tunnel ID.")
        with tempfile.NamedTemporaryFile(mode="w", dir=self.tunnel_record.parent, delete=False) as tmp:
            json.dump({"workspace": workspace, "id": tunnel_id}, tmp)
        os.replace(tmp.name, self.tunnel_record)
        return tunnel_id

    def status(self):
        source = self.source()
        tunnel_id = self.tunnel_id()
        return {
            "cartridge_configured": bool(source and Path(source).exists()),
            "sdk_installed": find_spec("mcp") is not None,
            "tunnel_installed": bool(shutil.which("tunnel-client")),
            "can_create_tunnel": bool(not self.tunnel_record.exists() and os.getenv("OPENAI_ADMIN_KEY")
                                     and os.getenv("PUBPARTNER_OPENAI_WORKSPACE_ID")
                                     and (os.getenv("CONTROL_PLANE_API_KEY") or os.getenv("OPENAI_API_KEY"))),
            "tunnel_id": tunnel_id,
            "tunnel_needs_recovery": self.tunnel_record.exists() and not tunnel_id,
            "tunnel_configured": bool(tunnel_id and
                                      (os.getenv("CONTROL_PLANE_API_KEY") or os.getenv("OPENAI_API_KEY"))),
            "starting": self.starting,
            "running": not self.starting and self.process is not None and self.process.returncode is None,
            "route": self.route,
            "exit_code": self.process.returncode if self.process else None,
        }

    async def start(self, route, create=False):
        async with self.lock:
            status = self.status()
            if status["running"]:
                if self.route == route:
                    return status
                raise ValueError("Stop the current connector before switching routes.")
            if not status["sdk_installed"] or not status["cartridge_configured"]:
                raise ValueError("Configure the host cartridge and install the connector SDK.")
            source = self.source().resolve()
            if route == "http":
                command = [sys.executable, "-m", "pubpartner.connector", str(source), "--transport", "http"]
            elif route == "openai-tunnel":
                from pubpartner.connector import tunnel_command
                tunnel_id = self.tunnel_id()
                if not tunnel_id and create and status["tunnel_installed"] and status["can_create_tunnel"]:
                    tunnel_id = await self.create_tunnel()
                command = tunnel_command(source, tunnel_id)
            else:
                raise ValueError("Unknown connector route")
            launch_id = secrets.token_hex(16)
            self.process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.DEVNULL, env={**os.environ, "PUBPARTNER_LAUNCH_ID": launch_id})
            self.route = route
            if route == "http":
                self.starting = True
                try:
                    async with asyncio.timeout(25), httpx.AsyncClient(timeout=.5, trust_env=False) as client:
                        while self.process.returncode is None:
                            try:
                                response = await client.get("http://127.0.0.1:8768/ready")
                                if response.status_code == 200 and response.text == launch_id:
                                    self.starting = False
                                    return self.status()
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(.1)
                        raise ValueError("Local MCP exited before startup. Check the cartridge and whether port 8768 is in use.")
                except (asyncio.CancelledError, TimeoutError) as error:
                    await self._stop_process()
                    if isinstance(error, TimeoutError):
                        raise ValueError("Local MCP did not become ready on port 8768.") from None
                    raise
                finally:
                    self.starting = False
            return self.status()

    async def stop(self):
        async with self.lock:
            await self._stop_process()
            return self.status()

    async def _stop_process(self):
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 8)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            except ProcessLookupError:
                pass
        self.process = None
        self.route = None
