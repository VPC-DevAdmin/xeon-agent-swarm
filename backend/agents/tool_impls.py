"""
backend/agents/tool_impls.py

Real async implementations of the "backing: api" tools in the managed catalog
(config/tool_catalog.yaml). Each is a single coroutine with the exact contract:

    async def <tool_id>(params: dict, creds: dict) -> str

- params: the arguments the worker/LLM supplies (read-vs-write tools carry an
          "action" key).
- creds:  the tool's decrypted connector config; keys are the `setup` field names
          declared for that tool in the catalog.
- returns: a short, LLM-readable string describing the result.
- never raises: every failure (including missing creds) is caught and returned as
          "[<tool_id>] error: <msg>".

Only the standard library + httpx are used (httpx is already a dependency). The
local tools (csv_file, sql_database over sqlite) execute for real; the networked
tools issue real requests when configured and are exercised in tests against a
mocked httpx / smtplib / imaplib so the suite runs fully offline.

The IMPLS registry at the bottom is what the toolbox looks up by tool_id.
"""
from __future__ import annotations

import asyncio
import base64
import csv
import email as email_lib
import imaplib
import json as json_lib
import os
import re
import smtplib
import sqlite3
import time
from email.message import EmailMessage
from email.header import decode_header
from typing import Any, Callable

import httpx


# ── helpers ─────────────────────────────────────────────────────────────────────

def _err(tool_id: str, msg: Any) -> str:
    return f"[{tool_id}] error: {msg}"


def _require(creds: dict, *fields: str) -> str | None:
    """Return a comma-joined list of missing fields, or None if all present."""
    missing = [f for f in fields if not (creds or {}).get(f)]
    return ", ".join(missing) if missing else None


def _truncate(text: str, limit: int = 2000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


# ── 1. csv_file — REAL local file ───────────────────────────────────────────────

async def csv_file(params: dict, creds: dict) -> str:
    tool_id = "csv_file"
    try:
        path = (creds or {}).get("file_path")
        if not path:
            return _err(tool_id, "missing credential: file_path")
        action = (params or {}).get("action", "read")

        if action == "read":
            if not os.path.exists(path):
                return _err(tool_id, f"file not found: {path}")
            limit = int((params or {}).get("limit", 20))
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                rows = list(reader)
            if not rows:
                return f"[{tool_id}] {path} is empty."
            header, body = rows[0], rows[1:]
            shown = body[:limit]
            lines = [", ".join(header)]
            lines += [", ".join(r) for r in shown]
            return (f"Read {len(shown)} of {len(body)} row(s) from {path}:\n"
                    + "\n".join(lines))

        if action == "append":
            row = (params or {}).get("row")
            if row is None:
                return _err(tool_id, "append requires params.row")
            exists = os.path.exists(path) and os.path.getsize(path) > 0
            if isinstance(row, dict):
                fieldnames = list(row.keys())
                # If the file already exists, honour its header for column order.
                if exists:
                    with open(path, newline="", encoding="utf-8") as fh:
                        existing_header = next(csv.reader(fh), None)
                    if existing_header:
                        fieldnames = existing_header
                with open(path, "a", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(fh, fieldnames=fieldnames)
                    if not exists:
                        writer.writeheader()
                    writer.writerow({k: row.get(k, "") for k in fieldnames})
            else:  # list/tuple → positional row
                with open(path, "a", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerow(list(row))
            return f"Appended 1 row to {path}."

        if action == "update":
            match = (params or {}).get("match") or {}
            new_vals = (params or {}).get("set") or {}
            col, val = match.get("column"), match.get("value")
            if not col or val is None:
                return _err(tool_id, "update requires params.match {column, value}")
            if not new_vals:
                return _err(tool_id, "update requires params.set")
            if not os.path.exists(path):
                return _err(tool_id, f"file not found: {path}")
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                fieldnames = reader.fieldnames or []
                rows = list(reader)
            if col not in fieldnames:
                return _err(tool_id, f"no such column: {col}")
            changed = 0
            for r in rows:
                if r.get(col) == str(val):
                    for k, v in new_vals.items():
                        if k in fieldnames:
                            r[k] = v
                    changed += 1
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            return f"Updated {changed} row(s) in {path} where {col}={val}."

        return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 2. sql_database — REAL for sqlite ───────────────────────────────────────────

def _sqlite_path(conn: str) -> str:
    """Map a connection string to a sqlite file path (or ':memory:')."""
    if conn.startswith("sqlite:///"):
        return conn[len("sqlite:///"):]
    if conn.startswith("sqlite://"):
        return conn[len("sqlite://"):]
    return conn  # bare path


async def sql_database(params: dict, creds: dict) -> str:
    tool_id = "sql_database"
    try:
        conn_str = (creds or {}).get("connection_string")
        if not conn_str:
            return _err(tool_id, "missing credential: connection_string")
        if conn_str.startswith(("postgresql://", "postgres://")):
            return _err(tool_id, "only sqlite is wired in this build")

        action = (params or {}).get("action", "query")
        sql = (params or {}).get("sql")
        if not sql:
            return _err(tool_id, "missing params.sql")
        sql_params = (params or {}).get("parameters") or []
        db_path = _sqlite_path(conn_str)

        def _run() -> str:
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute(sql, sql_params)
                if action == "query":
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description] if cur.description else []
                    if not rows:
                        return f"[{tool_id}] query returned 0 rows."
                    lines = [", ".join(cols)] if cols else []
                    lines += [", ".join("" if v is None else str(v) for v in r)
                              for r in rows]
                    return (f"query returned {len(rows)} row(s):\n" + "\n".join(lines))
                # execute
                conn.commit()
                return f"execute affected {cur.rowcount} row(s)."
            finally:
                conn.close()

        return await asyncio.to_thread(_run)
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 3. telegram ─────────────────────────────────────────────────────────────────

async def telegram(params: dict, creds: dict) -> str:
    tool_id = "telegram"
    try:
        missing = _require(creds, "bot_token", "chat_id")
        if missing:
            return _err(tool_id, f"missing credentials: {missing}")
        token, chat_id = creds["bot_token"], creds["chat_id"]
        action = (params or {}).get("action", "send")
        base = f"https://api.telegram.org/bot{token}"

        async with httpx.AsyncClient(timeout=30) as client:
            if action == "send":
                text = (params or {}).get("text", "")
                resp = await client.post(f"{base}/sendMessage",
                                         json={"chat_id": chat_id, "text": text})
                resp.raise_for_status()
                return f"Sent to Telegram chat {chat_id}."
            if action == "read":
                resp = await client.get(f"{base}/getUpdates")
                resp.raise_for_status()
                data = resp.json()
                texts = []
                for upd in data.get("result", []):
                    msg = upd.get("message") or upd.get("channel_post") or {}
                    if msg.get("text"):
                        texts.append(msg["text"])
                if not texts:
                    return f"[{tool_id}] no recent messages."
                return "Recent Telegram messages:\n" + "\n".join(f"- {t}" for t in texts)
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 4. sms (Twilio) ─────────────────────────────────────────────────────────────

async def sms(params: dict, creds: dict) -> str:
    tool_id = "sms"
    try:
        missing = _require(creds, "account_sid", "auth_token", "from_number")
        if missing:
            return _err(tool_id, f"missing credentials: {missing}")
        sid, token, from_number = (creds["account_sid"], creds["auth_token"],
                                   creds["from_number"])
        action = (params or {}).get("action", "send")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

        async with httpx.AsyncClient(timeout=30, auth=(sid, token)) as client:
            if action == "send":
                to = (params or {}).get("to")
                body = (params or {}).get("body", "")
                if not to:
                    return _err(tool_id, "send requires params.to")
                resp = await client.post(
                    url, data={"To": to, "From": from_number, "Body": body})
                resp.raise_for_status()
                return f"Sent SMS to {to}."
            if action == "read":
                resp = await client.get(url)
                resp.raise_for_status()
                msgs = resp.json().get("messages", [])
                if not msgs:
                    return f"[{tool_id}] no recent messages."
                lines = [f"- {m.get('from', '?')}: {m.get('body', '')}" for m in msgs]
                return "Recent SMS:\n" + "\n".join(lines)
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 5. email (smtplib / imaplib in a thread) ────────────────────────────────────

async def email(params: dict, creds: dict) -> str:
    tool_id = "email"
    try:
        action = (params or {}).get("action", "send")

        if action == "send":
            missing = _require(creds, "smtp_host", "smtp_port", "username", "password")
            if missing:
                return _err(tool_id, f"missing credentials: {missing}")
            to = (params or {}).get("to")
            if not to:
                return _err(tool_id, "send requires params.to")
            subject = (params or {}).get("subject", "")
            body = (params or {}).get("body", "")
            host = creds["smtp_host"]
            port = int(creds["smtp_port"])
            username = creds["username"]
            password = creds["password"]

            def _send() -> str:
                msg = EmailMessage()
                msg["From"] = username
                msg["To"] = to
                msg["Subject"] = subject
                msg.set_content(body)
                server = smtplib.SMTP(host, port)
                try:
                    server.starttls()
                    server.login(username, password)
                    server.sendmail(username, [to], msg.as_string())
                finally:
                    server.quit()
                return f"Sent email to {to}."

            return await asyncio.to_thread(_send)

        if action == "read":
            missing = _require(creds, "imap_host", "username", "password")
            if missing:
                return _err(tool_id, f"missing credentials: {missing}")
            imap_host = creds["imap_host"]
            username = creds["username"]
            password = creds["password"]
            limit = int((params or {}).get("limit", 10))

            def _read() -> str:
                conn = imaplib.IMAP4_SSL(imap_host)
                try:
                    conn.login(username, password)
                    conn.select("INBOX")
                    typ, data = conn.search(None, "ALL")
                    ids = (data[0].split() if data and data[0] else [])
                    ids = ids[-limit:]
                    lines = []
                    for mid in reversed(ids):
                        typ, msg_data = conn.fetch(mid, "(RFC822)")
                        raw = msg_data[0][1] if msg_data and msg_data[0] else b""
                        parsed = email_lib.message_from_bytes(raw)
                        subj = _decode_hdr(parsed.get("Subject", ""))
                        sender = _decode_hdr(parsed.get("From", ""))
                        lines.append(f"- {sender}: {subj}")
                    if not lines:
                        return f"[{tool_id}] inbox is empty."
                    return "Recent inbox:\n" + "\n".join(lines)
                finally:
                    try:
                        conn.logout()
                    except Exception:  # noqa: BLE001
                        pass

            return await asyncio.to_thread(_read)

        return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


def _decode_hdr(value: str) -> str:
    try:
        parts = decode_header(value)
        out = []
        for text, enc in parts:
            if isinstance(text, bytes):
                out.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(text)
        return "".join(out)
    except Exception:  # noqa: BLE001
        return value


# ── 6. x_twitter ────────────────────────────────────────────────────────────────

async def x_twitter(params: dict, creds: dict) -> str:
    tool_id = "x_twitter"
    try:
        bearer = (creds or {}).get("bearer_token")
        if not bearer:
            return _err(tool_id, "missing credential: bearer_token")
        action = (params or {}).get("action", "read")
        headers = {"Authorization": f"Bearer {bearer}"}

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action == "read":
                query = (params or {}).get("query")
                if not query:
                    return _err(tool_id, "read requires params.query")
                resp = await client.get(
                    "https://api.twitter.com/2/tweets/search/recent",
                    params={"query": query})
                resp.raise_for_status()
                tweets = resp.json().get("data", [])
                if not tweets:
                    return f"[{tool_id}] no tweets found."
                return "Tweets:\n" + "\n".join(
                    f"- {t.get('text', '')}" for t in tweets)
            if action == "post":
                text = (params or {}).get("text", "")
                resp = await client.post("https://api.twitter.com/2/tweets",
                                         json={"text": text})
                resp.raise_for_status()
                tid = (resp.json().get("data") or {}).get("id", "?")
                return f"Posted tweet {tid}."
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 7. fetch_url (no creds) ─────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


async def fetch_url(params: dict, creds: dict) -> str:
    tool_id = "fetch_url"
    try:
        url = (params or {}).get("url")
        if not url:
            return _err(tool_id, "missing params.url")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        html = _SCRIPT_STYLE_RE.sub(" ", html)
        text = _TAG_RE.sub(" ", html)
        text = _WS_RE.sub(" ", text).strip()
        return _truncate(text, 2000)
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 8. webhook ──────────────────────────────────────────────────────────────────

async def webhook(params: dict, creds: dict) -> str:
    tool_id = "webhook"
    try:
        url = (creds or {}).get("url")
        if not url:
            return _err(tool_id, "missing credential: url")
        payload = (params or {}).get("payload", {})
        headers = {}
        secret = (creds or {}).get("signing_secret")
        if secret:
            headers["X-Signature"] = secret
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
        return f"Webhook POST to {url} returned {resp.status_code}."
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 9. rss_feed ─────────────────────────────────────────────────────────────────

_ITEM_TITLE_RE = re.compile(
    r"<item\b[^>]*>.*?<title\b[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_ENTRY_TITLE_RE = re.compile(
    r"<entry\b[^>]*>.*?<title\b[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def _clean_title(raw: str) -> str:
    m = _CDATA_RE.search(raw)
    if m:
        raw = m.group(1)
    return _WS_RE.sub(" ", _TAG_RE.sub("", raw)).strip()


async def rss_feed(params: dict, creds: dict) -> str:
    tool_id = "rss_feed"
    try:
        feed_url = (creds or {}).get("feed_url")
        if not feed_url:
            return _err(tool_id, "missing credential: feed_url")
        limit = int((params or {}).get("limit", 10))
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(feed_url)
            resp.raise_for_status()
            body = resp.text
        titles = [_clean_title(t) for t in _ITEM_TITLE_RE.findall(body)]
        if not titles:
            titles = [_clean_title(t) for t in _ENTRY_TITLE_RE.findall(body)]
        titles = [t for t in titles if t][:limit]
        if not titles:
            return f"[{tool_id}] no items found."
        return (f"Latest {len(titles)} item(s) from feed:\n"
                + "\n".join(f"- {t}" for t in titles))
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 10. rest_api ────────────────────────────────────────────────────────────────

async def rest_api(params: dict, creds: dict) -> str:
    tool_id = "rest_api"
    try:
        base_url = (creds or {}).get("base_url")
        if not base_url:
            return _err(tool_id, "missing credential: base_url")
        method = ((params or {}).get("method") or "GET").upper()
        path = (params or {}).get("path", "")
        body = (params or {}).get("body")
        url = base_url.rstrip("/") + "/" + str(path).lstrip("/") if path else base_url
        headers = {}
        auth_header = (creds or {}).get("auth_header")
        if auth_header:
            headers["Authorization"] = auth_header
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.request(method, url, headers=headers, json=body)
        return f"{method} {url} → {resp.status_code}: {_truncate(resp.text, 500)}"
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 11. slack ─────────────────────────────────────────────────────────────────

async def slack(params: dict, creds: dict) -> str:
    tool_id = "slack"
    try:
        token = (creds or {}).get("bot_token")
        if not token:
            return _err(tool_id, "missing credential: bot_token")
        action = (params or {}).get("action", "send")
        channel = (params or {}).get("channel") or (creds or {}).get("channel")
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action == "send":
                if not channel:
                    return _err(tool_id, "send requires a channel")
                text = (params or {}).get("text", "")
                resp = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    json={"channel": channel, "text": text})
                data = resp.json()
                if not data.get("ok"):
                    return _err(tool_id, data.get("error", "unknown Slack error"))
                return f"Sent to Slack channel {channel}."
            if action == "read":
                if not channel:
                    return _err(tool_id, "read requires a channel")
                limit = int((params or {}).get("limit", 10))
                resp = await client.get(
                    "https://slack.com/api/conversations.history",
                    params={"channel": channel, "limit": limit})
                data = resp.json()
                if not data.get("ok"):
                    return _err(tool_id, data.get("error", "unknown Slack error"))
                texts = [m.get("text", "") for m in data.get("messages", [])]
                texts = [t for t in texts if t]
                if not texts:
                    return f"[{tool_id}] no recent messages."
                return "Recent Slack messages:\n" + "\n".join(f"- {t}" for t in texts)
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 12. discord ───────────────────────────────────────────────────────────────

async def discord(params: dict, creds: dict) -> str:
    tool_id = "discord"
    try:
        missing = _require(creds, "bot_token", "channel_id")
        if missing:
            return _err(tool_id, f"missing credentials: {missing}")
        token, channel_id = creds["bot_token"], creds["channel_id"]
        action = (params or {}).get("action", "send")
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {token}"}

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action == "send":
                text = (params or {}).get("text", "")
                resp = await client.post(url, json={"content": text})
                resp.raise_for_status()
                return f"Sent to Discord channel {channel_id}."
            if action == "read":
                limit = int((params or {}).get("limit", 10))
                resp = await client.get(url, params={"limit": limit})
                resp.raise_for_status()
                msgs = resp.json()
                contents = [m.get("content", "") for m in msgs]
                contents = [c for c in contents if c]
                if not contents:
                    return f"[{tool_id}] no recent messages."
                return "Recent Discord messages:\n" + "\n".join(
                    f"- {c}" for c in contents)
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 13. github ────────────────────────────────────────────────────────────────

async def github(params: dict, creds: dict) -> str:
    tool_id = "github"
    try:
        missing = _require(creds, "access_token", "repo")
        if missing:
            return _err(tool_id, f"missing credentials: {missing}")
        token, repo = creds["access_token"], creds["repo"]
        action = (params or {}).get("action", "read")
        headers = {"Authorization": f"Bearer {token}",
                   "Accept": "application/vnd.github+json"}

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action == "read":
                per_page = int((params or {}).get("limit", 10))
                resp = await client.get(
                    f"https://api.github.com/repos/{repo}/issues",
                    params={"per_page": per_page})
                resp.raise_for_status()
                issues = resp.json()
                lines = [f"#{i.get('number')} {i.get('title', '')}" for i in issues]
                if not lines:
                    return f"[{tool_id}] no issues found."
                return f"Issues in {repo}:\n" + "\n".join(lines)
            if action == "create":
                title = (params or {}).get("title", "")
                body = (params or {}).get("body", "")
                resp = await client.post(
                    f"https://api.github.com/repos/{repo}/issues",
                    json={"title": title, "body": body})
                resp.raise_for_status()
                num = (resp.json() or {}).get("number", "?")
                return f"Created GitHub issue #{num} in {repo}."
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 14. notion ────────────────────────────────────────────────────────────────

def _notion_title(result: dict) -> str:
    props = result.get("properties") or {}
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            rich = prop.get("title") or []
            text = "".join(r.get("plain_text", "") for r in rich)
            if text:
                return text
    return result.get("id", "")


async def notion(params: dict, creds: dict) -> str:
    tool_id = "notion"
    try:
        token = (creds or {}).get("integration_token")
        if not token:
            return _err(tool_id, "missing credential: integration_token")
        action = (params or {}).get("action", "read")
        headers = {"Authorization": f"Bearer {token}",
                   "Notion-Version": "2022-06-28"}

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action in ("read", "search"):
                query = (params or {}).get("query", "")
                resp = await client.post(
                    "https://api.notion.com/v1/search", json={"query": query})
                resp.raise_for_status()
                results = (resp.json() or {}).get("results", [])
                titles = [_notion_title(r) for r in results]
                titles = [t for t in titles if t]
                if not titles:
                    return f"[{tool_id}] no results."
                return "Notion results:\n" + "\n".join(f"- {t}" for t in titles)
            if action == "create":
                parent_id = (params or {}).get("parent_id")
                if not parent_id:
                    return _err(tool_id, "parent_id required to create a page")
                title = (params or {}).get("title", "")
                resp = await client.post(
                    "https://api.notion.com/v1/pages",
                    json={"parent": {"page_id": parent_id},
                          "properties": {"title": {"title": [
                              {"text": {"content": title}}]}}})
                resp.raise_for_status()
                pid = (resp.json() or {}).get("id", "?")
                return f"Created Notion page {pid}."
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 15. airtable ──────────────────────────────────────────────────────────────

async def airtable(params: dict, creds: dict) -> str:
    tool_id = "airtable"
    try:
        missing = _require(creds, "api_key", "base_id")
        if missing:
            return _err(tool_id, f"missing credentials: {missing}")
        api_key, base_id = creds["api_key"], creds["base_id"]
        action = (params or {}).get("action", "read")
        table = (params or {}).get("table")
        if not table:
            return _err(tool_id, "requires params.table")
        url = f"https://api.airtable.com/v0/{base_id}/{table}"
        headers = {"Authorization": f"Bearer {api_key}"}

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action == "read":
                resp = await client.get(url)
                resp.raise_for_status()
                records = (resp.json() or {}).get("records", [])
                if not records:
                    return f"[{tool_id}] no records."
                lines = []
                for rec in records:
                    fields = rec.get("fields") or {}
                    preview = ", ".join(
                        f"{k}={v}" for k, v in list(fields.items())[:2])
                    lines.append(f"- {rec.get('id')}: {preview}")
                return f"Airtable records in {table}:\n" + "\n".join(lines)
            if action == "create":
                fields = (params or {}).get("fields") or {}
                resp = await client.post(url, json={"fields": fields})
                resp.raise_for_status()
                rid = (resp.json() or {}).get("id", "?")
                return f"Created Airtable record {rid} in {table}."
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 16. linear ────────────────────────────────────────────────────────────────

async def linear(params: dict, creds: dict) -> str:
    tool_id = "linear"
    try:
        api_key = (creds or {}).get("api_key")
        if not api_key:
            return _err(tool_id, "missing credential: api_key")
        action = (params or {}).get("action", "read")
        headers = {"Authorization": api_key, "Content-Type": "application/json"}
        url = "https://api.linear.app/graphql"

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action == "read":
                first = int((params or {}).get("limit", 10))
                query = (
                    "{ issues(first: %d) { nodes { identifier title "
                    "state { name } } } }" % first)
                resp = await client.post(url, json={"query": query})
                resp.raise_for_status()
                nodes = (((resp.json() or {}).get("data") or {}).get("issues")
                         or {}).get("nodes", [])
                if not nodes:
                    return f"[{tool_id}] no issues."
                lines = [
                    f"{n.get('identifier')} {n.get('title', '')} "
                    f"({(n.get('state') or {}).get('name', '?')})"
                    for n in nodes]
                return "Linear issues:\n" + "\n".join(lines)
            if action == "create":
                team_id = (params or {}).get("team_id")
                if not team_id:
                    return _err(tool_id, "team_id required to create an issue")
                title = (params or {}).get("title", "")
                mutation = (
                    "mutation($t: String!, $tid: String!) { "
                    "issueCreate(input: {title: $t, teamId: $tid}) "
                    "{ success issue { identifier } } }")
                resp = await client.post(
                    url, json={"query": mutation,
                               "variables": {"t": title, "tid": team_id}})
                resp.raise_for_status()
                issue = ((((resp.json() or {}).get("data") or {}).get(
                    "issueCreate") or {}).get("issue") or {})
                return f"Created Linear issue {issue.get('identifier', '?')}."
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 17. linkedin ──────────────────────────────────────────────────────────────

async def linkedin(params: dict, creds: dict) -> str:
    tool_id = "linkedin"
    try:
        token = (creds or {}).get("access_token")
        if not token:
            return _err(tool_id, "missing credential: access_token")
        action = (params or {}).get("action", "post")

        if action == "read":
            return ("[linkedin] note: the LinkedIn API does not expose feed "
                    "reads to third-party apps.")
        if action == "post":
            author_urn = (params or {}).get("author_urn")
            if not author_urn:
                return _err(tool_id, "author_urn required to post")
            text = (params or {}).get("text", "")
            headers = {"Authorization": f"Bearer {token}",
                       "X-Restli-Protocol-Version": "2.0.0"}
            payload = {
                "author": author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                },
            }
            async with httpx.AsyncClient(timeout=30, headers=headers) as client:
                resp = await client.post(
                    "https://api.linkedin.com/v2/ugcPosts", json=payload)
                resp.raise_for_status()
                return "Published LinkedIn post."
        return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 18. instagram ─────────────────────────────────────────────────────────────

async def instagram(params: dict, creds: dict) -> str:
    tool_id = "instagram"
    try:
        missing = _require(creds, "access_token", "account_id")
        if missing:
            return _err(tool_id, f"missing credentials: {missing}")
        token, account_id = creds["access_token"], creds["account_id"]
        action = (params or {}).get("action", "read")
        base = f"https://graph.facebook.com/v19.0/{account_id}"

        async with httpx.AsyncClient(timeout=30) as client:
            if action == "read":
                resp = await client.get(
                    f"{base}/media",
                    params={"fields": "caption,timestamp",
                            "access_token": token})
                resp.raise_for_status()
                data = (resp.json() or {}).get("data", [])
                captions = [d.get("caption", "") for d in data]
                captions = [c for c in captions if c]
                if not captions:
                    return f"[{tool_id}] no recent media."
                return "Recent Instagram captions:\n" + "\n".join(
                    f"- {c}" for c in captions)
            if action == "publish":
                image_url = (params or {}).get("image_url")
                if not image_url:
                    return _err(tool_id, "image_url required to publish")
                caption = (params or {}).get("caption", "")
                resp = await client.post(
                    f"{base}/media",
                    data={"image_url": image_url, "caption": caption,
                          "access_token": token})
                resp.raise_for_status()
                creation_id = (resp.json() or {}).get("id")
                resp2 = await client.post(
                    f"{base}/media_publish",
                    data={"creation_id": creation_id, "access_token": token})
                resp2.raise_for_status()
                pub_id = (resp2.json() or {}).get("id", "?")
                return f"Published Instagram media {pub_id}."
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── shared Google service-account helper ────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def _google_access_token(service_account_json: str, scope: str) -> str:
    """Mint an OAuth access token from a service-account JSON for the given scope."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    info = json_lib.loads(service_account_json)
    client_email = info["client_email"]
    private_key_pem = info["private_key"]
    token_uri = info.get("token_uri", "https://oauth2.googleapis.com/token")

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": client_email,
        "scope": scope,
        "aud": token_uri,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = (
        _b64url(json_lib.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json_lib.dumps(claims, separators=(",", ":")).encode("utf-8"))
    )
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None)
    signature = private_key.sign(
        signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    assertion = signing_input + "." + _b64url(signature)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            token_uri,
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                  "assertion": assertion})
        resp.raise_for_status()
        return resp.json()["access_token"]


# ── 19. google_sheets ─────────────────────────────────────────────────────────

async def google_sheets(params: dict, creds: dict) -> str:
    tool_id = "google_sheets"
    try:
        missing = _require(creds, "service_account_json", "sheet_id")
        if missing:
            return _err(tool_id, f"missing credentials: {missing}")
        sheet_id = creds["sheet_id"]
        action = (params or {}).get("action", "read")
        rng = (params or {}).get("range", "A1:Z20")
        token = await _google_access_token(
            creds["service_account_json"],
            "https://www.googleapis.com/auth/spreadsheets")
        headers = {"Authorization": f"Bearer {token}"}
        base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values"

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action == "read":
                resp = await client.get(f"{base}/{rng}")
                resp.raise_for_status()
                values = (resp.json() or {}).get("values", [])
                if not values:
                    return f"[{tool_id}] range {rng} is empty."
                lines = [", ".join(str(c) for c in row) for row in values]
                return f"Values from {rng}:\n" + "\n".join(lines)
            if action == "append":
                row = (params or {}).get("row")
                if row is None:
                    row = (params or {}).get("values")
                if row is None:
                    return _err(tool_id, "append requires params.row or params.values")
                resp = await client.post(
                    f"{base}/{rng}:append",
                    params={"valueInputOption": "RAW"},
                    json={"values": [row]})
                resp.raise_for_status()
                return f"Appended a row to {sheet_id}."
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── 20. google_calendar ───────────────────────────────────────────────────────

async def google_calendar(params: dict, creds: dict) -> str:
    tool_id = "google_calendar"
    try:
        missing = _require(creds, "service_account_json", "calendar_id")
        if missing:
            return _err(tool_id, f"missing credentials: {missing}")
        calendar_id = creds["calendar_id"]
        action = (params or {}).get("action", "read")
        token = await _google_access_token(
            creds["service_account_json"],
            "https://www.googleapis.com/auth/calendar")
        headers = {"Authorization": f"Bearer {token}"}
        base = (f"https://www.googleapis.com/calendar/v3/calendars/"
                f"{calendar_id}/events")

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            if action == "read":
                max_results = int((params or {}).get("limit", 10))
                resp = await client.get(
                    base,
                    params={"maxResults": max_results, "singleEvents": "true",
                            "orderBy": "startTime"})
                resp.raise_for_status()
                items = (resp.json() or {}).get("items", [])
                summaries = [i.get("summary", "(no title)") for i in items]
                if not summaries:
                    return f"[{tool_id}] no upcoming events."
                return "Upcoming events:\n" + "\n".join(
                    f"- {s}" for s in summaries)
            if action == "create":
                summary = (params or {}).get("summary", "")
                start = (params or {}).get("start")
                end = (params or {}).get("end")
                resp = await client.post(
                    base,
                    json={"summary": summary,
                          "start": {"dateTime": start},
                          "end": {"dateTime": end}})
                resp.raise_for_status()
                eid = (resp.json() or {}).get("id", "?")
                return f"Created calendar event {eid}."
            return _err(tool_id, f"unknown action: {action}")
    except Exception as e:  # noqa: BLE001
        return _err(tool_id, e)


# ── registry ────────────────────────────────────────────────────────────────────

IMPLS: dict[str, Callable] = {
    "csv_file": csv_file,
    "sql_database": sql_database,
    "telegram": telegram,
    "sms": sms,
    "email": email,
    "x_twitter": x_twitter,
    "fetch_url": fetch_url,
    "webhook": webhook,
    "rss_feed": rss_feed,
    "rest_api": rest_api,
    "slack": slack,
    "discord": discord,
    "github": github,
    "notion": notion,
    "airtable": airtable,
    "linear": linear,
    "linkedin": linkedin,
    "instagram": instagram,
    "google_sheets": google_sheets,
    "google_calendar": google_calendar,
}
