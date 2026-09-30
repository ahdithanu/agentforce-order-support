"""Offline retrieval lab: how chunking, search type, and k change recall on the Acme policy corpus.

Mirrors the platform index where it can (section-aware chunks, <=512 tokens, hybrid search) and
varies what the managed Data Library does not expose. Scores every configuration on
tests/retrieval-gold.yaml with recall@k and MRR, split by direct vs paraphrased questions.

Usage: python3 scripts/retrieval_lab.py [--embeddings]   # --embeddings needs `fastembed` installed
Writes tests/results/retrieval-lab.json.
"""
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import yaml
from bs4 import BeautifulSoup
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

ROOT = Path(__file__).resolve().parent.parent
KS = (1, 3, 5, 10)
STOP = set("a an the and or of to in on for with is are be your you we our it this that at by as if from can do does my i me what how when will".split())


# ---------- corpus ----------
def load_sections():
    """Split each policy doc into (doc, heading, text) sections at <h2> boundaries."""
    sections = []
    for path in sorted((ROOT / "knowledge").glob("*.html")):
        soup = BeautifulSoup(path.read_text(), "html.parser")
        doc = path.stem
        title = soup.find("h1").get_text(strip=True)
        current, buf = "Overview", []
        for el in soup.body.find_all(["h2", "p"], recursive=False):
            if el.name == "h2":
                if buf:
                    sections.append((doc, title, current, " ".join(buf)))
                current, buf = el.get_text(strip=True), []
            else:
                buf.append(el.get_text(" ", strip=True))
        if buf:
            sections.append((doc, title, current, " ".join(buf)))
    return sections


def chunk(sections, strategy):
    """Return chunks as dicts: text + the set of section ids ('doc#heading') the chunk covers."""
    out = []
    if strategy.startswith("section"):
        header = strategy == "section+header"
        for doc, title, head, text in sections:
            body = f"{title}. {head}. {text}" if header else f"{head}. {text}"
            out.append({"text": body, "labels": {f"{doc}#{head}"}})
        return out
    # fixed-size word windows over each document, e.g. "fixed48" or "fixed48/o16"
    m = re.match(r"fixed(\d+)(?:/o(\d+))?", strategy)
    size, overlap = int(m.group(1)), int(m.group(2) or 0)
    by_doc = {}
    for doc, title, head, text in sections:
        words = f"{head}. {text}".split()
        by_doc.setdefault(doc, []).extend((w, f"{doc}#{head}") for w in words)
    for doc, labelled in by_doc.items():
        step = max(1, size - overlap)
        for start in range(0, len(labelled), step):
            window = labelled[start:start + size]
            if not window:
                break
            counts = Counter(lbl for _, lbl in window)
            out.append({"text": " ".join(w for w, _ in window),
                        "labels": {lbl for lbl, n in counts.items() if n >= 8 or n == len(window)}})
            if start + size >= len(labelled):
                break
    return out


# ---------- retrievers ----------
def tokens(text):
    words = re.findall(r"[a-z0-9.]+", text.lower())
    return [w.rstrip("s") if len(w) > 3 else w for w in words if w not in STOP]


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.docs = [tokens(d) for d in docs]
        self.avg = sum(map(len, self.docs)) / len(self.docs)
        self.df = Counter(t for d in self.docs for t in set(d))
        self.n, self.k1, self.b = len(self.docs), k1, b

    def scores(self, query):
        q = tokens(query)
        out = np.zeros(self.n)
        for i, d in enumerate(self.docs):
            tf = Counter(d)
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                out[i] += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
        return out


class Dense:
    """Cosine similarity over TF-IDF, optionally projected with LSA (a classic 'semantic' proxy)."""

    def __init__(self, docs, lsa=False):
        self.vec = TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2), stop_words="english")
        m = self.vec.fit_transform(docs)
        self.svd = None
        if lsa:
            self.svd = TruncatedSVD(n_components=min(24, m.shape[0] - 1), random_state=0)
            m = self.svd.fit_transform(m)
        else:
            m = m.toarray()
        self.m = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)

    def scores(self, query):
        q = self.vec.transform([query])
        q = self.svd.transform(q) if self.svd is not None else q.toarray()
        q = q / (np.linalg.norm(q) + 1e-9)
        return (self.m @ q.T).ravel()


class Embed:
    """Neural embeddings via fastembed (optional)."""

    def __init__(self, docs, model="BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model)
        m = np.array(list(self.model.embed(docs)))
        self.m = m / np.linalg.norm(m, axis=1, keepdims=True)

    def scores(self, query):
        q = np.array(list(self.model.query_embed(query)))[0]
        return self.m @ (q / np.linalg.norm(q))


def rrf(*score_lists, k=60):
    """Reciprocal rank fusion: how hybrid search merges keyword and vector rankings."""
    fused = np.zeros(len(score_lists[0]))
    for s in score_lists:
        for rank, idx in enumerate(np.argsort(-s)):
            fused[idx] += 1 / (k + rank + 1)
    return fused


# ---------- evaluation ----------
def evaluate(chunks, retriever_scores, gold):
    rows = []
    for item in gold:
        scores = retriever_scores(item["q"])
        order = list(np.argsort(-scores))
        rank = next((r + 1 for r, i in enumerate(order) if item["gold"] in chunks[i]["labels"]), None)
        rows.append({"style": item["style"], "rank": rank})
    def summarize(sub):
        n = len(sub)
        return {**{f"recall@{k}": round(sum(1 for r in sub if r["rank"] and r["rank"] <= k) / n, 3) for k in KS},
                "mrr": round(sum(1 / r["rank"] for r in sub if r["rank"] and r["rank"] <= 10) / n, 3)}
    return {"all": summarize(rows),
            "direct": summarize([r for r in rows if r["style"] == "direct"]),
            "paraphrase": summarize([r for r in rows if r["style"] == "paraphrase"])}


def main():
    gold = yaml.safe_load((ROOT / "tests/retrieval-gold.yaml").read_text())["questions"]
    sections = load_sections()
    use_embed = "--embeddings" in sys.argv
    results = []
    for strategy in ["section", "section+header", "fixed48", "fixed48/o16", "fixed24"]:
        chunks = chunk(sections, strategy)
        texts = [c["text"] for c in chunks]
        bm25, tfidf, lsa = BM25(texts), Dense(texts), Dense(texts, lsa=True)
        avg_words = sum(len(t.split()) for t in texts) / len(texts)
        retrievers = {
            "bm25 (keyword)": bm25.scores,
            "tfidf (vector)": tfidf.scores,
            "lsa (semantic proxy)": lsa.scores,
            "hybrid rrf(bm25+tfidf)": lambda q: rrf(bm25.scores(q), tfidf.scores(q)),
        }
        if use_embed:
            emb = Embed(texts)
            retrievers["embeddings (bge-small)"] = emb.scores
            retrievers["hybrid rrf(bm25+embeddings)"] = lambda q, e=emb: rrf(bm25.scores(q), e.scores(q))
        for name, fn in retrievers.items():
            results.append({"chunking": strategy, "retriever": name, "chunks": len(chunks),
                            "avg_chunk_words": round(avg_words, 1),
                            "context_words_at": {k: round(min(k, len(chunks)) * avg_words) for k in KS},
                            **evaluate(chunks, fn, gold)})
    out = ROOT / "tests/results/retrieval-lab.json"
    out.write_text(json.dumps(results, indent=1))
    header = f"{'chunking':<15}{'retriever':<30}{'n':>4}{'R@1':>6}{'R@3':>6}{'R@5':>6}{'MRR':>6}   paraphrase R@1/R@3"
    print(header)
    print("-" * len(header))
    for r in results:
        a, p = r["all"], r["paraphrase"]
        print(f"{r['chunking']:<15}{r['retriever']:<30}{r['chunks']:>4}{a['recall@1']:>6}{a['recall@3']:>6}{a['recall@5']:>6}{a['mrr']:>6}   {p['recall@1']}/{p['recall@3']}")


if __name__ == "__main__":
    main()
