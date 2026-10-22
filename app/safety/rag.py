"""Knowledge base RAG retrieval module for TickTock Meals.

Parses official food safety guidance notes, chunks and embeds them into SQLite,
and retrieves relevant citations using cosine similarity.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Union
import numpy as np

from app.db import get_connection
from app.llm.client import LLMClient


def init_kb_table(conn: sqlite3.Connection) -> None:
    """Create kb_chunks table in SQLite if it does not exist."""
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_chunks (
                id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                source TEXT NOT NULL,
                heading TEXT NOT NULL,
                text TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                vector BLOB NOT NULL
            );
            """
        )


def _chunk_markdown(content: str, file_stem: str) -> List[Dict[str, str]]:
    """Parse source header and split markdown document into logical chunks."""
    lines = content.strip().splitlines()
    source = "Official Food Safety Guidance"
    body_lines: List[str] = []

    for line in lines:
        if line.lower().startswith("source:"):
            source = line[len("source:") :].strip()
        else:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()

    # Split by markdown headers (# or ##)
    sections = re.split(r"(?m)^(?=#{1,3}\s+)", body)
    chunks: List[Dict[str, str]] = []

    for idx, sec in enumerate(sections):
        sec_clean = sec.strip()
        if not sec_clean:
            continue

        # Extract heading
        heading = f"Section {idx + 1}"
        first_line = sec_clean.splitlines()[0]
        if first_line.startswith("#"):
            heading = first_line.lstrip("#").strip()

        chunk_id = f"{file_stem}_{idx + 1}"
        chunks.append(
            {
                "id": chunk_id,
                "source": source,
                "heading": heading,
                "text": sec_clean,
            }
        )

    return chunks


def index_knowledge_base(
    kb_dir: Union[str, Path] = "app/safety/kb",
    conn: Optional[sqlite3.Connection] = None,
    client: Optional[LLMClient] = None,
    force: bool = False,
) -> int:
    """Index or re-index markdown guidance notes into SQLite vector store."""
    c = conn or get_connection()
    init_kb_table(c)
    llm = client or LLMClient()
    path = Path(kb_dir)

    if not path.is_dir():
        return 0

    total_chunks = 0
    md_files = sorted(list(path.glob("*.md")))

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        stem = md_file.stem

        if not force:
            # Check if current hash already stored
            cur = c.execute(
                "SELECT COUNT(*) as cnt FROM kb_chunks WHERE file_name = ? AND file_hash = ?",
                (md_file.name, file_hash),
            )
            row = cur.fetchone()
            if row and row["cnt"] > 0:
                total_chunks += row["cnt"]
                continue

        # File changed or new: delete old chunks and re-embed
        with c:
            c.execute("DELETE FROM kb_chunks WHERE file_name = ?", (md_file.name,))

        chunks = _chunk_markdown(content, stem)
        for chunk in chunks:
            # Generate embedding vector
            vec = llm.embed(chunk["text"])
            vec_bytes = np.array(vec, dtype=np.float32).tobytes()

            with c:
                c.execute(
                    """
                    INSERT INTO kb_chunks (id, file_name, source, heading, text, file_hash, vector)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["id"],
                        md_file.name,
                        chunk["source"],
                        chunk["heading"],
                        chunk["text"],
                        file_hash,
                        vec_bytes,
                    ),
                )
            total_chunks += 1

    return total_chunks


def retrieve(
    query: str,
    k: int = 4,
    conn: Optional[sqlite3.Connection] = None,
    client: Optional[LLMClient] = None,
    kb_dir: Union[str, Path] = "app/safety/kb",
) -> List[Dict[str, Any]]:
    """Retrieve top-k relevant food safety knowledge chunks for a query using cosine similarity."""
    c = conn or get_connection()
    init_kb_table(c)
    llm = client or LLMClient()

    # Ensure KB is indexed
    cur = c.execute("SELECT COUNT(*) as cnt FROM kb_chunks")
    if cur.fetchone()["cnt"] == 0:
        index_knowledge_base(kb_dir=kb_dir, conn=c, client=llm)

    # Embed query
    raw_embed = llm.embed(query)
    if not isinstance(raw_embed, (list, tuple, np.ndarray)) or len(raw_embed) == 0:
        from app.llm.client import _pseudo_embed
        raw_embed = _pseudo_embed(query)

    query_vec = np.array(raw_embed, dtype=np.float32)
    query_norm = np.linalg.norm(query_vec)
    if query_norm > 0:
        query_vec = query_vec / query_norm

    # Fetch all vectors
    cursor = c.execute("SELECT id, source, heading, text, vector FROM kb_chunks")
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for row in cursor.fetchall():
        chunk_vec = np.frombuffer(row["vector"], dtype=np.float32)
        chunk_norm = np.linalg.norm(chunk_vec)
        if chunk_norm > 0:
            norm_chunk_vec = chunk_vec / chunk_norm
            similarity = float(np.dot(query_vec, norm_chunk_vec))
        else:
            similarity = 0.0

        scored.append(
            (
                similarity,
                {
                    "id": row["id"],
                    "source": row["source"],
                    "heading": row["heading"],
                    "text": row["text"],
                    "score": round(similarity, 4),
                },
            )
        )

    # Sort descending by similarity
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:k]]
