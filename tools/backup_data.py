#!/usr/bin/env python3
"""Rotating compressed backups of the CSV data and the API cache.

Grandfather-father-son rotation: the newest daily is promoted to the weekly
tier once a week and to the monthly tier once a month, then each tier is
pruned to its retention. Defaults: 7 dailies, 9 weeklies (~two months),
60 monthlies (five years).

Runs on the host, not in a container: it only reads the data and cache
volumes, the host has zstd, and a backup process that lives inside the thing
it backs up disappears with it.

Every archive is recorded in ``backups/index.jsonl`` with its sha256, size,
file count, and the git commit of the code at the time of writing — so a
restore can pair old data with the schema the code expected then.

Usage:
    python tools/backup_data.py            # create today's backup + rotate
    python tools/backup_data.py --dry-run  # show what would happen
"""

import argparse
import hashlib
import json
import subprocess  # nosec B404 - tar/git with fixed argv, no shell
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKUP_ROOT = REPO / "backups"
INDEX = BACKUP_ROOT / "index.jsonl"

# What to back up. The working backups of previous tools (*.backup_*, *.bak)
# are excluded: they are themselves recovery copies, and including them would
# roughly double the archive for no additional information.
SOURCES = ["data", "cache"]
EXCLUDES = ["*.backup_*", "*.bak", ".store.lock"]

TIERS = {
    "daily": {"keep": 7, "min_gap_days": 1},
    "weekly": {"keep": 9, "min_gap_days": 7},
    "monthly": {"keep": 60, "min_gap_days": 28},
}


def git_commit():
    """Current commit hash, or 'unknown' outside a repo."""
    try:
        result = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def newest_in(tier_dir):
    archives = sorted(tier_dir.glob("*.tar.zst"))
    return archives[-1] if archives else None


def age_days(path, now):
    stamp = datetime.strptime(path.name.split(".")[0].split("_")[1], "%Y%m%d")
    return (now - stamp).days


def create_archive(now, dry_run):
    """Tar and compress the sources into today's daily archive."""
    daily_dir = BACKUP_ROOT / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    archive = daily_dir / f"backup_{now.strftime('%Y%m%d')}.tar.zst"
    if archive.exists():
        print(f"already exists: {archive.name}")
        return archive, False

    command = ["tar", "--zstd", "-cf", str(archive), "-C", str(REPO)]
    for pattern in EXCLUDES:
        command += ["--exclude", pattern]
    command += SOURCES

    if dry_run:
        print("would run:", " ".join(command))
        return archive, False

    subprocess.run(command, check=True, timeout=600)  # nosec B603
    # Verify the archive is readable before trusting it.
    subprocess.run(  # nosec B603 B607
        ["tar", "--zstd", "-tf", str(archive)],
        check=True,
        capture_output=True,
        timeout=600,
    )
    return archive, True


def promote(archive, tier, now, dry_run):
    """Copy today's archive into a slower tier when that tier is due."""
    tier_dir = BACKUP_ROOT / tier
    tier_dir.mkdir(parents=True, exist_ok=True)
    newest = newest_in(tier_dir)
    if newest and age_days(newest, now) < TIERS[tier]["min_gap_days"]:
        return None
    target = tier_dir / archive.name
    if dry_run:
        print(f"would promote to {tier}: {target.name}")
        return None
    target.write_bytes(archive.read_bytes())
    return target


def prune(tier, dry_run):
    """Drop the oldest archives beyond the tier's retention."""
    tier_dir = BACKUP_ROOT / tier
    if not tier_dir.exists():
        return []
    archives = sorted(tier_dir.glob("*.tar.zst"))
    excess = archives[: max(0, len(archives) - TIERS[tier]["keep"])]
    for path in excess:
        if dry_run:
            print(f"would prune {tier}: {path.name}")
        else:
            path.unlink()
    return excess


def record(archive, tiers, now):
    """Append this backup to the index."""
    entry = {
        "created": now.isoformat(timespec="seconds"),
        "archive": archive.name,
        "tiers": tiers,
        "size_bytes": archive.stat().st_size,
        "sha256": sha256_of(archive),
        "git_commit": git_commit(),
    }
    with open(INDEX, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = datetime.now()

    archive, created = create_archive(now, args.dry_run)

    tiers = ["daily"]
    for tier in ("weekly", "monthly"):
        if archive.exists() or args.dry_run:
            if promote(archive, tier, now, args.dry_run):
                tiers.append(tier)

    for tier in TIERS:
        prune(tier, args.dry_run)

    if created:
        entry = record(archive, tiers, now)
        print(
            f"{archive.name}: {entry['size_bytes']/1e6:.2f} MB, tiers {tiers}, "
            f"commit {entry['git_commit'][:9]}"
        )


if __name__ == "__main__":
    main()
