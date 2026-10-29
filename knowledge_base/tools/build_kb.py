"""Build the Marvel CPG RAG knowledge base.

    python knowledge_base/tools/build_kb.py            # validate, PDFs, registry, chunks, embeddings
    python knowledge_base/tools/build_kb.py --no-pdf   # skip PDF rendering (registry/chunks/index only)

Pipeline: Markdown source -> validation -> PDF (with section page map) -> registry.json
-> section-aware chunks (index/chunks.jsonl) -> dense embeddings (index/*.npy) -> index/manifest.json.
Requires: numpy, fpdf2 (PDFs), fastembed (embeddings). See knowledge_base/requirements.txt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_lib import (DOC_DIR, EMBEDDING_MODEL, INDEX_DIR, KB_ROOT, MAX_CHUNK_WORDS, PDF_DIR, REGISTRY_PATH,  # noqa: E402
                    chunk_document, load_documents, registry_entry, split_sections)

FONT_CANDIDATES = [  # (regular, bold, italic)
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
]
ACCESS_LABEL = {("manager", "sales"): "Field representatives and regional managers", ("manager",): "Regional managers only"}


# ---------------------------------------------------------------- PDF rendering

def inline(text: str) -> str:
    """Convert Markdown inline markup to fpdf2's markdown subset (**bold**, __italic__)."""
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(?<![*\w])\*(?!\*)([^*\n]+?)\*(?![*\w])", r"__\1__", text)
    return text


def render_pdf(doc, out: Path) -> tuple[dict, int]:
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    fonts = next((f for f in FONT_CANDIDATES if all(Path(p).exists() for p in f)), None)
    meta = doc.meta

    class PDF(FPDF):
        def header(self):
            if self.page_no() > 1:
                self.set_font(FAM, "I", 7.5)
                self.set_text_color(110)
                self.cell(0, 5, f"{meta['document_id']} · {meta['title']} · v{meta['version']}", align="R")
                self.ln(7)

        def footer(self):
            self.set_y(-12)
            self.set_font(FAM, "I", 7)
            self.set_text_color(120)
            self.cell(0, 5, "Marvel CPG is a fictional company (CDA 650 capstone). Team 1 RAG knowledge base.", align="L")
            self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")

    pdf = PDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=16)
    if fonts:
        FAM = "kbfont"
        pdf.add_font(FAM, "", fonts[0]); pdf.add_font(FAM, "B", fonts[1]); pdf.add_font(FAM, "I", fonts[2])
        pdf.add_font(FAM, "BI", fonts[1])
    else:
        FAM = "Helvetica"
    pdf.set_title(f"{meta['document_id']} {meta['title']}")
    pdf.set_author(meta["owner"])
    pdf.set_creator("knowledge_base/tools/build_kb.py")
    pdf.add_page()
    pages = {}

    def para(text, size=9.5, style="", indent=0, color=30):
        pdf.set_font(FAM, style, size)
        pdf.set_text_color(color)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(pdf.epw - indent, size * 0.5, inline(text), markdown=True, new_x="LMARGIN", new_y="NEXT")

    def table(lines):
        rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines if not re.match(r"^\s*\|[\s:|-]+\|\s*$", l)]
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        lens = [max(min(len(r[j]), 60) for r in rows) + 4 for j in range(ncol)]
        size = 8 if ncol <= 4 else 7.2
        pdf.set_font(FAM, "", size)
        pdf.set_text_color(30)
        with pdf.table(col_widths=lens, text_align="LEFT", markdown=True, line_height=size * 0.5,
                       headings_style=FontFace(emphasis="BOLD", fill_color=(225, 232, 242)),
                       borders_layout="SINGLE_TOP_LINE", padding=1.2) as t:
            for r in rows:
                row = t.row()
                for c in r:
                    row.cell(inline(c))
        pdf.ln(2)

    # title block
    pdf.set_font(FAM, "B", 17); pdf.set_text_color(20, 45, 90)
    pdf.multi_cell(0, 8, meta["title"], new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    scope = ACCESS_LABEL.get(tuple(sorted(meta["permission_scope"])), ", ".join(meta["permission_scope"]))
    for k, v in [("Document", f"{meta['document_id']}, version {meta['version']}"), ("Effective date", meta["effective_date"]),
                 ("Access", scope), ("Status", meta["status"].replace("_", " ")), ("Source", meta["authorized_source"])]:
        pdf.set_font(FAM, "B", 8.5); pdf.set_text_color(60)
        pdf.cell(28, 4.6, k)
        pdf.set_font(FAM, "", 8.5)
        pdf.multi_cell(0, 4.6, v, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    for sec in split_sections(doc):
        if sec.level == 1:
            pass  # the title is already in the title block
        elif sec.level == 2:
            if pdf.get_y() > pdf.h - 45:
                pdf.add_page()
            pdf.ln(2.5)
            pages[sec.label] = pdf.page_no()
            pdf.set_font(FAM, "B", 13); pdf.set_text_color(20, 45, 90)
            pdf.multi_cell(0, 6.5, sec.label, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif sec.level == 3:
            if pdf.get_y() > pdf.h - 38:
                pdf.add_page()
            pdf.ln(1.5)
            pages[sec.label] = pdf.page_no()
            pdf.set_font(FAM, "B", 10.5); pdf.set_text_color(35, 65, 120)
            pdf.multi_cell(0, 5.5, sec.label, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.5)
        lines, i = sec.lines, 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if line.startswith("```"):
                j = i + 1
                while j < len(lines) and not lines[j].startswith("```"):
                    j += 1
                pdf.set_font("Courier", "", 8.5); pdf.set_text_color(30)
                pdf.set_fill_color(242, 244, 247)
                pdf.multi_cell(0, 4.4, "\n".join(lines[i + 1:j]), fill=True, new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
                i = j + 1
            elif line.lstrip().startswith("|"):
                j = i
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    j += 1
                table(lines[i:j])
                i = j
            elif line.startswith(">"):
                para(line.lstrip("> ").strip(), size=8.5, style="I", indent=3, color=90)
                pdf.ln(1.5)
                i += 1
            elif re.match(r"^\s*[-*]\s", line):
                para("•  " + re.sub(r"^\s*[-*]\s+", "", line), indent=4)
                pdf.ln(0.8)
                i += 1
            elif re.match(r"^\s*\d+\.\s", line):
                para(line.strip(), indent=4)
                pdf.ln(0.8)
                i += 1
            else:
                para(line.strip())
                pdf.ln(1.8)
                i += 1
    pdf.output(str(out))
    return pages, pdf.page_no()


# ---------------------------------------------------------------- build

def build(no_pdf: bool = False, no_embed: bool = False) -> None:
    docs = load_documents()
    PDF_DIR.mkdir(exist_ok=True)
    INDEX_DIR.mkdir(exist_ok=True)
    registry, chunks = [], []
    for doc in docs:
        pdf_name = doc.path.with_suffix(".pdf").name
        pages, n_pages = ({}, None)
        page_map_file = INDEX_DIR / "page_map.json"
        if no_pdf and page_map_file.exists():
            saved = json.loads(page_map_file.read_text()).get(doc.doc_id, {})
            pages, n_pages = saved.get("pages", {}), saved.get("n_pages")
        elif not no_pdf:
            pages, n_pages = render_pdf(doc, PDF_DIR / pdf_name)
        doc_chunks = chunk_document(doc, pages)
        chunks += doc_chunks
        registry.append(registry_entry(doc, pdf_name, n_pages, len(doc_chunks)))
        registry[-1]["_pages"] = pages
        print(f"{doc.doc_id:7s} {len(doc_chunks):3d} chunks  {n_pages or '-'} PDF pages  {doc.meta['title']}")

    page_map = {r["document_id"]: {"pages": r.pop("_pages"), "n_pages": r["pdf_pages"]} for r in registry}
    if not no_pdf:
        (INDEX_DIR / "page_map.json").write_text(json.dumps(page_map, indent=1) + "\n")
    corpus_hash = hashlib.sha256("".join(r["content_hash"] for r in registry).encode()).hexdigest()
    REGISTRY_PATH.write_text(json.dumps({
        "knowledge_base": "Marvel CPG RAG knowledge base (Module 4)",
        "contract": "data_platform/contracts/v3/module-data-contract.json -> module_4.knowledge_contract",
        "corpus_hash": corpus_hash, "document_count": len(registry), "documents": registry}, indent=1) + "\n")
    with open(INDEX_DIR / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    vec_file = f"embeddings_{EMBEDDING_MODEL.split('/')[-1]}.npy"
    if not no_embed:
        from fastembed import TextEmbedding
        model = TextEmbedding(EMBEDDING_MODEL)
        vecs = np.array(list(model.passage_embed([c["embedding_text"] for c in chunks])), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        np.save(INDEX_DIR / vec_file, vecs)
    manifest_path = INDEX_DIR / "manifest.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest = {
        "corpus_hash": corpus_hash, "chunk_count": len(chunks), "documents": len(registry),
        "chunking": {"method": "section-aware: split at Markdown headings (##, ###), pack paragraphs/lists/tables "
                               "up to a word limit, repeat table headers when a table is split",
                     "max_words": MAX_CHUNK_WORDS, "embedded_text": "document title | heading path + section text"},
        "dense": {"model": EMBEDDING_MODEL, "library": "fastembed (ONNX)", "dimension": 384, "normalized": True,
                  "file": vec_file, "row_order": "same as chunks.jsonl", "query_method": "TextEmbedding.query_embed"},
        "lexical": {"method": "BM25 (k1=1.4, b=0.75), built from chunks.jsonl at load time"},
        "retrieval": {"fusion": "reciprocal rank fusion (k=60) of dense and BM25 rankings",
                      "permission_filter": "chunks outside the caller's role are removed before ranking",
                      "min_dense_score": old.get("retrieval", {}).get("min_dense_score", 0.55),
                      "no_evidence_rule": "return no_relevant_evidence when the best permitted dense score is below min_dense_score"},
    }
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"\n{len(registry)} documents, {len(chunks)} chunks -> {REGISTRY_PATH.relative_to(KB_ROOT.parent)}, "
          f"{INDEX_DIR.relative_to(KB_ROOT.parent)}/  (corpus hash {corpus_hash[:12]})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-pdf", action="store_true", help="skip PDF rendering (reuses the saved page map)")
    ap.add_argument("--no-embed", action="store_true", help="skip dense embeddings")
    a = ap.parse_args()
    build(no_pdf=a.no_pdf, no_embed=a.no_embed)
