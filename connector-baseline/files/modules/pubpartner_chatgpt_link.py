"""Session-bound OpenAI consent for a registered PubPartner client.

The browser returns to the same PubPartner tab. Access tokens stay in host
memory and are usable only with the originating authenticated PubCast session.
"""
import base64
import hashlib
import os
import logging
import secrets
import time
from contextvars import ContextVar
from urllib.parse import urlencode, urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import HTTPException

ISSUER = "https://auth.openai.com"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
current_session = ContextVar("chatgpt_link_session", default="")
pending = {}
accounts = {}


class CallbackAccessFilter(logging.Filter):
    """Preserve the access event while removing callback parameters."""
    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, separator, _ = args[2].partition("?")
            if separator and path == "/pubpartner":
                record.args = (*args[:2], path, *args[3:])
        return True


callback_access_filter = CallbackAccessFilter()


def configured():
    try:
        callback = urlsplit(os.getenv("PUBPARTNER_OPENAI_CALLBACK", ""))
        if callback.port == 0:
            return False
    except ValueError:
        return False
    secure = callback.scheme == "https" or (callback.scheme == "http" and callback.hostname == "127.0.0.1")
    return bool(os.getenv("PUBPARTNER_OPENAI_CLIENT_ID") and secure and callback.hostname
                and callback.path == "/pubpartner" and not (callback.query or callback.fragment or callback.username or callback.password))


def prune(records):
    now = time.monotonic()
    for key in [k for k, item in records.items() if item["expires"] <= now]:
        records.pop(key, None)


def session_id(identity):
    token_id = (identity.get("payload") or {}).get("jti")
    return token_id if identity.get("authenticated") and isinstance(token_id, str) else ""


def require_session(identity):
    session = session_id(identity)
    if not session:
        raise HTTPException(401, "Sign in to PubCast to link a ChatGPT account.")
    return session


async def metadata(client):
    response = await client.get(ISSUER + "/.well-known/openid-configuration")
    response.raise_for_status()
    doc = response.json()
    if doc["issuer"] != ISSUER or any(
        urlsplit(doc[field]).scheme != "https" or urlsplit(doc[field]).netloc != "auth.openai.com"
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri")
    ):
        raise ValueError("Unexpected OpenID discovery configuration")
    return doc


async def begin(identity, *, host_id, origin):
    session = require_session(identity)
    if not configured():
        raise HTTPException(409, "This host needs a registered PubPartner OpenAI client and callback.")
    callback = os.environ["PUBPARTNER_OPENAI_CALLBACK"]
    callback_url = urlsplit(callback)
    if origin != f"{callback_url.scheme}://{callback_url.netloc}":
        raise HTTPException(409, "Open PubPartner on the same address as its registered ChatGPT callback.")
    client_id = os.environ["PUBPARTNER_OPENAI_CLIENT_ID"]
    prune(pending)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    pending[session] = dict(state=state, verifier=verifier, nonce=nonce, expires=time.monotonic()+600,
                            callback=callback, client_id=client_id)
    async with httpx.AsyncClient(timeout=12) as client:
        doc = await metadata(client)
    if pending.get(session, {}).get("state") != state:
        raise HTTPException(409, "This sign-in attempt was cancelled or replaced.")
    params = dict(ext_agent_host_id=uuid5(NAMESPACE_URL, "pubpartner:openai:" + host_id).urn, client_id=client_id, redirect_uri=callback, response_type="code",
                  resource="https://api.openai.com/v1", scope=f"openid email profile resource.invoke {PLAN_SCOPE}", prompt="select_account consent",
                  state=state, nonce=nonce, code_challenge=challenge, code_challenge_method="S256")
    return {"url": doc["authorization_endpoint"] + "?" + urlencode(params)}


async def finish(identity, body):
    import jwt

    session = require_session(identity)
    prune(pending)
    attempt = pending.get(session)
    if not attempt or attempt.get("used") or not secrets.compare_digest(str(body.get("state", "")).encode(), attempt["state"].encode()):
        raise HTTPException(400, "This sign-in request expired or belongs to another session.")
    attempt["used"] = True
    if body.get("error") or not body.get("code"):
        raise HTTPException(400, "Account connection cancelled.")
    client_id = attempt["client_id"]
    if body.get("client_id", client_id) != client_id:
        raise HTTPException(400, "Client registration changed during sign-in.")
    async with httpx.AsyncClient(timeout=15) as client:
        doc = await metadata(client)
        form = dict(client_id=client_id, redirect_uri=attempt["callback"], grant_type="authorization_code",
                    resource="https://api.openai.com/v1", code=body["code"], code_verifier=attempt["verifier"])
        secret = os.getenv("PUBPARTNER_OPENAI_CLIENT_SECRET")
        response = await client.post(doc["token_endpoint"], data=form, auth=(client_id, secret) if secret else None)
        response.raise_for_status()
        tokens = response.json()
        keys = await client.get(doc["jwks_uri"])
        keys.raise_for_status()
        encoded = tokens["id_token"]
        kid = jwt.get_unverified_header(encoded)["kid"]
        key = next((k for k in jwt.PyJWKSet.from_dict(keys.json()).keys if k.key_id == kid), None)
        if key is None:
            raise ValueError("Unknown identity signing key")
        claims = jwt.decode(encoded, key.key, algorithms=["RS256"], issuer=ISSUER, audience=client_id,
                            options={"require":["sub","iat","exp","nonce"]})
        if claims["nonce"] != attempt["nonce"] or claims.get("azp", client_id) != client_id:
            raise ValueError("Identity did not match sign-in request")
        access = ""
        models = []
        if PLAN_SCOPE in tokens.get("scope", "").split():
            access = tokens["access_token"]
            catalog = await client.get("https://api.openai.com/v1/models", headers={"Authorization":"Bearer "+access})
            catalog.raise_for_status()
            models = [m["slug"] for m in catalog.json().get("models", [])
                      if m.get("visibility") == "list" and m.get("slug")]
    prune(pending)
    if pending.get(session) is not attempt:
        raise HTTPException(409, "This sign-in attempt expired, was disconnected, or was replaced.")
    del pending[session]
    prune(accounts)
    accounts[session] = dict(access=access, models=models, label=claims.get("email") or claims["sub"],
                             expires=time.monotonic()+int(tokens["expires_in"]))
    return status(session)


def status(session):
    prune(accounts)
    record = accounts.get(session)
    return {"configured": configured(), "linked": bool(record), "plan_enabled": bool(record and record["access"]), "label": record["label"] if record else "",
            "models": record["models"] if record else []}


def token_for_model(model):
    prune(accounts)
    record = accounts.get(current_session.get())
    if not record or model not in record["models"]:
        raise ValueError("Reconnect ChatGPT and select a model offered by this account.")
    return record["access"]
