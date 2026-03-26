"""Runtime configuration for the ESEF data fetcher."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    """All tuneable parameters in one place."""

    # Where to write output locally before uploading
    output_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("ESEF_OUTPUT_DIR", "esef-output")
    ))

    # filings.xbrl.org pagination / concurrency
    max_filings_per_entity: int = 5   # keep N most recent annual reports
    request_timeout: int = 30
    max_workers: int = 8              # parallel JSON downloads

    # OpenFIGI ticker lookup
    use_openfigi: bool = True
    openfigi_api_key: str = field(default_factory=lambda: os.environ.get("OPENFIGI_API_KEY", ""))
    openfigi_batch_size: int = 10    # items per API call (free limit)

    # Upload
    nfs_host: str = field(default_factory=lambda: os.environ.get("NFS_HOST", "root@10.44.0.6"))
    nfs_path: str = field(default_factory=lambda: os.environ.get("NFS_PATH", "/srv/nfs/gmr/esef"))
