"""
Sandboxed Python code execution MCP server.

Executes Python code in a restricted namespace with a timeout.
No filesystem writes, no network access, no subprocess calls.

Speaks JSON-RPC 2.0 over HTTP (MCP streamable-HTTP transport).
"""
import os
import ast
import sys
import io
import contextlib
import signal
import threading
from fastapi import FastAPI

app = FastAPI(title="MCP Code Exec Server")

TOOLS = [
    {
        "name": "execute_python",
        "description": "Execute Python code in a sandboxed environment and return stdout",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "timeout_seconds": {"type": "integer", "default": 10},
            },
            "required": ["code"],
        },
    },
    {
        "name": "check_syntax",
        "description": "Check Python code for syntax errors without executing",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to check"},
            },
            "required": ["code"],
        },
    },
]

# ── Blocked modules/builtins ──────────────────────────────────────────────────
_BLOCKED_IMPORTS = {
    "os", "sys", "subprocess", "socket", "urllib", "urllib2",
    "http", "ftplib", "smtplib", "shutil", "glob", "pathlib",
    "importlib", "ctypes", "multiprocessing", "threading",
    "__builtins__",
}


def _is_safe(code: str) -> tuple[bool, str]:
    """Basic AST check to block dangerous constructs."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _BLOCKED_IMPORTS:
                    return False, f"Import of '{alias.name}' is not allowed."
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BLOCKED_IMPORTS:
                return False, f"Import of '{node.module}' is not allowed."
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec", "compile", "__import__"):
                return False, f"Call to '{node.func.id}' is not allowed."

    return True, ""


def _run_with_timeout(code: str, timeout: int) -> tuple[str, str]:
    """Execute code capturing stdout/stderr, with a thread-based timeout."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    result = {"done": False, "error": None}

    safe_globals = {
        "__builtins__": {
            "print": print,
            "range": range,
            "len": len,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "sorted": sorted,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "isinstance": isinstance,
            "type": type,
            "repr": repr,
            "format": format,
        }
    }

    def _execute():
        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(compile(code, "<sandbox>", "exec"), safe_globals)  # noqa: S102
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            result["done"] = True

    t = threading.Thread(target=_execute, daemon=True)
    t.start()
    t.join(timeout)

    if not result["done"]:
        return "", f"TimeoutError: execution exceeded {timeout}s"

    stderr = stderr_buf.getvalue()
    if result["error"]:
        stderr += result["error"]

    return stdout_buf.getvalue(), stderr


@app.post("/mcp")
async def mcp_endpoint(request: dict):
    method = request.get("method")
    req_id = request.get("id", 1)

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "check_syntax":
            code = arguments.get("code", "")
            try:
                ast.parse(code)
                text = "Syntax OK"
            except SyntaxError as e:
                text = f"SyntaxError: {e}"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }

        if tool_name == "execute_python":
            code = arguments.get("code", "")
            timeout = min(arguments.get("timeout_seconds", 10), 30)

            safe, reason = _is_safe(code)
            if not safe:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"Blocked: {reason}"}]},
                }

            stdout, stderr = _run_with_timeout(code, timeout)
            output = stdout
            if stderr:
                output += f"\n[stderr]\n{stderr}"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": output or "(no output)"}]},
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


@app.get("/health")
def health():
    return {"status": "ok", "server": "mcp-code-exec"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 9003)))
