# Embed claims via OpenRouter — takes slice N of 10
from __future__ import annotations
import json, logging, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openai import OpenAI
from ara.central_db import CentralDB
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def main():
    slice_n = int(os.environ.get("SLICE", "0"))
    total_slices = 10
    _log = logging.getLogger(f"or_embed_{slice_n}")
    
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key: sys.exit(1)
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    db = CentralDB()
    
    rows = db._conn.execute("SELECT claim_id, claim_text FROM claims WHERE embedding IS NULL ORDER BY claim_id DESC").fetchall()
    # Take our slice (from the other end)
    chunk = [dict(r) for i, r in enumerate(rows) if i % total_slices == slice_n]
    _log.info("Slice %d/%d: %d claims to embed", slice_n, total_slices, len(chunk))
    
    embedded = 0; t0 = time.time()
    for j in range(0, len(chunk), 50):
        batch = chunk[j:j+50]
        texts = [r["claim_text"][:500] for r in batch]
        try:
            result = client.embeddings.create(model="google/gemini-embedding-001", input=texts)
            if result.data:
                for row, emb in zip(batch, result.data):
                    db.store_claim_embedding(row["claim_id"], emb.embedding)
                    embedded += 1
        except Exception as exc:
            if "429" in str(exc) or "rate" in str(exc).lower():
                _log.warning("Rate limited — 15s"); time.sleep(15)
            else:
                _log.warning("Batch failed: %s", exc); time.sleep(2)
        time.sleep(0.5)
        if (j+50) % 500 == 0 and j > 0:
            _log.info("  %d/%d embedded (%.0f/min)", embedded, len(chunk), embedded/((time.time()-t0)/60))
    
    _log.info("DONE: embedded %d claims in %.1f min", embedded, (time.time()-t0)/60)

if __name__ == "__main__": main()
