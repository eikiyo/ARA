# Embed claims via direct Google API — takes slice N of 8
from __future__ import annotations
import json, logging, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ara.central_db import CentralDB
from ara.credentials import load_api_key
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def main():
    slice_n = int(os.environ.get("SLICE", "0"))
    total_slices = 8
    _log = logging.getLogger(f"gem_embed_{slice_n}")
    
    gkey = load_api_key()
    if not gkey: sys.exit(1)
    from google import genai
    client = genai.Client(api_key=gkey)
    db = CentralDB()
    
    rows = db._conn.execute("SELECT claim_id, claim_text FROM claims WHERE embedding IS NULL ORDER BY claim_id").fetchall()
    # Take our slice
    chunk = [dict(r) for i, r in enumerate(rows) if i % total_slices == slice_n]
    _log.info("Slice %d/%d: %d claims to embed", slice_n, total_slices, len(chunk))
    
    embedded = 0; t0 = time.time()
    for j in range(0, len(chunk), 50):
        batch = chunk[j:j+50]
        # Skip already embedded (another agent may have done it)
        texts = [r["claim_text"][:500] for r in batch]
        try:
            result = client.models.embed_content(model="gemini-embedding-001", contents=texts)
            if result.embeddings:
                for row, emb_obj in zip(batch, result.embeddings):
                    db.store_claim_embedding(row["claim_id"], emb_obj.values)
                    embedded += 1
        except Exception as exc:
            if "429" in str(exc) or "rate" in str(exc).lower() or "quota" in str(exc).lower():
                _log.warning("Rate limited — 15s"); time.sleep(15)
            else:
                _log.warning("Batch failed: %s", exc); time.sleep(2)
        if (j+50) % 500 == 0 and j > 0:
            _log.info("  %d/%d embedded (%.0f/min)", embedded, len(chunk), embedded/((time.time()-t0)/60))
    
    _log.info("DONE: embedded %d claims in %.1f min", embedded, (time.time()-t0)/60)

if __name__ == "__main__": main()
