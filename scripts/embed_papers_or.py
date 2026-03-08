from __future__ import annotations
import logging, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openai import OpenAI
from ara.central_db import CentralDB
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def main():
    slice_n = int(os.environ.get("SLICE", "0"))
    total_slices = 10
    _log = logging.getLogger(f"or_pembed_{slice_n}")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key: sys.exit(1)
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    db = CentralDB()
    rows = db._conn.execute("SELECT paper_id, title, abstract FROM papers WHERE embedding IS NULL ORDER BY paper_id DESC").fetchall()
    chunk = [dict(r) for i, r in enumerate(rows) if i % total_slices == slice_n]
    _log.info("Slice %d/%d: %d papers to embed", slice_n, total_slices, len(chunk))
    embedded = 0; t0 = time.time()
    for j in range(0, len(chunk), 50):
        batch = chunk[j:j+50]
        texts = [((r["title"] or "") + " " + (r["abstract"] or ""))[:500] for r in batch]
        texts = [t for t in texts if len(t.strip()) > 10]
        if not texts: continue
        try:
            result = client.embeddings.create(model="google/gemini-embedding-001", input=texts)
            if result.data:
                for row, emb in zip(batch, result.data):
                    db.store_embedding(row["paper_id"], emb.embedding)
                    embedded += 1
        except Exception as exc:
            if "429" in str(exc) or "rate" in str(exc).lower():
                time.sleep(15)
            else:
                _log.warning("Batch failed: %s", exc); time.sleep(2)
        time.sleep(0.5)
        if (j+50) % 500 == 0 and j > 0:
            _log.info("  %d/%d embedded", embedded, len(chunk))
    _log.info("DONE: %d papers in %.1f min", embedded, (time.time()-t0)/60)
if __name__ == "__main__": main()
