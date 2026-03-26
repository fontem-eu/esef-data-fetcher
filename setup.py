"""
ESEF Data Fetcher — main entry point.

Usage:
    python setup.py [--output-dir PATH] [--no-openfigi] [--upload]

Steps:
  1. Build entity registry from filings.xbrl.org
  2. Fetch financial summaries for every entity
  3. Write JSON output locally
  4. Optionally SCP output to NFS server
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn

from src.config import Config
from src.entity_registry import build_registry
from src.filing_fetcher import fetch_entity_summaries
from src.storage import write_registry, write_summary, write_metadata

console = Console()
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ESEF financial summary fetcher")
    p.add_argument("--output-dir", default=None, help="Output directory (default: esef-output/)")
    p.add_argument("--no-openfigi", action="store_true", help="Skip OpenFIGI ticker lookup")
    p.add_argument("--upload", action="store_true", help="SCP output to NFS server after fetching")
    p.add_argument("--max-workers", type=int, default=None, help="Parallel download workers")
    p.add_argument("--max-filings", type=int, default=5, help="Max filings per entity")
    return p.parse_args()


def upload(output_dir: Path, nfs_host: str, nfs_path: str) -> None:
    console.print(f"\n[bold]Uploading {output_dir} → {nfs_host}:{nfs_path}[/bold]")
    # Ensure target directory exists, then stream a tar archive over SSH.
    # rsync is not available in all environments; tar+ssh is universally available.
    mk = subprocess.run(["ssh", nfs_host, f"mkdir -p {nfs_path}/summaries"], check=False)
    if mk.returncode != 0:
        console.print("[red]Upload failed — could not create remote directory[/red]")
        sys.exit(1)
    with subprocess.Popen(
        ["tar", "-C", str(output_dir), "-cf", "-", "."],
        stdout=subprocess.PIPE,
    ) as tar_proc:
        result = subprocess.run(
            ["ssh", nfs_host, f"tar -C {nfs_path} -xf -"],
            stdin=tar_proc.stdout,
            check=False,
        )
        tar_proc.stdout.close()
    if result.returncode != 0:
        console.print("[red]Upload failed — check SSH access and NFS path[/red]")
        sys.exit(1)
    console.print("[green]Upload complete.[/green]")


def main() -> None:
    args = parse_args()
    cfg = Config()
    if args.output_dir:
        cfg.output_dir = Path(args.output_dir)
    if args.no_openfigi:
        cfg.use_openfigi = False
    if args.max_workers:
        cfg.max_workers = args.max_workers

    start = time.monotonic()
    console.print("[bold cyan]ESEF Data Fetcher[/bold cyan]")
    console.print(f"  Output dir : {cfg.output_dir}")
    console.print(f"  OpenFIGI   : {cfg.use_openfigi}")
    console.print(f"  Workers    : {cfg.max_workers}")

    # ── Step 1: Build entity registry ───────────────────────────────────────
    console.print("\n[bold]Step 1/3 — Building entity registry…[/bold]")
    registry, filing_urls = build_registry(cfg)
    write_registry(registry, cfg.output_dir)
    console.print(f"  [green]✓[/green] {len(registry)} entities in registry")

    # Build a LEI → ticker reverse map for the fetch step
    lei_to_ticker = {meta["lei"]: ticker for ticker, meta in registry.items()}

    # ── Step 2: Fetch financial summaries ───────────────────────────────────
    console.print("\n[bold]Step 2/3 — Fetching financial summaries…[/bold]")
    fetched = skipped = errors = 0
    tickers = list(registry.keys())

    def _fetch_one(ticker: str) -> tuple[str, str, list]:
        meta = registry[ticker]
        lei = meta["lei"]
        refs = filing_urls.get(lei, [])
        if not refs:
            return ticker, "skip", []
        try:
            filings = fetch_entity_summaries(
                lei,
                refs,
                request_timeout=cfg.request_timeout,
            )
            return ticker, "ok", filings
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Error fetching %s (%s): %s", ticker, lei, exc)
            return ticker, "error", []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching…", total=len(tickers))

        with ThreadPoolExecutor(max_workers=cfg.max_workers) as executor:
            futures = {executor.submit(_fetch_one, t): t for t in tickers}
            for future in as_completed(futures):
                ticker, status, filings = future.result()
                if status == "error":
                    errors += 1
                elif not filings:
                    skipped += 1
                else:
                    fetched += 1
                    write_summary(ticker, registry[ticker], filings, cfg.output_dir)
                progress.advance(task)

    elapsed = time.monotonic() - start
    write_metadata(
        cfg.output_dir,
        total_entities=len(registry),
        fetched=fetched,
        skipped=skipped,
        errors=errors,
        elapsed_seconds=elapsed,
    )

    console.print(f"\n[bold]Step 2/3 done[/bold]")
    console.print(f"  fetched={fetched}  skipped={skipped}  errors={errors}  elapsed={elapsed:.0f}s")

    # ── Step 3: Upload ───────────────────────────────────────────────────────
    if args.upload:
        console.print("\n[bold]Step 3/3 — Uploading to NFS…[/bold]")
        upload(cfg.output_dir, cfg.nfs_host, cfg.nfs_path)
    else:
        console.print(f"\n[dim]Step 3/3 — Skipped (run with --upload to push to {cfg.nfs_host}:{cfg.nfs_path})[/dim]")

    console.print(f"\n[bold green]Done in {elapsed:.0f}s[/bold green]")


if __name__ == "__main__":
    main()
