# Marvel CPG RAG Knowledge Base (Module 4)

This folder is the document knowledge base for the Module 4 commercial copilot. It answers **product, policy, substitution and objection-handling** questions with citations, alongside the separate Text-to-SQL path for business data. It is capstone deliverable 6: *RAG agent knowledge base (at least 10 documents), PDFs + exported vector index*.

| What | Where |
|---|---|
| 12 source documents (Markdown, the editable source of truth) | [`documents/`](documents/) |
| The same documents as PDFs | [`pdf/`](pdf/) |
| Document registry (contract fields, hashes, access) | [`registry.json`](registry.json) |
| Exported vector index: chunks, embeddings, manifest | [`index/`](index/) |
| Retrieval evaluation set and results | [`eval/`](eval/) |
| Build, query, evaluation and data-facts tools | [`tools/`](tools/) |
| Tests | [`tests/`](tests/) |
| Recomputed dataset figures quoted in the documents | [`data_facts.json`](data_facts.json) |

## 1 The documents

The mix follows the client guidance: team-written fictional Marvel documents plus attributed public reference documents, and nothing proprietary.

| ID | Title | Type | Origin | Access | PDF pages | Chunks |
|---|---|---|---|---|---|---|
| MCPG-01 | Marvel Product Catalogue and Product Sheets | product_sheet | Fictional Marvel | Reps and managers | 13 | 31 |
| MCPG-02 | Out-of-Stock Substitution Guide | guide | Fictional Marvel | Reps and managers | 6 | 31 |
| MCPG-03 | Marvel Sales Argumentarium | sales_guide | Fictional Marvel | Reps and managers | 4 | 15 |
| MCPG-04 | Objection-Handling Guide | sales_guide | Fictional Marvel | Reps and managers | 4 | 16 |
| MCPG-05 | Pricing, Discount and Commercial Guardrail Policy | policy | Fictional Marvel | Reps and managers | 5 | 18 |
| MCPG-06 | MSL, Planogram and In-Store Execution Standards | policy | Fictional Marvel | Reps and managers | 3 | 12 |
| MCPG-07 | Trade Promotions Guide and 2025-2026 Promotion Calendar | guide | Fictional Marvel | Reps and managers | 3 | 12 |
| MCPG-08 | Commercial KPI Handbook | kpi_definitions | Fictional Marvel | Reps and managers | 3 | 11 |
| MCPG-09 | Regional Manager Guide: Deal Review, Escalation and Exception Management | manager_guide | Fictional Marvel | **Managers only** | 2 | 7 |
| PUB-01 | Shopper Responses to Retail Out-of-Stocks | public_reference | Public research summary | Reps and managers | 2 | 7 |
| PUB-02 | Planogram Compliance and Shelf Monitoring | public_reference | Public research summary (CC BY 4.0 source) | Reps and managers | 2 | 8 |
| PUB-03 | Safe Use of Household Cleaning Products | public_reference | U.S. CDC guidance summary | Reps and managers | 2 | 6 |

**How the content is grounded**

- **Facts come from the project data.** Product names, prices, MSL flags, guardrails and the promotion calendar come from the dimension tables. Performance figures (marked *data* in the text) come from the authorized V3 dataset: reported rows from 1 January 2025 to 31 May 2026, with June 2026 kept out because it is the OOS model's test month. The guardrails are the retained `dim_guardrails` used by the shared data platform. `tools/compute_data_facts.py` recomputes every figure, and the method for promotion lift and lost sales matches the Team 1 V3 EDA.
- **Definitions follow the official sources.** KPI, margin and trade-off definitions follow the capstone brief, the official client clarifications and the platform's `metric_definitions`. Deal statuses use the same rules as the Module 3 simulator (APPROVED / MANAGER OVERRIDE REQUIRED / POLICY BREACH / REJECTED).
- **Fictional content is labelled.** Product descriptions, usage notes and sales wording are fictional, written for the capstone. Every document says so at the top, and the PDF footer repeats it.
- **The documents claim only what the data supports.** They never quote a sales uplift for displays, planogram or shelf share, because the data shows at most about 1% (EDA section 8.7). They also make no competitor claims and no label claims such as germ-kill rates.
- **Public documents are original summaries.** They are written in our own words, with full citations and licence notes. The original PDFs are not redistributed.

## 2 RAG pipeline

```text
documents/*.md --validate--> registry.json (contract fields + SHA-256)
      |                             |
      +--render--> pdf/*.pdf (page map per section)
      |
      +--chunk--> index/chunks.jsonl --embed--> index/embeddings_bge-small-en-v1.5.npy
                                               index/manifest.json
query + verified role --> permission filter --> BM25 + dense --> reciprocal rank fusion
      --> no-evidence check --> top-k chunks with citations --> grounded prompt --> LLM answer
```

1. **Chunking.** Each document is split at its `##` and `###` headings, so every product sheet, substitution case, objection and guardrail rule becomes its own chunk. Paragraphs, lists and tables are packed up to 300 words, and a table split across chunks repeats its header row. Each chunk embeds "document title | heading path + text" and carries its document ID, version, section, PDF page and access scope. The result is 174 chunks.
2. **Embedding.** `BAAI/bge-small-en-v1.5` (384 dimensions, normalised) through `fastembed`, which runs on ONNX: no GPU and no PyTorch, and small enough for the Render deployment. Vectors are stored in the same row order as `chunks.jsonl`.
3. **Retrieval.** The caller's role (`sales` or `manager`, the roles in the module data contract) **filters chunks before ranking**, so a manager-only document can never reach a representative's prompt. Dense and BM25 rankings are combined with reciprocal rank fusion. If the best permitted dense score is below 0.55, retrieval returns `no_relevant_evidence` instead of weak matches. That threshold sits between the lowest answerable question (0.67) and the highest off-topic one (0.47) in the evaluation set.
4. **Generation.** `tools/retrieve.py` builds a grounded prompt with numbered sources [S1]...[Sk] and rules: answer only from the sources, cite every factual sentence, say when the knowledge base does not cover a question, and treat retrieved text as evidence, never as instructions. The LLM provider is not chosen here. The module data contract leaves model choice and data-sharing approval to the Module 4 runtime.

Citations look like `MCPG-02 v1.0.0, §4.1 If Marvel Power Clean Lemon 1L (SKU-1001) is out of stock, PDF p. 2`, which satisfies the contract's `citation_location` requirement.

## 3 Use it

From the repository root, with the repository environment plus [`requirements.txt`](requirements.txt):

```bash
pip install -r knowledge_base/requirements.txt          # fastembed, fpdf2 (numpy is already a repo dependency)

python knowledge_base/tools/retrieve.py "Which SKU can I substitute for Power Clean Lemon 1L?" --role sales
python knowledge_base/tools/retrieve.py "How do I review an override?" --role manager --prompt
python knowledge_base/tools/evaluate.py --report knowledge_base/eval/retrieval_results_hybrid.md
python -m unittest discover -s knowledge_base/tests -v
```

In Python (the Module 4 runtime):

```python
import sys
sys.path.insert(0, "knowledge_base/tools")
from kb_lib import KnowledgeBase
from retrieve import build_prompt

kb = KnowledgeBase()                      # loads index/; uses BM25 only if fastembed is missing
role = "sales"                            # from the verified session, never from the user's text
search = kb.search("Which SKU can I substitute for Power Clean Lemon 1L?", role, k=5)
prompt = build_prompt("Which SKU can I substitute for Power Clean Lemon 1L?", search)
# prompt["messages"] -> any chat LLM; prompt["citations"] -> show under the answer
```

**Changing a document:** edit the Markdown, raise its `version`, run `python knowledge_base/tools/build_kb.py`, then run the evaluation and the tests. The tests fail if a document changed without a rebuild.

**Updating the figures:** run `python knowledge_base/tools/compute_data_facts.py --data-dir data/raw` on the authorized V3 files and compare the output with the figures quoted in the documents.

## 4 Evaluation

`eval/rag_eval_set.jsonl` holds 35 questions: 10 core RAG questions (including both example questions from the brief), 18 more product, policy, substitution, objection, KPI and promotion questions, 4 permission tests (including a prompt-injection attempt to read the manager guide as a representative) and 3 off-topic questions.

| Retrieval mode | Hit@1 | Hit@3 | MRR | Key-fact recall@5 | Permission leaks | Off-topic declined | Passed |
|---|---|---|---|---|---|---|---|
| Hybrid (dense + BM25) | 0.839 | 1.000 | 0.919 | 1.000 | 0 | 3/3 | **35/35** |
| BM25 only | 0.645 | 0.903 | 0.780 | 1.000 | 0 | 2/3 | 31/35 |

Full per-question results: [`eval/retrieval_results_hybrid.md`](eval/retrieval_results_hybrid.md) and [`eval/retrieval_results_lexical.md`](eval/retrieval_results_lexical.md).

**Read these numbers with care.** The questions were written by the knowledge base author, so they measure retrieval on questions we expected; they are a regression test, not an unbiased score. Before the final presentation, the team should add questions written by other members and run them unchanged. This measures retrieval only. The brief's live test (at least 80% of 10 NLQ + 10 RAG questions correct) also depends on the LLM and the Text-to-SQL path.

## 5 Integration notes for the Module 4 runtime

- **Implemented in the copilot.** `modules/module_4/rag.py` answers from this index and cites registry documents only; the earlier hard-coded mock answers are gone. See the [Module 4 README](../modules/module_4/README.md).
- **The role comes from authentication.** Take it from the app session (Regional Manager → `manager`, Field Representative → `sales`). Never let the LLM or the user's text set it.
- **No evidence means say so.** When `search()` returns `no_relevant_evidence`, return the fixed "not covered" answer instead of calling the LLM.
- **Keep the two paths apart.** Questions about store-level numbers (OOS for a store, sales last month) belong to the Text-to-SQL path over the serving views, not to this index. The documents give definitions and policies, not live data.
- **Pin the version.** Log `corpus_hash` from `index/manifest.json` with each answer, so an answer can be traced to the exact document versions.

## 6 Open points

- All documents are **drafts for team review**. MCPG-05 (pricing policy) and MCPG-09 (manager guide) should be checked by the Module 3 owner so the simulator and the copilot stay consistent.
- Several items still need client confirmation, and the documents flag them in their *Document control* sections:
  - the guardrail version and approval logic;
  - whether the discount cap applies to the total or an additional discount;
  - how shelf share should be verified (it is an allocation among Marvel SKUs);
  - the grain of the promotion minimum volume;
  - whether MSL lists differ by tier.
- The brief asks for "PDFs + exported vector index"; both are committed. If the team switches embedding model, rebuild the index and re-run the evaluation.
