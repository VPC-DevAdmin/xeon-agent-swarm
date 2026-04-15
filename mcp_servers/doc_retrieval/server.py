"""
Document retrieval MCP server — ChromaDB-backed RAG.

Maintains a local ChromaDB collection. Workers can:
  - add_document: ingest text with metadata
  - search_documents: semantic search over the collection

Speaks JSON-RPC 2.0 over HTTP (MCP streamable-HTTP transport).
"""
import os
import uuid
from fastapi import FastAPI

app = FastAPI(title="MCP Doc Retrieval Server")

TOOLS = [
    {
        "name": "search_documents",
        "description": "Semantic search over the document collection",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 3},
                "collection": {"type": "string", "default": "default"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "add_document",
        "description": "Ingest a document into the collection",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Document text"},
                "metadata": {"type": "object", "description": "Optional metadata"},
                "collection": {"type": "string", "default": "default"},
            },
            "required": ["text"],
        },
    },
]

_chroma_client = None
_collections: dict = {}


def _get_collection(name: str = "default"):
    global _chroma_client, _collections
    if _chroma_client is None:
        try:
            import chromadb
            _chroma_client = chromadb.Client()
        except ImportError:
            return None
    if name not in _collections:
        _collections[name] = _chroma_client.get_or_create_collection(name)
    return _collections[name]


async def search_documents(query: str, max_results: int, collection: str) -> str:
    col = _get_collection(collection)
    if col is None:
        return "ChromaDB not available."
    try:
        results = col.query(query_texts=[query], n_results=max_results)
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        if not docs:
            return "No matching documents found."
        lines = []
        for i, (doc, meta) in enumerate(zip(docs, metas), 1):
            source = meta.get("source", "unknown") if meta else "unknown"
            lines.append(f"**[{i}] Source: {source}**\n{doc[:500]}")
        return "\n\n".join(lines)
    except Exception as exc:
        return f"Search error: {exc}"


async def add_document(text: str, metadata: dict | None, collection: str) -> str:
    col = _get_collection(collection)
    if col is None:
        return "ChromaDB not available."
    try:
        doc_id = str(uuid.uuid4())
        col.add(
            documents=[text],
            metadatas=[metadata or {}],
            ids=[doc_id],
        )
        return f"Document added with id={doc_id}"
    except Exception as exc:
        return f"Add error: {exc}"


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
        collection = arguments.get("collection", "default")

        if tool_name == "search_documents":
            result = await search_documents(
                arguments.get("query", ""),
                arguments.get("max_results", 3),
                collection,
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": result}]},
            }

        if tool_name == "add_document":
            result = await add_document(
                arguments.get("text", ""),
                arguments.get("metadata"),
                collection,
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": result}]},
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


@app.get("/health")
def health():
    return {"status": "ok", "server": "mcp-doc-retrieval"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 9002)))
