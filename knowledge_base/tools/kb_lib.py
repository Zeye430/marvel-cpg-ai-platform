"""Shared logic for the Marvel CPG RAG knowledge base.

Parsing, registry, section-aware chunking, BM25 and hybrid retrieval. Needs only the
Python standard library and numpy; dense query embedding uses fastembed when installed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

KB_ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = KB_ROOT / "documents"
PDF_DIR = KB_ROOT / "pdf"
INDEX_DIR = KB_ROOT / "index"
REGISTRY_PATH = KB_ROOT / "registry.json"

# Registry fields required by data_platform/contracts/v3/module-data-contract.json (module_4.knowledge_contract)
REQUIRED_FIELDS = ("document_id", "version", "content_hash", "authorized_source",
                   "effective_date", "permission_scope", "citation_location")
FRONT_MATTER_FIELDS = ("document_id", "title", "version", "effective_date", "document_type", "origin",
                       "authorized_source", "permission_scope", "status", "owner", "data_basis")
ROLES = ("sales", "manager")  # access roles defined in the module data contract
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
MAX_CHUNK_WORDS = 300
MIN_SECTION_WORDS = 8

HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*)$")


# ---------------------------------------------------------------- documents

@dataclass
class Section:
    level: int
    number: str | None
    title: str
    path: list[str]
    lines: list[str] = field(default_factory=list)

    @property
    def section_id(self) -> str:
        return self.number or slug(self.title)

    @property
    def label(self) -> str:
        return f"{self.number} {self.title}" if self.number else self.title


@dataclass
class Document:
    path: Path
    meta: dict
    body: str

    @property
    def doc_id(self) -> str:
        return self.meta["document_id"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def content_hash(path: Path) -> str:
    """SHA-256 of the document file with normalised line endings."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def parse_document(path: Path) -> Document:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: missing front matter")
    end = text.index("\n---\n", 4)
    meta = {}
    for line in text[4:end].splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    meta["permission_scope"] = [r.strip() for r in meta.get("permission_scope", "").split(",") if r.strip()]
    if meta.get("related_documents"):
        meta["related_documents"] = [r.strip() for r in meta["related_documents"].split(",")]
    return Document(path, meta, text[end + 5:])


def load_documents(doc_dir: Path = DOC_DIR) -> list[Document]:
    docs = [parse_document(p) for p in sorted(doc_dir.glob("*.md"))]
    problems = validate_documents(docs)
    if problems:
        raise ValueError("Document validation failed:\n" + "\n".join(problems))
    return docs


def validate_documents(docs: list[Document]) -> list[str]:
    problems, seen = [], set()
    for d in docs:
        missing = [f for f in FRONT_MATTER_FIELDS if not d.meta.get(f)]
        if missing:
            problems.append(f"{d.path.name}: missing {missing}")
        if not d.path.name.startswith(d.meta.get("document_id", "?") + "_"):
            problems.append(f"{d.path.name}: file name must start with its document_id")
        if d.meta.get("document_id") in seen:
            problems.append(f"{d.path.name}: duplicate document_id")
        seen.add(d.meta.get("document_id"))
        bad = [r for r in d.meta["permission_scope"] if r not in ROLES]
        if bad or not d.meta["permission_scope"]:
            problems.append(f"{d.path.name}: permission_scope must list roles from {ROLES}")
        if not re.fullmatch(r"\d+\.\d+\.\d+", d.meta.get("version", "")):
            problems.append(f"{d.path.name}: version must look like 1.0.0")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.meta.get("effective_date", "")):
            problems.append(f"{d.path.name}: effective_date must be YYYY-MM-DD")
    return problems


def split_sections(doc: Document) -> list[Section]:
    """Split the Markdown body at #, ## and ### headings (headings inside code fences are ignored)."""
    sections: list[Section] = []
    stack: dict[int, str] = {}
    current = Section(0, None, "Preamble", [])
    in_code = False
    for line in doc.body.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            sections.append(current)
            level, title = len(m.group(1)), m.group(2).strip()
            n = NUMBERED.match(title)
            number, name = (n.group(1), n.group(2)) if n else (None, title)
            stack = {k: v for k, v in stack.items() if k < level}
            stack[level] = title
            current = Section(level, number, name, [stack[k] for k in sorted(stack)])
        else:
            current.lines.append(line)
    sections.append(current)
    return sections


# ---------------------------------------------------------------- chunking

def _blocks(lines: list[str]) -> list[list[str]]:
    """Group lines into paragraphs, lists, tables, quotes and code blocks."""
    blocks, cur, kind, in_code = [], [], None, False

    def flush():
        nonlocal cur, kind
        if any(x.strip() for x in cur):
            blocks.append(cur)
        cur, kind = [], None

    for line in lines:
        if line.startswith("```"):
            if not in_code:
                flush()
            in_code = not in_code
            cur.append(line)
            if not in_code:
                flush()
            continue
        if in_code:
            cur.append(line)
            continue
        if not line.strip():
            if kind != "list":
                flush()
            continue
        k = ("table" if line.lstrip().startswith("|") else
             "list" if re.match(r"^\s*(?:[-*]|\d+\.)\s", line) else
             "quote" if line.startswith(">") else "text")
        if kind and k != kind and not (kind == "list" and k == "text" and line.startswith("  ")):
            flush()
        kind = kind or k
        cur.append(line)
    flush()
    return blocks


def _words(lines: list[str]) -> int:
    return sum(len(x.split()) for x in lines)


def _split_table(block: list[str], limit: int) -> list[list[str]]:
    header, rows = block[:2], block[2:]
    parts, cur = [], []
    for row in rows:
        if cur and _words(header + cur + [row]) > limit:
            parts.append(header + cur)
            cur = []
        cur.append(row)
    parts.append(header + cur)
    return parts


def chunk_document(doc: Document, pages: dict | None = None, limit: int = MAX_CHUNK_WORDS) -> list[dict]:
    """Section-aware chunks. Each chunk keeps its document identity, section path and citation."""
    pages = pages or {}
    chunks = []
    title = doc.meta["title"]
    for sec in split_sections(doc):
        if sec.level <= 1:
            continue  # title and fictional-company notice: kept in metadata, not embedded
        blocks = [b for b in _blocks(sec.lines) if not b[0].startswith(">")]
        if _words([x for b in blocks for x in b]) < MIN_SECTION_WORDS:
            continue
        pieces: list[list[str]] = []
        for b in blocks:
            pieces += _split_table(b, limit) if b[0].lstrip().startswith("|") and _words(b) > limit else [b]
        groups, cur = [], []
        for p in pieces:
            if cur and _words([x for q in cur for x in q]) + _words(p) > limit:
                groups.append(cur)
                cur = []
            cur.append(p)
        if cur:
            groups.append(cur)
        heading_path = " > ".join(sec.path)
        page = pages.get(sec.label)
        for i, g in enumerate(groups, 1):
            text = "\n\n".join("\n".join(p) for p in g).strip()
            cid = f"{doc.doc_id}@{doc.meta['version']}#{sec.section_id}" + (f"-{i}" if len(groups) > 1 else "")
            citation = f"{doc.doc_id} v{doc.meta['version']}, §{sec.label}" + (f", PDF p. {page}" if page else "")
            chunks.append({
                "chunk_id": cid, "document_id": doc.doc_id, "version": doc.meta["version"],
                "title": title, "section": sec.number or sec.title, "section_title": sec.title,
                "heading_path": heading_path, "page": page, "citation": citation,
                "permission_scope": doc.meta["permission_scope"], "origin": doc.meta["origin"],
                "document_type": doc.meta["document_type"], "effective_date": doc.meta["effective_date"],
                "text": text,
                "embedding_text": f"{title} | {heading_path}\n{text}",
                "chunk_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
            })
    return chunks


# ---------------------------------------------------------------- registry

def registry_entry(doc: Document, pdf_name: str, n_pages: int | None, n_chunks: int) -> dict:
    m = doc.meta
    return {
        "document_id": m["document_id"], "version": m["version"], "content_hash": content_hash(doc.path),
        "authorized_source": m["authorized_source"], "effective_date": m["effective_date"],
        "permission_scope": m["permission_scope"],
        "citation_location": f"knowledge_base/pdf/{pdf_name}; cite as '{m['document_id']} v{m['version']}, §<section>, PDF p. <page>'",
        "title": m["title"], "document_type": m["document_type"], "origin": m["origin"], "status": m["status"],
        "owner": m["owner"], "data_basis": m["data_basis"], "related_documents": m.get("related_documents", []),
        "source_path": f"knowledge_base/documents/{doc.path.name}", "pdf_path": f"knowledge_base/pdf/{pdf_name}",
        "pdf_pages": n_pages, "chunk_count": n_chunks,
    }


# ---------------------------------------------------------------- retrieval

STOP = set("""a an and are as at be but by can could do does for from had has have how i if in into is it its
me my of on or our should so than that the their them then there these they this to was we what when where which
who why will with would you your about any all also get give""".split())


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*", text.lower())
    out = []
    for t in toks:
        if t in STOP:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss") and not t[-2].isdigit():
            t = t[:-1]
        out.append(t)
    return out


class BM25:
    def __init__(self, texts: list[str], k1: float = 1.4, b: float = 0.75):
        self.docs = [Counter(tokenize(t)) for t in texts]
        self.len = np.array([sum(d.values()) for d in self.docs], dtype=float)
        self.avg = self.len.mean() if len(self.len) else 0.0
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query: str) -> np.ndarray:
        q = tokenize(query)
        s = np.zeros(len(self.docs))
        for i, d in enumerate(self.docs):
            norm = self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)
            for t in q:
                f = d.get(t)
                if f:
                    s[i] += self.idf[t] * f * (self.k1 + 1) / (f + norm)
        return s


class KnowledgeBase:
    """Permission-aware hybrid retrieval over the exported index.

    search() filters chunks by the caller's verified role BEFORE ranking, so a
    document outside the role's permission_scope can never be returned or leak
    into the generation context.
    """

    def __init__(self, index_dir: Path = INDEX_DIR, use_dense: bool = True):
        self.index_dir = Path(index_dir)
        self.manifest = json.loads((self.index_dir / "manifest.json").read_text())
        self.chunks = [json.loads(x) for x in (self.index_dir / "chunks.jsonl").read_text().splitlines() if x]
        self.bm25 = BM25([c["embedding_text"] for c in self.chunks])
        self.vectors, self.model = None, None
        vec_file = self.index_dir / self.manifest["dense"]["file"]
        if use_dense and vec_file.exists():
            try:
                from fastembed import TextEmbedding
            except ImportError:
                TextEmbedding = None  # fastembed not installed: lexical retrieval only
            if TextEmbedding is not None:
                name = self.manifest["dense"]["model"]
                try:
                    # Cached model: no Hugging Face round-trips (those can stall start-up for minutes).
                    self.model = TextEmbedding(name, local_files_only=True)
                except Exception:  # noqa: BLE001 - not cached yet: download once
                    self.model = TextEmbedding(name)
                self.vectors = np.load(vec_file)

    def embed_query(self, query: str) -> np.ndarray:
        v = np.asarray(next(iter(self.model.query_embed(query))), dtype=np.float32)
        return v / np.linalg.norm(v)

    def search(self, query: str, role: str, k: int = 5, min_dense: float | None = None) -> dict:
        if role not in ROLES:
            raise PermissionError(f"Unknown role {role!r}: no documents are accessible")
        allowed = np.array([role in c["permission_scope"] for c in self.chunks])
        lex = self.bm25.scores(query)
        lex[~allowed] = -np.inf
        rank_lex = np.argsort(-lex)
        fused = np.zeros(len(self.chunks))
        dense = None
        if self.model is not None:
            dense = self.vectors @ self.embed_query(query)
            dense[~allowed] = -np.inf
            for r, i in enumerate(np.argsort(-dense)):
                if allowed[i]:
                    fused[i] += 1 / (60 + r + 1)
        for r, i in enumerate(rank_lex):
            if allowed[i] and lex[i] > 0:
                fused[i] += 1 / (60 + r + 1)
        fused[~allowed] = -np.inf
        order = [i for i in np.argsort(-fused) if allowed[i] and fused[i] > 0][:k]
        best_dense = float(dense[allowed].max()) if dense is not None and allowed.any() else None
        threshold = self.manifest["retrieval"]["min_dense_score"] if min_dense is None else min_dense
        if not order or (best_dense is not None and best_dense < threshold) or (dense is None and lex.max() <= 0):
            return {"status": "no_relevant_evidence", "best_dense_score": best_dense, "results": []}
        results = []
        for i in order:
            c = self.chunks[i]
            results.append({"chunk_id": c["chunk_id"], "document_id": c["document_id"], "version": c["version"],
                            "section": c["section"], "citation": c["citation"], "title": c["title"],
                            "heading_path": c["heading_path"], "text": c["text"],
                            "dense_score": None if dense is None else round(float(dense[i]), 4),
                            "lexical_score": round(float(lex[i]), 3), "fused_score": round(float(fused[i]), 5)})
        return {"status": "ok", "best_dense_score": best_dense, "results": results}
