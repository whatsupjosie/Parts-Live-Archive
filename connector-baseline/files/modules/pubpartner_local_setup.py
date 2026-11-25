"""Discover an installed local Ollama chat model or fetch the small starter."""
import asyncio
import os
import shutil
from pathlib import Path
import httpx

HOST = "http://127.0.0.1:11434"
STARTER = "smollm2:360m-instruct-q4_K_M"


def ollama_binary():
    executable = shutil.which("ollama")
    if executable:
        return executable
    local = os.getenv("LOCALAPPDATA")
    candidates = [Path("/Applications/Ollama.app/Contents/Resources/ollama")]
    if os.name == "nt" and local:
        candidates.insert(0, Path(local) / "Programs" / "Ollama" / "ollama.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


class LocalOllama:
    """Supervise just the process Reach starts. Leave an existing service alone."""
    def __init__(self):
        self.process = None
        self.lock = asyncio.Lock()
        self.download = None

    async def start(self):
        async with self.lock:
            async with httpx.AsyncClient(base_url=HOST, timeout=1, trust_env=False) as client:
                for attempt in range(40):
                    try:
                        res = await client.get("/api/tags")
                        if res.is_success and isinstance(res.json().get("models"), list):
                            return
                    except (httpx.HTTPError, ValueError, AttributeError):
                        pass
                    if attempt == 0 and (self.process is None or self.process.returncode is not None):
                        executable = ollama_binary()
                        if not executable:
                            raise ValueError("Install Ollama on the PubPartner computer first.")
                        self.process = await asyncio.create_subprocess_exec(
                            executable, "serve", env={**os.environ, "OLLAMA_HOST":"127.0.0.1:11434", "OLLAMA_NO_CLOUD":"1"},
                            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.sleep(.25)
                raise ValueError("Ollama did not become ready. Open Ollama and check again.")

    async def install_starter(self):
        # A browser retry joins the current pull, even if the first request was cancelled.
        if self.download is None or self.download.done():
            self.download = asyncio.create_task(download_starter())
            self.download.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        await asyncio.shield(self.download)

    async def stop(self):
        if self.download is not None and not self.download.done():
            self.download.cancel()
            await asyncio.gather(self.download, return_exceptions=True)
        async with self.lock:
            if self.process and self.process.returncode is None:
                try:
                    self.process.terminate()
                    await asyncio.wait_for(self.process.wait(), 5)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                except ProcessLookupError:
                    pass
            self.process = None


async def discover():
    from .local_model_config import configured_url, default_provider
    launcher_issue = ""
    async with httpx.AsyncClient(base_url=HOST, timeout=3, trust_env=False) as client:
        try:
            launcher = configured_url()
            if launcher:
                from .local_model_config import local_api_key
                health = await client.get(launcher.removesuffix("/v1") + "/health",
                                          headers={"Authorization":"Bearer " + local_api_key(launcher)})
                health.raise_for_status()
                if health.json().get("status") == "ok":
                    return {"choices": [default_provider()], "ollama": False}
                launcher_issue = "The configured local launcher is still starting."
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            launcher_issue = "The configured local launcher is unavailable."
        try:
            tags = await client.get("/api/tags")
            tags.raise_for_status()
            rows = tags.json()["models"]
            if not isinstance(rows, list):
                raise ValueError("Invalid Ollama model list")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"choices": [], "ollama": False, "can_start": bool(ollama_binary()), "notice": launcher_issue}
        semaphore = asyncio.Semaphore(4)

        failed = []

        async def chat_model(row):
            name = row.get("name")
            if not isinstance(name, str) or not name or row.get("remote_model") or row.get("remote_host"):
                return None
            async with semaphore:
                try:
                    reply = await client.post("/api/show", json={"model": name})
                    reply.raise_for_status()
                    detail = reply.json()
                    if not isinstance(detail.get("capabilities"), list):
                        raise ValueError("Missing model capabilities")
                    if (detail.get("remote_model") or detail.get("remote_host") or name.endswith("-cloud")
                            or "completion" not in detail.get("capabilities", [])):
                        return None
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    failed.append(name)
                    return None
            return {"provider":"ollama", "model":name, "base_url":HOST}

        choices = await asyncio.gather(*(chat_model(row) for row in rows if isinstance(row, dict)))
        result = {"choices": [choice for choice in choices if choice], "ollama": True}
        if failed or any(not isinstance(row, dict) or not row.get("name") for row in rows):
            result["incomplete"] = True
        if launcher_issue:
            result["notice"] = launcher_issue
        return result


async def download_starter():
    async with httpx.AsyncClient(base_url=HOST, timeout=900, trust_env=False) as client:
        response = await client.post("/api/pull", json={"model": STARTER, "stream": False})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("status") != "success":
            raise ValueError("Download incomplete")
