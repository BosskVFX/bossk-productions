"""Run the BOSSK core:  python -m core --config /path/bossk.yml"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import CoreConfig  # noqa: E402
from core.secrets import Secrets  # noqa: E402
from core.server import Core, serve  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get("BOSSK_CONFIG", "bossk.yml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = CoreConfig.load(args.config)
    secrets = Secrets()
    token = secrets.get("BOSSK_TOKEN")
    if not token:
        logging.error("BOSSK_TOKEN is not set (env or %s). Generate one: python3 -c 'import secrets;print(secrets.token_urlsafe(32))'", secrets.file)
        return 1
    core = Core(cfg, token=token, secrets=secrets)
    if cfg.knowledge_dir:
        from core.knowledge import seed, start_log_writer
        created = seed(cfg.knowledge_dir)
        if created:
            logging.info("seeded knowledge folder: %s", ", ".join(created))
        start_log_writer(cfg.knowledge_dir, core.ledger)
    httpd = serve(core)
    if os.environ.get("ANTHROPIC_API_KEY"):
        logging.warning("ANTHROPIC_API_KEY is set in the core's environment. It is stripped from every Claude call; remove it anyway.")
    logging.info("BOSSK core listening on %s:%s, %d rules, brain=%s (Claude plan), notify=%s", cfg.host, cfg.port,
                 len(core.rulebook.rules), "ready" if core.cli.available and secrets.get("CLAUDE_CODE_OAUTH_TOKEN") else "NOT SET UP",
                 core.notify_cfg.enabled())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
