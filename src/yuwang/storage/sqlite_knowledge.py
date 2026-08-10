"""RAG 知识文档与片段的 SQLite 存储。"""

from __future__ import annotations

from uuid import UUID

from yuwang.knowledge import KnowledgeChunk, KnowledgeDocument
from yuwang.storage.sqlite_common import SQLiteStore


class SQLiteKnowledgeStore(SQLiteStore):
    def save_knowledge_document(
        self, document: KnowledgeDocument, chunks: list[KnowledgeChunk]
    ) -> KnowledgeDocument:
        if len(chunks) != document.chunk_count:
            raise ValueError("知识文档片段数与索引不一致")
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR REPLACE INTO knowledge_documents VALUES(?,?,?)",
                (str(document.id), document.model_dump_json(), document.created_at.isoformat()),
            )
            db.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (str(document.id),))
            db.executemany(
                "INSERT INTO knowledge_chunks(id,document_id,ordinal,data) VALUES(?,?,?,?)",
                [
                    (str(chunk.id), str(chunk.document_id), chunk.ordinal, chunk.model_dump_json())
                    for chunk in chunks
                ],
            )
        return document

    def get_knowledge_document(
        self, document_id: UUID | str
    ) -> KnowledgeDocument | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM knowledge_documents WHERE id=?", (str(document_id),)
            ).fetchone()
        return KnowledgeDocument.model_validate_json(row["data"]) if row else None

    def list_knowledge_documents(self) -> list[KnowledgeDocument]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM knowledge_documents ORDER BY created_at,id"
            ).fetchall()
        return [KnowledgeDocument.model_validate_json(row["data"]) for row in rows]

    def list_knowledge_chunks(
        self, document_id: UUID | str | None = None
    ) -> list[KnowledgeChunk]:
        with self.connect() as db:
            if document_id is None:
                rows = db.execute(
                    "SELECT data FROM knowledge_chunks ORDER BY document_id,ordinal"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT data FROM knowledge_chunks WHERE document_id=? ORDER BY ordinal",
                    (str(document_id),),
                ).fetchall()
        return [KnowledgeChunk.model_validate_json(row["data"]) for row in rows]

    def update_knowledge_document(self, document: KnowledgeDocument) -> KnowledgeDocument:
        with self._lock, self.connect() as db:
            cursor = db.execute(
                "UPDATE knowledge_documents SET data=? WHERE id=?",
                (document.model_dump_json(), str(document.id)),
            )
            if cursor.rowcount == 0:
                raise KeyError("知识文档不存在")
        return document

    def delete_knowledge_document(self, document_id: UUID | str) -> None:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "DELETE FROM knowledge_documents WHERE id=?", (str(document_id),)
            )
            if cursor.rowcount == 0:
                raise KeyError("知识文档不存在")
