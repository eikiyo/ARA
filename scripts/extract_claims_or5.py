from __future__ import annotations
import json, logging, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openai import OpenAI
from ara.central_db import CentralDB
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_log = logging.getLogger("or5")
_PROMPT = """Extract ALL findings, theories, methods, limitations, and gaps from this paper.
Return a JSON array of claim objects. Each claim must have:
- claim_text, claim_type, confidence (float 0-1), section, study_design, sample_size, country.
Extract EVERYTHING. Return ONLY a JSON array, no markdown fences.
Title: {title}
Text: {text}"""
def _pc(r):
    if isinstance(r,(int,float)):return float(r)
    if isinstance(r,str):
        m={"high":0.9,"very high":0.95,"medium":0.6,"moderate":0.6,"low":0.3}
        if r.lower() in m:return m[r.lower()]
        try:return float(r)
        except:pass
    return 0.5
def _parse(t):
    t=t.strip()
    if t.startswith("```"):ls=t.split("\n");t="\n".join(ls[1:-1] if ls[-1].strip()=="```" else ls[1:])
    for a in[t,None]:
        if a is None:
            s,e=t.find("["),t.rfind("]")
            if s<0 or e<=s:s,e=t.find("{"),t.rfind("}");
            if s<0 or e<=s:return[]
            a=t[s:e+1]
        try:
            o=json.loads(a)
            if isinstance(o,list):return o
            if isinstance(o,dict):
                for v in o.values():
                    if isinstance(v,list):return v
                if o.get("claim_text"):return[o]
            return[]
        except:continue
    return[]
def main():
    ak=os.getenv("OPENROUTER_API_KEY","")
    if not ak:sys.exit(1)
    cl=OpenAI(base_url="https://openrouter.ai/api/v1",api_key=ak)
    db=CentralDB()
    rows=db._conn.execute("SELECT p.paper_id,p.title,p.abstract,p.doi,p.full_text FROM papers p WHERE p.full_text IS NOT NULL AND p.full_text!='' ORDER BY p.paper_id DESC").fetchall()
    # Take first third (high paper_id)
    chunk=rows[:len(rows)//3]
    papers=[dict(r) for r in chunk if not db.is_paper_fully_extracted(r["title"] or "")]
    if not papers:_log.info("Done");return
    _log.info("OR-5: %d papers",len(papers))
    tc=0;done=0;fail=0;t0=time.time()
    for i,p in enumerate(papers):
        if db.is_paper_fully_extracted(p["title"] or ""):continue
        ft=p["full_text"] or "";ab=p["abstract"] or ""
        txt=ft[:4000] if len(ft)>200 else ab
        if not txt or len(txt)<100:continue
        try:
            r=cl.chat.completions.create(messages=[{"role":"user","content":_PROMPT.format(title=p["title"],text=txt)}],model="google/gemini-2.5-flash-lite",max_tokens=4096,temperature=0.1)
            cs=_parse(r.choices[0].message.content or "")
            if not cs:fail+=1
            else:
                cc=[{"paper_title":p["title"],"paper_doi":p.get("doi",""),"claim_text":c["claim_text"],"claim_type":c.get("claim_type","finding"),"confidence":_pc(c.get("confidence",0.5)),"supporting_quotes":json.dumps(c.get("supporting_quotes",[])),"section":c.get("section",""),"sample_size":str(c.get("sample_size","")),"effect_size":str(c.get("effect_size","")),"p_value":str(c.get("p_value","")),"confidence_interval":str(c.get("confidence_interval","")),"study_design":str(c.get("study_design","")),"population":str(c.get("population","")),"country":str(c.get("country","")),"year_range":str(c.get("year_range",""))} for c in cs if isinstance(c,dict) and c.get("claim_text")]
                if cc:
                    res=db.store_claims(cc,session_topic="prewarm_or5");tc+=res.get("stored",0)
                    db.mark_paper_fully_extracted(p["title"]);done+=1
        except Exception as e:
            fail+=1
            if "429" in str(e) or "rate" in str(e).lower():time.sleep(30)
            else:_log.warning("[%d] %s",i+1,e)
        time.sleep(1)
        if(i+1)%25==0:
            el=time.time()-t0;rt=done/el*60 if el>0 else 0
            _log.info("[%d/%d] %d claims %d done (%.0f/min %d fail)",i+1,len(papers),tc,done,rt,fail)
    _log.info("OR-5 DONE: %d claims %d papers %.1f min",tc,done,(time.time()-t0)/60)
if __name__=="__main__":main()
