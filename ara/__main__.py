# Location: ara/__main__.py
# Purpose: CLI entry point — `python -m ara` or `ara`
# Functions: main, build_parser
# Calls: builder.py, config.py, credentials.py, runtime.py, tui.py, settings.py
# Imports: argparse, os, sys

from __future__ import annotations

import argparse
import os
import sys

from .builder import build_engine, infer_provider_for_model
from .config import ARAConfig
from .credentials import (
    CredentialBundle, CredentialStore, credentials_from_env,
    parse_env_file, prompt_for_credentials,
)
from .logging import setup_logging
from .runtime import SessionError, SessionRuntime
from .settings import SettingsStore, PersistentSettings
from .tui import ChatContext, _get_model_display_name, dispatch_slash_command, run_rich_repl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ara",
        description="ARA — Autonomous Research Agent for academic research.",
    )
    parser.add_argument("--workspace", default=".", help="Workspace root directory.")
    parser.add_argument(
        "--provider", default=None,
        choices=["auto", "google", "openai", "anthropic", "openrouter", "ollama"],
        help="Model provider.",
    )
    parser.add_argument("--model", help="Model name.")
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high", "none"],
        help="Reasoning effort override.",
    )
    parser.add_argument("--configure-keys", action="store_true", help="Set/update API keys.")
    parser.add_argument("--max-depth", type=int, help="Maximum recursion depth.")
    parser.add_argument("--max-steps", type=int, help="Maximum steps per recursive call.")
    parser.add_argument("--task", help="Single objective to run and exit.")
    parser.add_argument("--resume", action="store_true", help="Resume existing session.")
    parser.add_argument("--no-tui", action="store_true", help="Plain text mode.")
    parser.add_argument("--no-gates", action="store_true", help="Auto-approve all phase gates.")
    return parser


def _resolve_provider(requested: str | None, creds: CredentialBundle) -> str:
    if requested and requested not in ("auto", "", None):
        return requested.strip().lower()
    if creds.google_api_key:
        return "google"
    if creds.anthropic_api_key:
        return "anthropic"
    if creds.openai_api_key:
        return "openai"
    if creds.openrouter_api_key:
        return "openrouter"
    return "ollama"


def _load_credentials(cfg: ARAConfig, args: argparse.Namespace) -> CredentialBundle:
    store = CredentialStore()
    creds = store.load()
    creds.merge_missing(credentials_from_env())
    env_path = cfg.workspace / ".env"
    if env_path.exists():
        creds.merge_missing(parse_env_file(env_path))

    if args.configure_keys:
        updated, changed = prompt_for_credentials(creds, force=True)
        creds = updated
        if changed:
            store.save(creds)

    if not creds.has_any() and not (args.provider and args.provider.lower() == "ollama"):
        print("No API keys configured. Run `ara --configure-keys` or set env vars.")

    return creds


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    non_tty = not (sys.stdin.isatty() and sys.stdout.isatty())
    if non_tty:
        args.no_tui = True

    cfg = ARAConfig.from_env(args.workspace)
    settings_store = SettingsStore(workspace=cfg.workspace, session_root_dir=cfg.session_root_dir)
    settings = settings_store.load()

    # Restore persisted settings
    if args.provider is None and settings.default_provider:
        cfg.provider = settings.default_provider
    if args.reasoning_effort is None and settings.default_reasoning_effort:
        cfg.reasoning_effort = settings.default_reasoning_effort

    if args.configure_keys and not args.task:
        _load_credentials(cfg, args)
        print("Credential configuration complete.")
        return

    creds = _load_credentials(cfg, args)

    # Apply CLI overrides
    if args.max_depth is not None:
        cfg.max_depth = args.max_depth
    if args.max_steps is not None:
        cfg.max_steps_per_call = args.max_steps
    if args.no_gates:
        cfg.approval_gates = False
    if args.provider:
        cfg.provider = args.provider

    cfg.provider = _resolve_provider(cfg.provider, creds)
    cfg.google_api_key = creds.google_api_key
    cfg.anthropic_api_key = creds.anthropic_api_key
    cfg.openai_api_key = creds.openai_api_key
    cfg.openrouter_api_key = creds.openrouter_api_key

    if args.model:
        cfg.model = args.model
    elif not os.getenv("ARA_MODEL"):
        persisted = settings.default_model_for_provider(cfg.provider)
        if persisted:
            cfg.model = persisted

    if args.reasoning_effort:
        cfg.reasoning_effort = None if args.reasoning_effort == "none" else args.reasoning_effort

    # Auto-switch provider if model implies different one
    model_check = (cfg.model or "").strip()
    if model_check:
        inferred = infer_provider_for_model(model_check)
        if inferred and inferred != cfg.provider:
            key_map = {
                "google": cfg.google_api_key,
                "anthropic": cfg.anthropic_api_key,
                "openai": cfg.openai_api_key,
                "openrouter": cfg.openrouter_api_key,
                "ollama": "ollama",
            }
            if key_map.get(inferred):
                cfg.provider = inferred

    # Google: no reasoning effort
    if cfg.provider == "google" and not args.reasoning_effort:
        cfg.reasoning_effort = None

    # Initialize
    setup_logging(cfg.workspace, cfg.session_root_dir)
    engine = build_engine(cfg)
    model_name = _get_model_display_name(engine)

    try:
        runtime = SessionRuntime.bootstrap(engine=engine, config=cfg, resume=args.resume)
    except SessionError as exc:
        print(f"Session error: {exc}")
        return

    startup_info = {
        "Provider": cfg.provider,
        "Model": model_name,
        "Workspace": str(cfg.workspace),
        "Session": runtime.session_id,
    }
    if cfg.reasoning_effort:
        startup_info["Reasoning"] = cfg.reasoning_effort

    # Persist settings
    settings.default_provider = cfg.provider
    prov = cfg.provider
    attr = f"default_model_{prov}" if prov in ("google", "openai", "anthropic", "openrouter", "ollama") else "default_model"
    setattr(settings, attr, cfg.model)
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
