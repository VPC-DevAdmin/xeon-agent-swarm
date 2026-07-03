"""
Offline unit tests for backend/agents/tool_impls.py.

Local tools (csv_file, sqlite sql_database) execute for real against tmp files.
Networked tools have httpx.AsyncClient (and smtplib/imaplib for email) monkey-
patched so nothing touches the network — we assert the request the function
builds and the success string it returns, plus that a missing-creds call returns
a "[tool] error:" string instead of raising. Style mirrors test_event_adapter.py
(plain asyncio.run for the async bits).
"""
from __future__ import annotations

import asyncio
import os
import sqlite3

import httpx
import pytest

from backend.agents import tool_impls as ti


# ── a recording fake for httpx.AsyncClient ──────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=None, response=None)


class FakeAsyncClient:
    """Records every request; returns a scripted response.

    `responder(method, url, kwargs)` returns a FakeResponse. `calls` accumulates
    (method, url, kwargs) tuples across all instances (shared class list reset per
    test via install()).
    """
    calls: list = []
    responder = staticmethod(lambda method, url, kwargs: FakeResponse())
    init_kwargs: list = []

    def __init__(self, *args, **kwargs):
        type(self).init_kwargs.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        type(self).calls.append((method.upper(), url, kwargs))
        return type(self).responder(method.upper(), url, kwargs)

    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)


def install(monkeypatch, responder=None):
    FakeAsyncClient.calls = []
    FakeAsyncClient.init_kwargs = []
    if responder is not None:
        FakeAsyncClient.responder = staticmethod(responder)
    else:
        FakeAsyncClient.responder = staticmethod(
            lambda method, url, kwargs: FakeResponse())
    monkeypatch.setattr(ti.httpx, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient


# ── 1. csv_file (REAL local file) ───────────────────────────────────────────────

def test_csv_file_real_roundtrip(tmp_path):
    path = str(tmp_path / "data.csv")
    creds = {"file_path": path}

    async def go():
        r1 = await ti.csv_file(
            {"action": "append", "row": {"name": "alice", "score": "1"}}, creds)
        r2 = await ti.csv_file(
            {"action": "append", "row": {"name": "bob", "score": "2"}}, creds)
        read = await ti.csv_file({"action": "read"}, creds)
        upd = await ti.csv_file(
            {"action": "update", "match": {"column": "name", "value": "alice"},
             "set": {"score": "99"}}, creds)
        read2 = await ti.csv_file({"action": "read", "limit": 10}, creds)
        return r1, r2, read, upd, read2

    r1, r2, read, upd, read2 = asyncio.run(go())
    assert "Appended" in r1 and "Appended" in r2
    assert "alice" in read and "bob" in read
    assert "name, score" in read  # header written
    assert "Updated 1 row" in upd
    assert "99" in read2

    # verify on disk for real
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(__import__("csv").DictReader(fh))
    assert rows[0]["name"] == "alice" and rows[0]["score"] == "99"
    assert rows[1]["name"] == "bob"


def test_csv_file_missing_creds():
    out = asyncio.run(ti.csv_file({"action": "read"}, {}))
    assert out.startswith("[csv_file] error:")


# ── 2. sql_database (REAL sqlite) ───────────────────────────────────────────────

def test_sql_database_real_sqlite(tmp_path):
    db = str(tmp_path / "t.db")
    creds = {"connection_string": f"sqlite:///{db}"}

    async def go():
        c = await ti.sql_database(
            {"action": "execute",
             "sql": "CREATE TABLE users (id INTEGER, name TEXT)"}, creds)
        i = await ti.sql_database(
            {"action": "execute", "sql": "INSERT INTO users VALUES (?, ?)",
             "parameters": [1, "alice"]}, creds)
        q = await ti.sql_database(
            {"action": "query", "sql": "SELECT id, name FROM users"}, creds)
        return c, i, q

    c, i, q = asyncio.run(go())
    assert "affected" in i
    assert "alice" in q and "1 row" in q

    # confirm it really wrote to the file
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM users").fetchone()[0] == "alice"
    conn.close()


def test_sql_database_postgres_not_wired():
    out = asyncio.run(ti.sql_database(
        {"action": "query", "sql": "SELECT 1"},
        {"connection_string": "postgresql://u:p@host/db"}))
    assert "only sqlite is wired in this build" in out


def test_sql_database_missing_creds():
    out = asyncio.run(ti.sql_database({"action": "query", "sql": "SELECT 1"}, {}))
    assert out.startswith("[sql_database] error:")


# ── 3. telegram ─────────────────────────────────────────────────────────────────

def test_telegram_send(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.telegram(
        {"action": "send", "text": "hi"},
        {"bot_token": "TOK", "chat_id": "123"}))
    assert out == "Sent to Telegram chat 123."
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.telegram.org/botTOK/sendMessage"
    assert kwargs["json"] == {"chat_id": "123", "text": "hi"}


def test_telegram_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(
        json_data={"result": [{"message": {"text": "hello"}},
                              {"message": {"text": "world"}}]}))
    out = asyncio.run(ti.telegram({"action": "read"},
                                  {"bot_token": "TOK", "chat_id": "123"}))
    assert "hello" in out and "world" in out
    assert FakeAsyncClient.calls[-1][1].endswith("/getUpdates")


def test_telegram_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.telegram({"action": "send", "text": "x"}, {}))
    assert out.startswith("[telegram] error:")


# ── 4. sms ──────────────────────────────────────────────────────────────────────

def test_sms_send(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"sid": "SM1"}))
    out = asyncio.run(ti.sms(
        {"action": "send", "to": "+1999", "body": "yo"},
        {"account_sid": "AC1", "auth_token": "TT", "from_number": "+1000"}))
    assert out == "Sent SMS to +1999."
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST"
    assert url == "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json"
    assert kwargs["data"] == {"To": "+1999", "From": "+1000", "Body": "yo"}
    # basic auth passed to the client
    assert FakeAsyncClient.init_kwargs[-1]["auth"] == ("AC1", "TT")


def test_sms_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(
        json_data={"messages": [{"from": "+1", "body": "hey"}]}))
    out = asyncio.run(ti.sms(
        {"action": "read"},
        {"account_sid": "AC1", "auth_token": "TT", "from_number": "+1000"}))
    assert "hey" in out


def test_sms_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.sms({"action": "send", "to": "+1"}, {}))
    assert out.startswith("[sms] error:")


# ── 5. email (smtplib / imaplib patched) ────────────────────────────────────────

class FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.sent = None
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.started_tls = True

    def login(self, u, p):
        self.logged_in = (u, p)

    def sendmail(self, frm, to, msg):
        self.sent = (frm, to, msg)

    def quit(self):
        pass


def test_email_send(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(ti.smtplib, "SMTP", FakeSMTP)
    creds = {"smtp_host": "smtp.x", "smtp_port": "587",
             "username": "me@x.com", "password": "pw", "imap_host": "imap.x"}
    out = asyncio.run(ti.email(
        {"action": "send", "to": "you@x.com", "subject": "S", "body": "B"}, creds))
    assert out == "Sent email to you@x.com."
    inst = FakeSMTP.instances[-1]
    assert inst.host == "smtp.x" and inst.port == 587
    assert inst.started_tls and inst.logged_in == ("me@x.com", "pw")
    assert inst.sent[0] == "me@x.com" and inst.sent[1] == ["you@x.com"]
    assert "Subject: S" in inst.sent[2]


class FakeIMAP:
    def __init__(self, host):
        self.host = host

    def login(self, u, p):
        pass

    def select(self, mbox):
        pass

    def search(self, charset, criteria):
        return "OK", [b"1 2"]

    def fetch(self, mid, spec):
        raw = (b"Subject: Hello\r\nFrom: a@b.com\r\n\r\nbody")
        return "OK", [(b"1", raw)]

    def logout(self):
        pass


def test_email_read(monkeypatch):
    monkeypatch.setattr(ti.imaplib, "IMAP4_SSL", FakeIMAP)
    creds = {"smtp_host": "smtp.x", "smtp_port": "587",
             "username": "me@x.com", "password": "pw", "imap_host": "imap.x"}
    out = asyncio.run(ti.email({"action": "read", "limit": 5}, creds))
    assert "Hello" in out and "a@b.com" in out


def test_email_missing_creds():
    out = asyncio.run(ti.email({"action": "send", "to": "x@y.com"}, {}))
    assert out.startswith("[email] error:")


# ── 6. x_twitter ────────────────────────────────────────────────────────────────

def test_x_twitter_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(
        json_data={"data": [{"text": "first tweet"}, {"text": "second"}]}))
    out = asyncio.run(ti.x_twitter(
        {"action": "read", "query": "python"}, {"bearer_token": "BR"}))
    assert "first tweet" in out and "second" in out
    _, url, kwargs = FakeAsyncClient.calls[-1]
    assert url == "https://api.twitter.com/2/tweets/search/recent"
    assert kwargs["params"] == {"query": "python"}
    assert FakeAsyncClient.init_kwargs[-1]["headers"]["Authorization"] == "Bearer BR"


def test_x_twitter_post(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(
        json_data={"data": {"id": "42"}}))
    out = asyncio.run(ti.x_twitter(
        {"action": "post", "text": "hello world"}, {"bearer_token": "BR"}))
    assert out == "Posted tweet 42."
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.twitter.com/2/tweets"
    assert kwargs["json"] == {"text": "hello world"}


def test_x_twitter_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.x_twitter({"action": "read", "query": "x"}, {}))
    assert out.startswith("[x_twitter] error:")


# ── 7. fetch_url ────────────────────────────────────────────────────────────────

def test_fetch_url(monkeypatch):
    html = "<html><head><style>a{}</style></head><body><h1>Hi</h1> <p>There</p></body></html>"
    install(monkeypatch, lambda m, u, k: FakeResponse(text=html))
    out = asyncio.run(ti.fetch_url({"url": "http://example.com"}, {}))
    assert "Hi" in out and "There" in out
    assert "<" not in out  # tags stripped
    assert FakeAsyncClient.calls[-1][0] == "GET"


def test_fetch_url_missing_url(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.fetch_url({}, {}))
    assert out.startswith("[fetch_url] error:")


# ── 8. webhook ──────────────────────────────────────────────────────────────────

def test_webhook(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(status_code=202))
    out = asyncio.run(ti.webhook(
        {"payload": {"a": 1}},
        {"url": "https://hook.x/e", "signing_secret": "sec"}))
    assert "202" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://hook.x/e"
    assert kwargs["json"] == {"a": 1}
    assert kwargs["headers"]["X-Signature"] == "sec"


def test_webhook_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.webhook({"payload": {}}, {}))
    assert out.startswith("[webhook] error:")


# ── 9. rss_feed ─────────────────────────────────────────────────────────────────

def test_rss_feed(monkeypatch):
    xml = """<rss><channel>
      <item><title>First post</title></item>
      <item><title><![CDATA[Second post]]></title></item>
    </channel></rss>"""
    install(monkeypatch, lambda m, u, k: FakeResponse(text=xml))
    out = asyncio.run(ti.rss_feed({"limit": 5}, {"feed_url": "http://f/rss"}))
    assert "First post" in out and "Second post" in out


def test_rss_feed_atom(monkeypatch):
    xml = """<feed><entry><title>Atom one</title></entry></feed>"""
    install(monkeypatch, lambda m, u, k: FakeResponse(text=xml))
    out = asyncio.run(ti.rss_feed({}, {"feed_url": "http://f/atom"}))
    assert "Atom one" in out


def test_rss_feed_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.rss_feed({}, {}))
    assert out.startswith("[rss_feed] error:")


# ── 10. rest_api ────────────────────────────────────────────────────────────────

def test_rest_api(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(status_code=200, text="pong"))
    out = asyncio.run(ti.rest_api(
        {"method": "get", "path": "/ping"},
        {"base_url": "https://api.x", "auth_header": "Bearer T"}))
    assert "200" in out and "pong" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "GET" and url == "https://api.x/ping"
    assert kwargs["headers"]["Authorization"] == "Bearer T"


def test_rest_api_post_body(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(status_code=201, text="{}"))
    out = asyncio.run(ti.rest_api(
        {"method": "POST", "path": "items", "body": {"n": 1}},
        {"base_url": "https://api.x/", "auth_header": "Key K"}))
    assert "201" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.x/items"
    assert kwargs["json"] == {"n": 1}


def test_rest_api_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.rest_api({"method": "GET", "path": "/x"}, {}))
    assert out.startswith("[rest_api] error:")


# ── registry ────────────────────────────────────────────────────────────────────

def test_registry_complete():
    expected = {"csv_file", "sql_database", "telegram", "sms", "email",
                "x_twitter", "fetch_url", "webhook", "rss_feed", "rest_api"}
    assert set(ti.IMPLS) == expected
    assert all(callable(fn) for fn in ti.IMPLS.values())
