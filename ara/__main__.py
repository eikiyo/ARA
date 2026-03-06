# Location: ara/__main__.py
# Purpose: CLI entry point — `python -m ara` or `ara`
# Functions: main, build_parser
# Calls: builder.py, config.py, credentials.py, runtime.py, tui.py, settings.py
# Imports: argparse, sys

from __future__ import annotations

import argparse
import sys

from .builder import build_engine
from .config import ARAConfig
from .credentials import CredentialStore, load_api_key, load_anthropic_api_key, load_openai_api_key
from .logging import setup_logging
from .runtime import SessionError, SessionRuntime
from .settings import SettingsStore
from .tui import ChatContext, dispatch_slash_command, run_rich_repl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ara",
        description="ARA — Autonomous Research Agent for academic research.",
    )
    parser.add_argument("--workspace", default=".", help="Workspace root directory.")
    parser.add_argument("--model", help="Gemini model name (default: gemini-2.0-flash).")
    parser.add_argument("--configure-keys", action="store_true", help="Set/update Google API key.")
    parser.add_argument("--max-depth", type=int, help="Maximum recursion depth.")
    parser.add_argument("--max-steps", type=int, help="Maximum steps per recursive call.")
    parser.add_argument("--task", help="Single objective to run and exit.")
    parser.add_argument("--resume", action="store_true", help="Resume existing session.")
    parser.add_argument("--no-tui", action="store_true", help="Plain text mode.")
    parser.add_argument("--no-gates", action="store_true", help="Auto-approve all phase gates.")
    parser.add_argument("--paper-type", choices=["review", "scoping", "conceptual"], default=None,
                        help="Paper type: 'review' (SLR), 'scoping' (scoping review), or 'conceptual' (theoretical framework).")
    parser.add_argument("--central-db-stats", action="store_true",
                        help="Show persistent central database statistics and exit.")
    parser.add_argument("--import-session-db", metavar="PATH",
                        help="Import papers from an existing session.db into the central database.")
    parser.add_argument("--no-peer-review", action="store_true",
                        help="Disable post-pipeline peer review.")
    parser.add_argument("--import-papers", metavar="DIR",
                        help="Import papers from a directory (PDFs/text files). Skips scout/snowball/brancher.")
    parser.add_argument("--special-instructions", type=str, default=None,
                        help="Topic-specific instructions passed to all phases (e.g., 'Focus on fintech applications').")
    parser.add_argument("--special-authors", type=str, default=None,
                        help="Comma-separated foundational authors to search for (e.g., 'Kappen,Govindarajan').")
    return parser


def _import_paper_corpus(runtime: SessionRuntime, corpus_dir) -> int:
    """Import papers from a directory into the session DB and checkpoint search phases."""
    import re
    from pathlib import Path

    # Ensure session exists
    if not runtime.db_session_id:
        runtime.start_research(topic="Corpus import")

    db = runtime.db
    sid = runtime.db_session_id
    papers: list[dict] = []

    for f in sorted(corpus_dir.iterdir()):
        if f.is_dir():
            continue
        suffix = f.suffix.lower()

        if suffix == ".pdf":
            # Extract text from PDF filename as title, store path for later processing
            title = f.stem.replace("_", " ").replace("-", " ")
            # Try to extract year from filename
            year_match = re.search(r"(19|20)\d{2}", f.name)
            year = int(year_match.group()) if year_match else None
            papers.append({
                "title": title,
                "source": "corpus_import",
                "year": year,
                "full_text_path": str(f),
            })

        elif suffix in (".txt", ".md"):
            content = f.read_text("utf-8", errors="replace")
            title = content.split("\n", 1)[0].strip()[:200] or f.stem
            year_match = re.search(r"(19|20)\d{2}", f.name)
            year = int(year_match.group()) if year_match else None
            papers.append({
                "title": title,
                "source": "corpus_import",
                "year": year,
                "full_text": content,
            })

        elif suffix in (".bib", ".json"):
            # Try to parse structured bibliographic data
            if suffix == ".json":
                import json as _json
                try:
                    data = _json.loads(f.read_text("utf-8"))
                    if isinstance(data, list):
                        for entry in data:
                            if isinstance(entry, dict) and entry.get("title"):
                                papers.append({
                                    "title": entry["title"],
                                    "abstract": entry.get("abstract", ""),
                                    "authors": entry.get("authors", []),
                                    "year": entry.get("year"),
                                    "doi": entry.get("doi"),
                                    "source": "corpus_import",
                                })
                except Exception:
                    pass

    if not papers:
        return 0

    stored = db.store_papers(sid, papers)

    # Mark all imported papers as selected for deep read
    db._conn.execute(
        "UPDATE papers SET selected_for_deep_read = 1 WHERE session_id = ? AND source = 'corpus_import'",
        (sid,),
    )
    db._conn.commit()

    # Store full_text for papers that had it
    for p in papers:
        if p.get("full_text"):
            row = db._conn.execute(
                "SELECT paper_id FROM papers WHERE session_id = ? AND title = ?",
                (sid, p["title"]),
            ).fetchone()
            if row:
                db._conn.execute(
                    "UPDATE papers SET full_text = ? WHERE paper_id = ?",
                    (p["full_text"], row["paper_id"]),
                )
    db._conn.commit()

    # Checkpoint search phases so pipeline skips them
    for phase in ("scout", "snowball", "verifier", "protocol", "triage"):
        db.save_phase_checkpoint(sid, phase)

    return stored


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    non_tty = not (sys.stdin.isatty() and sys.stdout.isatty())
    if non_tty:
        args.no_tui = True

    cfg = ARAConfig.from_env(args.workspace)
    settings_store = SettingsStore(workspace=cfg.workspace, session_root_dir=cfg.session_root_dir)
    settings = settings_store.load()

    # Handle --central-db-stats
    if args.central_db_stats:
        from .central_db import CentralDB
        cdb = CentralDB()
        import json as _json
        stats = cdb.stats()
        print(_json.dumps(stats, indent=2))
        cdb.close()
        return

    # Handle --import-session-db
    if args.import_session_db:
        from pathlib import Path
        from .central_db import CentralDB
        cdb = CentralDB()
        result = cdb.import_from_session_db(Path(args.import_session_db))
        import json as _json
        print(_json.dumps(result, indent=2))
        print(f"\nCentral DB now has {cdb.paper_count()} total papers.")
        cdb.close()
        return

    # Handle --configure-keys
    if args.configure_keys:
        store = CredentialStore()
        store.prompt_and_save()
        print("API key saved.")
        return

    # Load API key from all sources
    api_key = load_api_key(workspace=cfg.workspace)
    if not api_key:
        print("No Google API key found. Run `ara --configure-keys` or set GOOGLE_API_KEY env var.")
        if not args.task:
            return
    cfg.google_api_key = api_key

    # Load Anthropic API key for peer review
    anthropic_key = load_anthropic_api_key(workspace=cfg.workspace)
    if anthropic_key:
        cfg.anthropic_api_key = anthropic_key

    # Load OpenAI API key for hypothesis/critic load balancing
    openai_key = load_openai_api_key(workspace=cfg.workspace)
    if openai_key:
        cfg.openai_api_key = openai_key

    # Apply CLI overrides
    if args.max_depth is not None:
        cfg.max_depth = args.max_depth
    if args.max_steps is not None:
        cfg.max_steps_per_call = args.max_steps
    if args.no_gates:
        cfg.approval_gates = False
    if args.paper_type:
        cfg.paper_type = args.paper_type
    if getattr(args, 'no_peer_review', False):
        cfg.peer_review_enabled = False
    if args.special_instructions:
        cfg.special_instructions = args.special_instructions
    if args.special_authors:
        cfg.special_authors = args.special_authors
    if args.model:
        cfg.model = args.model
    elif settings.default_model:
        cfg.model = settings.default_model

    # Initialize
    setup_logging(cfg.workspace, cfg.session_root_dir)
    engine = build_engine(cfg)
    model_name = getattr(engine.model, "model", "unknown")

    try:
        runtime = SessionRuntime.bootstrap(engine=engine, config=cfg, resume=args.resume)
    except SessionError as exc:
        print(f"Session error: {exc}")
        return

    startup_info = {
        "Model": model_name,
        "Workspace": str(cfg.workspace),
        "Session": runtime.session_id,
    }

    # Persist model choice
    settings.default_model = cfg.model
    settings_store.save(settings)

    # Handle --import-papers: ingest corpus and pre-checkpoint search phases
    if getattr(args, 'import_papers', None):
        from pathlib import Path as _Path
        corpus_dir = _Path(args.import_papers).expanduser().resolve()
        if not corpus_dir.is_dir():
            print(f"Error: {corpus_dir} is not a directory.")
            return
        count = _import_paper_corpus(runtime, corpus_dir)
        print(f"Imported {count} papers from {corpus_dir}")
        print("Scout/Snowball/Brancher phases will be skipped.")

    ctx = ChatContext(runtime=runtime, cfg=cfg, settings_store=settings_store)

    if args.task:
        for key, val in startup_info.items():
            print(f"{key:>10}  {val}")
        print()
        result = runtime.solve(args.task, on_event=lambda ev: print(f"trace> {ev.event_type}: {ev.data[:120]}"))
        print(result)
        return

    if args.no_tui:
        if not sys.stdin.isatty():
            print("No interactive stdin. Use --task for headless mode.")
            raise SystemExit(2)
        print("ARA — Plain mode. Type /quit to exit.")
        while True:
            try:
                objective = input("you> ").strip()
            except EOFError:
                break
            if not objective:
                continue
            r = dispatch_slash_command(objective, ctx, emit=lambda line: print(f"ara> {line}"))
            if r == "quit":
                break
            if r in ("clear", "handled"):
                continue
            response = runtime.solve(objective, on_event=lambda ev: print(f"trace> {ev.event_type}: {ev.data[:120]}"))
            print(f"ara> {response}")
        return

    try:
        run_rich_repl(ctx, startup_info=startup_info)
    except ImportError:
        print("Rich/prompt_toolkit not installed. Use --no-tui or install dependencies.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
