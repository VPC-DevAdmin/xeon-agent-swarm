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


# ── 11. slack ─────────────────────────────────────────────────────────────────

def test_slack_send(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"ok": True}))
    out = asyncio.run(ti.slack(
        {"action": "send", "text": "hi"},
        {"bot_token": "TOK", "channel": "C1"}))
    assert out == "Sent to Slack channel C1."
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://slack.com/api/chat.postMessage"
    assert kwargs["json"] == {"channel": "C1", "text": "hi"}
    assert FakeAsyncClient.init_kwargs[-1]["headers"]["Authorization"] == "Bearer TOK"


def test_slack_send_channel_override(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"ok": True}))
    out = asyncio.run(ti.slack(
        {"action": "send", "text": "hi", "channel": "OVERRIDE"},
        {"bot_token": "TOK", "channel": "C1"}))
    assert "OVERRIDE" in out
    assert FakeAsyncClient.calls[-1][2]["json"]["channel"] == "OVERRIDE"


def test_slack_send_error(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(
        json_data={"ok": False, "error": "channel_not_found"}))
    out = asyncio.run(ti.slack(
        {"action": "send", "text": "hi"},
        {"bot_token": "TOK", "channel": "C1"}))
    assert out.startswith("[slack] error:") and "channel_not_found" in out


def test_slack_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(
        json_data={"ok": True, "messages": [{"text": "one"}, {"text": "two"}]}))
    out = asyncio.run(ti.slack(
        {"action": "read", "limit": 5},
        {"bot_token": "TOK", "channel": "C1"}))
    assert "one" in out and "two" in out
    _, url, kwargs = FakeAsyncClient.calls[-1]
    assert url == "https://slack.com/api/conversations.history"
    assert kwargs["params"] == {"channel": "C1", "limit": 5}


def test_slack_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.slack({"action": "send", "text": "x"}, {}))
    assert out.startswith("[slack] error:")


# ── 12. discord ───────────────────────────────────────────────────────────────

def test_discord_send(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"id": "1"}))
    out = asyncio.run(ti.discord(
        {"action": "send", "text": "hi"},
        {"bot_token": "TOK", "channel_id": "999"}))
    assert out == "Sent to Discord channel 999."
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST"
    assert url == "https://discord.com/api/v10/channels/999/messages"
    assert kwargs["json"] == {"content": "hi"}
    assert FakeAsyncClient.init_kwargs[-1]["headers"]["Authorization"] == "Bot TOK"


def test_discord_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(
        json_data=[{"content": "a"}, {"content": "b"}]))
    out = asyncio.run(ti.discord(
        {"action": "read", "limit": 3},
        {"bot_token": "TOK", "channel_id": "999"}))
    assert "a" in out and "b" in out
    assert FakeAsyncClient.calls[-1][2]["params"] == {"limit": 3}


def test_discord_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.discord({"action": "send", "text": "x"}, {}))
    assert out.startswith("[discord] error:")


# ── 13. github ────────────────────────────────────────────────────────────────

def test_github_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(
        json_data=[{"number": 1, "title": "Bug"}, {"number": 2, "title": "Feat"}]))
    out = asyncio.run(ti.github(
        {"action": "read", "limit": 5},
        {"access_token": "TOK", "repo": "o/r"}))
    assert "#1 Bug" in out and "#2 Feat" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "GET" and url == "https://api.github.com/repos/o/r/issues"
    assert kwargs["params"] == {"per_page": 5}
    hdrs = FakeAsyncClient.init_kwargs[-1]["headers"]
    assert hdrs["Authorization"] == "Bearer TOK"
    assert hdrs["Accept"] == "application/vnd.github+json"


def test_github_create(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"number": 7}))
    out = asyncio.run(ti.github(
        {"action": "create", "title": "T", "body": "B"},
        {"access_token": "TOK", "repo": "o/r"}))
    assert out == "Created GitHub issue #7 in o/r."
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.github.com/repos/o/r/issues"
    assert kwargs["json"] == {"title": "T", "body": "B"}


def test_github_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.github({"action": "read"}, {}))
    assert out.startswith("[github] error:")


# ── 14. notion ────────────────────────────────────────────────────────────────

def test_notion_search(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"results": [
        {"id": "p1", "properties": {"Name": {"type": "title",
         "title": [{"plain_text": "My page"}]}}},
        {"id": "p2", "properties": {}}]}))
    out = asyncio.run(ti.notion(
        {"action": "read", "query": "foo"}, {"integration_token": "TOK"}))
    assert "My page" in out and "p2" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.notion.com/v1/search"
    assert kwargs["json"] == {"query": "foo"}
    hdrs = FakeAsyncClient.init_kwargs[-1]["headers"]
    assert hdrs["Authorization"] == "Bearer TOK"
    assert hdrs["Notion-Version"] == "2022-06-28"


def test_notion_create(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"id": "newpage"}))
    out = asyncio.run(ti.notion(
        {"action": "create", "parent_id": "par", "title": "Hi"},
        {"integration_token": "TOK"}))
    assert "newpage" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.notion.com/v1/pages"
    assert kwargs["json"]["parent"] == {"page_id": "par"}
    assert (kwargs["json"]["properties"]["title"]["title"][0]["text"]["content"]
            == "Hi")


def test_notion_create_no_parent(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.notion(
        {"action": "create", "title": "Hi"}, {"integration_token": "TOK"}))
    assert out == "[notion] error: parent_id required to create a page"


def test_notion_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.notion({"action": "read"}, {}))
    assert out.startswith("[notion] error:")


# ── 15. airtable ──────────────────────────────────────────────────────────────

def test_airtable_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"records": [
        {"id": "rec1", "fields": {"Name": "Al", "Age": 3}}]}))
    out = asyncio.run(ti.airtable(
        {"action": "read", "table": "T1"},
        {"api_key": "KEY", "base_id": "B1"}))
    assert "rec1" in out and "Name=Al" in out
    method, url, _ = FakeAsyncClient.calls[-1]
    assert method == "GET" and url == "https://api.airtable.com/v0/B1/T1"
    assert FakeAsyncClient.init_kwargs[-1]["headers"]["Authorization"] == "Bearer KEY"


def test_airtable_create(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"id": "rec9"}))
    out = asyncio.run(ti.airtable(
        {"action": "create", "table": "T1", "fields": {"Name": "Bo"}},
        {"api_key": "KEY", "base_id": "B1"}))
    assert "rec9" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.airtable.com/v0/B1/T1"
    assert kwargs["json"] == {"fields": {"Name": "Bo"}}


def test_airtable_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.airtable({"action": "read", "table": "T"}, {}))
    assert out.startswith("[airtable] error:")


# ── 16. linear ────────────────────────────────────────────────────────────────

def test_linear_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"data": {"issues": {
        "nodes": [{"identifier": "ENG-1", "title": "Fix",
                   "state": {"name": "Todo"}}]}}}))
    out = asyncio.run(ti.linear({"action": "read"}, {"api_key": "KEY"}))
    assert "ENG-1 Fix (Todo)" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.linear.app/graphql"
    assert "issues(first:" in kwargs["json"]["query"]
    # raw api_key, NOT Bearer
    assert FakeAsyncClient.init_kwargs[-1]["headers"]["Authorization"] == "KEY"


def test_linear_create(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"data": {
        "issueCreate": {"success": True, "issue": {"identifier": "ENG-9"}}}}))
    out = asyncio.run(ti.linear(
        {"action": "create", "title": "New", "team_id": "team123"},
        {"api_key": "KEY"}))
    assert "ENG-9" in out
    kwargs = FakeAsyncClient.calls[-1][2]
    assert "issueCreate" in kwargs["json"]["query"]
    assert kwargs["json"]["variables"] == {"t": "New", "tid": "team123"}


def test_linear_create_no_team(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.linear(
        {"action": "create", "title": "New"}, {"api_key": "KEY"}))
    assert out.startswith("[linear] error:") and "team_id" in out


def test_linear_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.linear({"action": "read"}, {}))
    assert out.startswith("[linear] error:")


# ── 17. linkedin ──────────────────────────────────────────────────────────────

def test_linkedin_post(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"id": "urn:1"}))
    out = asyncio.run(ti.linkedin(
        {"action": "post", "text": "hello", "author_urn": "urn:li:person:X"},
        {"access_token": "TOK"}))
    assert out == "Published LinkedIn post."
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST" and url == "https://api.linkedin.com/v2/ugcPosts"
    body = kwargs["json"]
    assert body["author"] == "urn:li:person:X"
    assert body["lifecycleState"] == "PUBLISHED"
    assert (body["specificContent"]["com.linkedin.ugc.ShareContent"]
            ["shareCommentary"]["text"] == "hello")
    hdrs = FakeAsyncClient.init_kwargs[-1]["headers"]
    assert hdrs["Authorization"] == "Bearer TOK"
    assert hdrs["X-Restli-Protocol-Version"] == "2.0.0"


def test_linkedin_post_no_urn(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.linkedin(
        {"action": "post", "text": "hi"}, {"access_token": "TOK"}))
    assert out.startswith("[linkedin] error:") and "author_urn" in out


def test_linkedin_read(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.linkedin({"action": "read"}, {"access_token": "TOK"}))
    assert "does not expose feed reads" in out
    assert not out.startswith("[linkedin] error:")


def test_linkedin_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.linkedin({"action": "post", "text": "x"}, {}))
    assert out.startswith("[linkedin] error:")


# ── 18. instagram ─────────────────────────────────────────────────────────────

def test_instagram_read(monkeypatch):
    install(monkeypatch, lambda m, u, k: FakeResponse(json_data={"data": [
        {"caption": "sunset"}, {"caption": "coffee"}]}))
    out = asyncio.run(ti.instagram(
        {"action": "read"}, {"access_token": "TOK", "account_id": "acc"}))
    assert "sunset" in out and "coffee" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "GET"
    assert url == "https://graph.facebook.com/v19.0/acc/media"
    assert kwargs["params"] == {"fields": "caption,timestamp", "access_token": "TOK"}


def test_instagram_publish(monkeypatch):
    def responder(m, u, k):
        if u.endswith("/media_publish"):
            return FakeResponse(json_data={"id": "pub1"})
        return FakeResponse(json_data={"id": "creation1"})
    install(monkeypatch, responder)
    out = asyncio.run(ti.instagram(
        {"action": "publish", "image_url": "http://img", "caption": "hi"},
        {"access_token": "TOK", "account_id": "acc"}))
    assert "pub1" in out
    # first POST → media (creation), second → media_publish with creation_id
    (m1, u1, k1), (m2, u2, k2) = FakeAsyncClient.calls[-2], FakeAsyncClient.calls[-1]
    assert u1 == "https://graph.facebook.com/v19.0/acc/media"
    assert k1["data"] == {"image_url": "http://img", "caption": "hi",
                          "access_token": "TOK"}
    assert u2 == "https://graph.facebook.com/v19.0/acc/media_publish"
    assert k2["data"] == {"creation_id": "creation1", "access_token": "TOK"}


def test_instagram_publish_no_image(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.instagram(
        {"action": "publish", "caption": "hi"},
        {"access_token": "TOK", "account_id": "acc"}))
    assert out.startswith("[instagram] error:") and "image_url" in out


def test_instagram_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.instagram({"action": "read"}, {}))
    assert out.startswith("[instagram] error:")


# ── shared google service-account JSON (real throwaway RSA key) ─────────────────

def _fake_service_account_json():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()).decode("ascii")
    return __import__("json").dumps({
        "client_email": "svc@proj.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    })


def _google_responder(api_response):
    """Return {"access_token": "fake"} for the token POST, api_response otherwise."""
    def responder(method, url, kwargs):
        if url == "https://oauth2.googleapis.com/token":
            return FakeResponse(json_data={"access_token": "fake"})
        return api_response
    return responder


# ── 19. google_sheets ─────────────────────────────────────────────────────────

def test_google_sheets_read(monkeypatch):
    install(monkeypatch, _google_responder(
        FakeResponse(json_data={"values": [["a", "b"], ["c", "d"]]})))
    creds = {"service_account_json": _fake_service_account_json(),
             "sheet_id": "SHEET"}
    out = asyncio.run(ti.google_sheets({"action": "read", "range": "A1:B2"}, creds))
    assert "a, b" in out and "c, d" in out
    # token POST happened, then the sheets GET with Bearer fake
    token_call = FakeAsyncClient.calls[0]
    assert token_call[1] == "https://oauth2.googleapis.com/token"
    assert token_call[2]["data"]["grant_type"] == (
        "urn:ietf:params:oauth:grant-type:jwt-bearer")
    method, url, _ = FakeAsyncClient.calls[-1]
    assert method == "GET"
    assert url == ("https://sheets.googleapis.com/v4/spreadsheets/SHEET/"
                   "values/A1:B2")
    assert FakeAsyncClient.init_kwargs[-1]["headers"]["Authorization"] == "Bearer fake"


def test_google_sheets_append(monkeypatch):
    install(monkeypatch, _google_responder(
        FakeResponse(json_data={"updates": {}})))
    creds = {"service_account_json": _fake_service_account_json(),
             "sheet_id": "SHEET"}
    out = asyncio.run(ti.google_sheets(
        {"action": "append", "range": "A1", "row": ["x", "y"]}, creds))
    assert "Appended" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST"
    assert url == ("https://sheets.googleapis.com/v4/spreadsheets/SHEET/"
                   "values/A1:append")
    assert kwargs["params"] == {"valueInputOption": "RAW"}
    assert kwargs["json"] == {"values": [["x", "y"]]}


def test_google_sheets_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.google_sheets({"action": "read"}, {}))
    assert out.startswith("[google_sheets] error:")


# ── 20. google_calendar ───────────────────────────────────────────────────────

def test_google_calendar_read(monkeypatch):
    install(monkeypatch, _google_responder(
        FakeResponse(json_data={"items": [{"summary": "Standup"},
                                          {"summary": "Lunch"}]})))
    creds = {"service_account_json": _fake_service_account_json(),
             "calendar_id": "CAL"}
    out = asyncio.run(ti.google_calendar({"action": "read", "limit": 5}, creds))
    assert "Standup" in out and "Lunch" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "GET"
    assert url == ("https://www.googleapis.com/calendar/v3/calendars/CAL/events")
    assert kwargs["params"]["singleEvents"] == "true"
    assert FakeAsyncClient.init_kwargs[-1]["headers"]["Authorization"] == "Bearer fake"


def test_google_calendar_create(monkeypatch):
    install(monkeypatch, _google_responder(
        FakeResponse(json_data={"id": "evt1"})))
    creds = {"service_account_json": _fake_service_account_json(),
             "calendar_id": "CAL"}
    out = asyncio.run(ti.google_calendar(
        {"action": "create", "summary": "Sync",
         "start": "2026-01-01T10:00:00Z", "end": "2026-01-01T11:00:00Z"}, creds))
    assert "evt1" in out
    method, url, kwargs = FakeAsyncClient.calls[-1]
    assert method == "POST"
    assert url == "https://www.googleapis.com/calendar/v3/calendars/CAL/events"
    assert kwargs["json"] == {
        "summary": "Sync",
        "start": {"dateTime": "2026-01-01T10:00:00Z"},
        "end": {"dateTime": "2026-01-01T11:00:00Z"}}


def test_google_calendar_missing_creds(monkeypatch):
    install(monkeypatch)
    out = asyncio.run(ti.google_calendar({"action": "read"}, {}))
    assert out.startswith("[google_calendar] error:")


# ── registry ────────────────────────────────────────────────────────────────────

def test_registry_complete():
    expected = {"csv_file", "sql_database", "telegram", "sms", "email",
                "x_twitter", "fetch_url", "webhook", "rss_feed", "rest_api",
                "slack", "discord", "github", "notion", "airtable", "linear",
                "linkedin", "instagram", "google_sheets", "google_calendar"}
    assert set(ti.IMPLS) == expected
    assert all(callable(fn) for fn in ti.IMPLS.values())
