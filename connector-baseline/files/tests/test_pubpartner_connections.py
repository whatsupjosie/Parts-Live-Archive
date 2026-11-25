"""Connection setup acceptance using real Reach and Chromium, plus simulated OpenAI consent."""
import asyncio
import base64
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from modules import pubpartner_chatgpt_link as link
from modules.pubpartner_provider import PubPartnerProviderDispatcher
from tests.test_pubpartner_menus_browser import reach


@pytest.mark.parametrize("grant,model_list", [(True, True), (True, False), (False, False)])
def test_consent_and_plan_turn(monkeypatch, grant, model_list):
    session = {"authenticated": True, "payload": {"jti": "session-one"}}
    other = {"authenticated": True, "payload": {"jti": "session-two"}}
    monkeypatch.setenv("PUBPARTNER_OPENAI_CLIENT_ID", "example-client")
    monkeypatch.setenv("PUBPARTNER_OPENAI_CALLBACK", "http://127.0.0.1:8000/pubpartner")
    secret = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(secret.public_key()))
    jwk["kid"] = "test"
    params = {}
    original = httpx.AsyncClient

    def upstream(req):
        if req.url.path.endswith("openid-configuration"):
            return httpx.Response(200,json=dict(issuer=link.ISSUER,authorization_endpoint=link.ISSUER+"/authorize",
                token_endpoint=link.ISSUER+"/token",jwks_uri=link.ISSUER+"/jwks"))
        if req.url.path == "/token":
            fields = parse_qs(req.content.decode())
            challenge = base64.urlsafe_b64encode(hashlib.sha256(fields["code_verifier"][0].encode()).digest()).decode().rstrip("=")
            assert challenge == params["code_challenge"][0]
            claims = dict(sub="sample",iat=int(time.time()),exp=int(time.time())+120,nonce=params["nonce"][0],
                          aud="example-client",iss=link.ISSUER,email="tester@example.com")
            return httpx.Response(200,json=dict(access_token="test-access",expires_in=120,scope=link.PLAN_SCOPE if grant else "openid",
                id_token=jwt.encode(claims,secret,algorithm="RS256",headers={"kid":"test"})))
        if req.url.path == "/jwks":
            return httpx.Response(200,json={"keys":[jwk]})
        assert req.headers["Authorization"] == "Bearer test-access"
        if req.url.path == "/v1/models":
            assert grant
            return httpx.Response(200,json={"models":[{"slug":"test-plan","visibility":"list"}] if model_list else []})
        assert req.url.path == "/v1/responses"
        body = json.loads(req.content)
        assert body["store"] is False and body["stream"] is True
        return httpx.Response(200,text='data: {"type":"response.output_text.delta","delta":"hello"}\n\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n')

    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw: original(transport=httpx.MockTransport(upstream),**kw))
    async def exercise():
        params.update(parse_qs(urlsplit((await link.begin(session, host_id="test-host", origin="http://127.0.0.1:8000"))["url"]).query))
        assert params["ext_agent_host_id"][0].startswith("urn:uuid:")
        callback = {"state":params["state"][0],"code":"test-code"}
        with pytest.raises(Exception, match="another session"):
            await link.finish(other,callback)
        result = await link.finish(session,callback)
        assert result["linked"] and "test-access" not in json.dumps(result)
        with pytest.raises(Exception, match="expired"):
            await link.finish(session,callback)
        token = link.current_session.set("session-one")
        try:
            answer = await PubPartnerProviderDispatcher().dispatch({},dict(provider="chatgpt_plan",model="test-plan"))
            assert result["plan_enabled"] is grant
            assert bool(result["models"]) is model_list
            if model_list:
                assert answer.ok and answer.raw_response == "hello"
            else:
                assert not answer.ok
        finally:
            link.current_session.reset(token)
            link.accounts.pop("session-one", None)
    asyncio.run(exercise())


def test_connection_screen_and_saved_key(reach, tmp_path):
    from playwright.sync_api import sync_playwright
    base, token = reach
    headers = {"Authorization": "Bearer "+token}
    resp = httpx.post(base+"/api/pubpartner/providers/credentials",headers=headers,
                      json=dict(provider="xai",model="test-grok",api_key="test-only-key"))
    assert resp.status_code == 200
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width":390,"height":844})
        ctx.add_init_script("sessionStorage.setItem('pubcast_token',"+json.dumps(token)+");sessionStorage.setItem('pubcast_user',JSON.stringify({username:'josie-check',role:'owner'}));")
        page = ctx.new_page()
        page.goto(base+"/byok")
        page.get_by_role("link",name="AI connections").click()
        dialog = page.get_by_role("dialog",name="AI connections")
        dialog.wait_for()
        page.locator("#connectProvider").select_option("xai")
        page.wait_for_function("document.querySelector('#connectionModel').value === 'test-grok'")
        assert not page.locator("#connectionModel").is_editable()
        assert not page.locator("#connectionKey").is_visible()
        page.get_by_role("button", name="Use connection",exact=True).click()
        page.wait_for_function("!document.querySelector('#connections').open")
        assert page.locator("#provider").input_value() == "xai"
        assert page.locator("#providerCredential").input_value() == resp.json()["credential_model_id"]
        page.get_by_role("button",name="Connections",exact=True).click()
        page.locator("#connections summary").click()
        archive = tmp_path / "guest.zip"
        with ZipFile(archive,"w") as z:
            z.writestr("character.yaml","name: Sample\nsummary: A test guest\n")
            z.writestr("voice_profile.yaml","cadence: calm\nvocabulary: plain\n")
        page.locator("#connectorCartridge").set_input_files(archive)
        page.wait_for_function("document.querySelector('#connectorInfo').textContent.includes('Ready to start')")
        page.locator("#connectorToggle").click()
        page.wait_for_function("document.querySelector('#connectorToggle').textContent === 'Stop connector'")
        async def get_brief():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            for _ in range(50):
                try:
                    async with httpx.AsyncClient(trust_env=False) as client:
                        await client.get("http://127.0.0.1:8768/mcp")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(.1)
            async with streamablehttp_client("http://127.0.0.1:8768/mcp") as (r,w,_), ClientSession(r,w) as session:
                await session.initialize()
                assert "# Sample" in (await session.call_tool("pp_brief",{})).content[0].text
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(asyncio.run, get_brief()).result(timeout=25)
        page.locator("#connectProvider").select_option("claude_web")
        page.locator("#prepareClaude").click()
        page.locator("#claudeBrief").wait_for()
        assert "# Sample" in page.locator("#claudeBrief").input_value()
        page.locator("#connectProvider").select_option("claude_desktop")
        with page.expect_download() as download:
            page.locator("#setupClaude").click()
        bundle = tmp_path / "PubPartner.mcpb"
        download.value.save_as(bundle)
        with ZipFile(bundle) as extension:
            extension.extractall(tmp_path / "desktop")
        async def desktop_brief():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            server = StdioServerParameters(command="node", args=[str(tmp_path / "desktop" / "server.js")])
            async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                assert "# Sample" in (await session.call_tool("pp_brief", {})).content[0].text
        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(asyncio.run, desktop_brief()).result(timeout=25)
        page.locator("#connectorToggle").click()
        page.wait_for_function("document.querySelector('#connectorToggle').textContent === 'Start connector'")
        browser.close()


def test_workspace_tunnel_creation(tmp_path, monkeypatch):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from modules.pubpartner_connector_host import ConnectorHost

    if not shutil.which("tunnel-client"):
        pytest.skip("Optional OpenAI tunnel client unavailable")
    received = []

    class Workspace(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id":"tunnel_test","name":"PubPartner"}')

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Workspace) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("CONTROL_PLANE_BASE_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-admin-synthetic")
        monkeypatch.setenv("PUBPARTNER_OPENAI_WORKSPACE_ID", "workspace_test")
        monkeypatch.delenv("CONTROL_PLANE_TUNNEL_ID", raising=False)
        try:
            host = ConnectorHost(tmp_path)
            assert asyncio.run(host.create_tunnel()) == "tunnel_test"
            assert ConnectorHost(tmp_path).tunnel_id() == "tunnel_test"
            with pytest.raises(ValueError, match="resume"):
                asyncio.run(host.create_tunnel())
            assert len(received) == 1 and received[0][1]["name"] == "PubPartner"
        finally:
            server.shutdown()
            thread.join()


def test_disconnect_wins_during_consent(monkeypatch):
    session = {"authenticated": True, "payload": {"jti": "disconnect-test"}}
    params = {}
    monkeypatch.setenv("PUBPARTNER_OPENAI_CLIENT_ID", "client")
    monkeypatch.setenv("PUBPARTNER_OPENAI_CALLBACK", "http://127.0.0.1:8000/pubpartner")
    secret = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(secret.public_key()))
    jwk["kid"] = "test"
    original = httpx.AsyncClient

    async def exercise():
        reading_models, resume = asyncio.Event(), asyncio.Event()

        async def upstream(req):
            if req.url.path.endswith("openid-configuration"):
                return httpx.Response(200, json=dict(issuer=link.ISSUER, authorization_endpoint=link.ISSUER+"/authorize",
                    token_endpoint=link.ISSUER+"/token", jwks_uri=link.ISSUER+"/jwks"))
            if req.url.path == "/token":
                now = int(time.time())
                claims = dict(sub="u", iat=now, exp=now+60, nonce=params["nonce"][0], aud="client", iss=link.ISSUER)
                return httpx.Response(200, json=dict(access_token="test", expires_in=60, scope=link.PLAN_SCOPE,
                    id_token=jwt.encode(claims, secret, algorithm="RS256", headers={"kid":"test"})))
            if req.url.path == "/jwks":
                return httpx.Response(200, json={"keys":[jwk]})
            reading_models.set()
            await resume.wait()
            return httpx.Response(200, json={"models":[{"slug":"test","visibility":"list"}]})

        from modules.pubpartner_routes import chatgpt_disconnect
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(upstream), **kw))
        params.update(parse_qs(urlsplit((await link.begin(session, host_id="test-host", origin="http://127.0.0.1:8000"))["url"]).query))
        task = asyncio.create_task(link.finish(session, {"state":params["state"][0], "code":"sample"}))
        await asyncio.wait_for(reading_models.wait(), 3)
        await chatgpt_disconnect(identity=session)
        resume.set()
        await asyncio.gather(task, return_exceptions=True)
        assert not link.status("disconnect-test")["linked"]
    try:
        asyncio.run(exercise())
    finally:
        link.accounts.pop("disconnect-test", None)
        link.pending.pop("disconnect-test", None)


def test_callback_credentials_do_not_enter_reach_log(reach, tmp_path_factory):
    base, _ = reach
    # A synthetic authorization callback through the production launcher.
    marker = "synthetic-callback-value-for-log-check"
    response = httpx.get(base + "/pubpartner", params={"state":"synthetic-state", "code":marker})
    assert response.status_code == 200
    logs = list(tmp_path_factory.getbasetemp().glob("pp_menus*/reach.log"))
    assert logs
    for log in logs:
        content = log.read_text()
        assert "/health" in content
        assert marker not in content


def test_wrong_callback_origin_is_rejected_before_sign_in(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv("PUBPARTNER_OPENAI_CLIENT_ID", "sample")
    monkeypatch.setenv("PUBPARTNER_OPENAI_CALLBACK", "http://127.0.0.1:8000/pubpartner")
    with pytest.raises(HTTPException, match="same address"):
        asyncio.run(link.begin({"authenticated":True, "payload":{"jti":"test"}},
                               host_id="local-test", origin="http://localhost:8000"))


def test_interrupted_tunnel_record_keeps_local_controls_available(tmp_path, monkeypatch):
    from modules.pubpartner_connector_host import ConnectorHost
    monkeypatch.delenv("CONTROL_PLANE_TUNNEL_ID", raising=False)
    host = ConnectorHost(tmp_path)
    host.tunnel_record.parent.mkdir(parents=True)
    host.tunnel_record.write_text("{")
    state = asyncio.run(host.stop())
    assert state["tunnel_needs_recovery"] and not state["can_create_tunnel"]
    assert not state["running"]


@pytest.mark.parametrize("provider,variable,host,header", [
    ("xai","XAI_API_KEY","api.x.ai","authorization"),
    ("openai","OPENAI_API_KEY","api.openai.com","authorization"),
    ("anthropic","ANTHROPIC_API_KEY","api.anthropic.com","x-api-key"),
    ("gemini","GEMINI_API_KEY","generativelanguage.googleapis.com","x-goog-api-key"),
])
@pytest.mark.parametrize("accepted", [True, False])
def test_hosted_provider_selection(monkeypatch, provider, variable, host, header, accepted):
    original = httpx.AsyncClient
    key = "synthetic-provider-key"
    monkeypatch.setenv(variable, key)
    calls = []

    def upstream(request):
        calls.append(request.url.host)
        assert request.headers[header] == ("Bearer " + key if header == "authorization" else key)
        assert "current-turn" in request.content.decode()
        if not accepted:
            return httpx.Response(401, text="rejected " + key)
        if provider == "anthropic":
            return httpx.Response(200, json={"stop_reason":"end_turn","content":[{"type":"text","text":"hello"}]})
        if provider == "gemini":
            return httpx.Response(200, json={"candidates":[{"finishReason":"STOP","content":{"parts":[{"text":"hello"}]}}]})
        if provider == "xai":
            payload = {"choices":[{"delta":{"content":"hello"}, "finish_reason":"stop"}]}
            return httpx.Response(200, text="data: "+json.dumps(payload)+"\n\ndata: [DONE]\n\n")
        return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"hello"}\n\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n')

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs))
    result = asyncio.run(PubPartnerProviderDispatcher().dispatch(
        {"llm_prompt_text":"current-turn"}, {"provider":provider,"model":"synthetic-model"}))
    assert calls == [host]
    assert result.ok is accepted
    assert result.raw_response == ("hello" if accepted else "")
    assert key not in json.dumps(result.as_dict())


def test_bad_memory_does_not_replace_selected_cartridge(tmp_path):
    from io import BytesIO
    from modules.pubpartner_connector_host import ConnectorHost
    host = ConnectorHost(tmp_path)

    def archive(memories=None):
        data = BytesIO()
        with ZipFile(data, "w") as z:
            z.writestr("character.yaml", "name: Sample\nsummary: Test\n")
            z.writestr("voice_profile.yaml", "cadence: calm\nvocabulary: plain\n")
            if memories:
                z.writestr("memories.jsonl", memories)
        return data.getvalue()

    original = archive()
    asyncio.run(host.select(original))
    with pytest.raises(ValueError):
        asyncio.run(host.select(archive("incomplete record")))
    assert host.selected.read_bytes() == original


def test_cancelled_callback_keeps_retry_control(reach):
    from playwright.sync_api import sync_playwright
    base, token = reach
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width":390,"height":844})
        page.add_init_script("sessionStorage.setItem('pubcast_token',"+json.dumps(token)+")")
        page.route("**/api/pubpartner/chatgpt/account", lambda route: route.fulfill(
            json={"configured":True, "linked":False, "models":[]}))
        page.goto(base+"/pubpartner?state=synthetic&error=access_denied")
        page.locator("#connectionError").wait_for()
        page.locator("#connectAccount").wait_for()
        assert urlsplit(page.url).query == ""
        page.get_by_role("button",name="Close connections").click()
        assert page.locator("#connectionError").is_hidden()
        browser.close()


def test_local_discovery_filters_cloud_and_embedding_models(monkeypatch):
    from modules.pubpartner_local_setup import discover, download_starter, STARTER
    monkeypatch.delenv("PUBCAST_LOCAL_MODEL_URL", raising=False)
    original = httpx.AsyncClient
    def upstream(request):
        assert request.url.host == "127.0.0.1"
        if request.url.path == "/api/tags":
            return httpx.Response(200,json={"models":[{"name":name} for name in ("chat","embed","remote")]})
        if request.url.path == "/api/pull":
            assert json.loads(request.content) == {"model":STARTER,"stream":False}
            return httpx.Response(200,json={"status":"success"})
        name = json.loads(request.content)["model"]
        data = {"capabilities":["embedding" if name == "embed" else "completion"]}
        if name == "remote":
            data["remote_host"] = "https://example.invalid"
        return httpx.Response(200,json=data)
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kw: original(transport=httpx.MockTransport(upstream),**kw))
    result = asyncio.run(discover())
    assert [item["model"] for item in result["choices"]] == ["chat"]
    asyncio.run(download_starter())


def test_choose_detected_local_model_without_typing(reach):
    from playwright.sync_api import sync_playwright
    base, token = reach
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width":390,"height":844})
        page.add_init_script("sessionStorage.setItem('pubcast_token',"+json.dumps(token)+")")
        page.route("**/api/pubpartner/providers/local-discovery", lambda route: route.fulfill(json={
            "choices":[{"provider":"ollama","model":"my-local-model","base_url":"http://127.0.0.1:11434"}],"ollama":True}))
        page.goto(base+"/pubpartner#connections")
        page.locator("#connectProvider").select_option("local")
        page.locator("#applyConnection").click()
        assert page.locator("#provider").input_value() == "ollama"
        assert page.locator("#providerHost").input_value() == "http://127.0.0.1:11434"
        assert page.locator("#modelName").input_value() == "my-local-model"
        assert not page.locator("#allowFallback").is_checked()
        browser.close()


def test_starter_prompt_uses_scoped_text_and_preserves_wording():
    from modules.pubpartner_provider import _starter_prompt
    message = "Keep both lines.\n  Keep these spaces."
    packet = {"f":{"name":"Test","bound":["respect privacy"]},"msg":{"text":message},
              "vessel":{"claims":["scoped memory"],"prompt_text":"duplicated diagnostic context"}}
    prompt = _starter_prompt({"llm_prompt_json":json.dumps(packet)}, "full")
    assert prompt.endswith(message) and "respect privacy" in prompt and "scoped memory" in prompt
    assert "duplicated diagnostic context" not in prompt
    del packet["msg"]["text"]
    assert _starter_prompt({"llm_prompt_json":json.dumps(packet)}, "reference-only") == "reference-only"


def test_small_model_keeps_identity_and_conversational_direction():
    from modules.pubpartner_provider import _starter_prompt
    packet = {"msg":{"text":"hello"}, "f":{"identity":"retained identity", "modes":{"a":"retained mode"}},
              "jeremy":{"whisper":"retained direction"}, "alex":{"do_not_touch":["retained boundary"]}}
    prompt = _starter_prompt({"llm_prompt_json":json.dumps(packet)}, "full")
    for item in ("retained identity","retained mode","retained direction","retained boundary"):
        assert item in prompt


def test_cartridge_brief_keeps_spoken_voice_preference(tmp_path):
    from pubpartner.cartridge import init_cartridge, load_cartridge
    from pubpartner.prompt_assembler import assemble_prompt
    import yaml
    init_cartridge(tmp_path,"Example","Example friend","gentle","simple")
    voice = tmp_path / "voice_profile.yaml"
    data = yaml.safe_load(voice.read_text())
    data["spoken_voice_preference"] = {"preset_name":"test preset","register":"lower"}
    voice.write_text(yaml.safe_dump(data))
    with load_cartridge(tmp_path) as cartridge:
        brief = assemble_prompt(cartridge).text
        assert "test preset" in brief and "lower" in brief


def test_corrupt_launcher_does_not_break_cloud_setup(monkeypatch):
    from modules.pubpartner_routes import local_model_defaults
    monkeypatch.setenv("PUBCAST_LOCAL_MODEL_URL", "incomplete")
    assert asyncio.run(local_model_defaults())["enabled"] is False


def test_shared_download_survives_cancellation_and_retries_after_failure(monkeypatch):
    from modules import pubpartner_local_setup as local

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def pull():
            calls.append(True)
            entered.set()
            await release.wait()
            if len(calls) == 1:
                raise ValueError("interrupted upstream")

        monkeypatch.setattr(local, "download_starter", pull)
        host = local.LocalOllama()
        first = asyncio.create_task(host.install_starter())
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(host.install_starter())
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(ValueError, match="interrupted upstream"):
            await second
        assert len(calls) == 1
        await host.install_starter()
        assert len(calls) == 2
        await host.stop()

    asyncio.run(exercise())


def test_occupied_connector_port_never_reports_ready(tmp_path, monkeypatch):
    from modules.pubpartner_connector_host import ConnectorHost
    from pubpartner.cartridge import init_cartridge
    init_cartridge(tmp_path, "Sample", "Test cartridge", "quiet", "simple")
    monkeypatch.setenv("PUBPARTNER_CONNECTOR_CARTRIDGE", str(tmp_path))

    async def exercise():
        async def unrelated(reader, writer):
            await reader.read(2048)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
            writer.close()

        host = ConnectorHost(tmp_path)
        async with await asyncio.start_server(unrelated, "127.0.0.1", 8768):
            start = asyncio.create_task(host.start("http"))
            while not host.status()["starting"] and not start.done():
                await asyncio.sleep(.01)
            assert not host.status()["running"]
            with pytest.raises(ValueError, match="8768"):
                await start
        assert not host.status()["running"]
        try:
            assert (await host.start("http"))["running"]
        finally:
            await host.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("callback", ["https://[broken/pubpartner", "https://example.net:99999/pubpartner"])
def test_invalid_oauth_host_setup_still_returns_status(monkeypatch, callback):
    from modules.pubpartner_chatgpt_link import configured
    monkeypatch.setenv("PUBPARTNER_OPENAI_CLIENT_ID", "synthetic")
    monkeypatch.setenv("PUBPARTNER_OPENAI_CALLBACK", callback)
    assert not configured()


def test_discovery_distinguishes_failed_inspection_from_empty_catalog(monkeypatch):
    from modules.pubpartner_local_setup import discover
    monkeypatch.delenv("PUBCAST_LOCAL_MODEL_URL", raising=False)
    original = httpx.AsyncClient

    def upstream(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "already-installed"}]})
        return httpx.Response(503)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(upstream), **kw))
    result = asyncio.run(discover())
    assert result["ollama"] and result["incomplete"] and not result["choices"]


def test_late_chatgpt_signin_does_not_redirect_after_switching_provider(reach):
    from playwright.sync_api import sync_playwright
    base, token = reach
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.add_init_script("sessionStorage.setItem('pubcast_token'," + json.dumps(token) + ")")
        page.route("**/api/pubpartner/chatgpt/account", lambda route: route.fulfill(
            json={"configured": True, "linked": False, "models": []}))
        page.goto(base + "/pubpartner#connections")
        held = []
        page.route("**/api/pubpartner/chatgpt/connect", lambda route: held.append(route))
        with page.expect_request("**/chatgpt/connect"):
            page.locator("#connectAccount").click()
        page.locator("#connectProvider").select_option("claude_web")
        with page.expect_event("requestfinished", predicate=lambda req: req.url.endswith("/chatgpt/connect")):
            held[0].fulfill(json={"url": base + "/unexpected-signin"})
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert page.url == base + "/pubpartner#connections"
        assert page.locator("#claudeHandoff").is_visible()
        browser.close()
