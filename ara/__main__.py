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
from .credentials import CredentialStore, load_api_key, load_anthropic_api_key
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
    parser.add_argument("--paper-type", choices=["review", "conceptual"], default=None,
                        help="Paper type: 'review' (systematic lit review) or 'conceptual' (theoretical framework).")
    parser.add_argument("--central-db-stats", action="store_true",
                        help="Show persistent central database statistics and exit.")
    parser.add_argument("--import-session-db", metavar="PATH",
                        help="Import papers from an existing session.db into the central database.")
    parser.add_argument("--no-peer-review", action="store_true",
                        help="Disable post-pipeline peer review.")
    return parser


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
