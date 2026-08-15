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

# Offsite replica. rsync mirrors the whole rotation after each run, so the
# remote holds the same tiers and prunes track automatically via --delete.
# Empty REMOTE disables the sync (e.g. when running on a machine without the
# key). The Z2 is on the tailnet, so transport rides the existing WireGuard.
REMOTE_HOST = "mhuot@mike-huot-z2-mini-g3.quagga-ide.ts.net"
REMOTE_DIR = "pyvisionic-backups"
REMOTE = f"{REMOTE_HOST}:{REMOTE_DIR}/"

# Alerts ride the existing fleet bus: the notifier forwards *.notify to
# Telegram. Same path as the trip readiness checker.
NATS_BIN = "/usr/local/bin/nats"
ALERT_SUBJECT = "fleet.karwyn.notify"

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


def sync_offsite(dry_run):
    """Mirror the backup tree to the offsite host; failure is non-fatal.

    --delete keeps the remote rotation identical to the local one, so pruning
    propagates. A sync failure must not fail the backup itself: local backup
    first, replication second.
    """
    if not REMOTE:
        return
    command = [
        "rsync",
        "-az",
        "--delete",
        "--timeout=60",
        str(BACKUP_ROOT) + "/",
        REMOTE,
    ]
    if dry_run:
        print("would run:", " ".join(command))
        return
    try:
        subprocess.run(command, check=True, timeout=600)  # nosec B603 B607
        print(f"offsite sync OK -> {REMOTE}")
    except (subprocess.SubprocessError, OSError) as sync_error:
        print(f"WARNING: offsite sync failed: {sync_error}")


def indexed_checksums():
    """archive name -> sha256 from the index; the newest entry wins."""
    checksums = {}
    if INDEX.exists():
        with open(INDEX, encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                    checksums[entry["archive"]] = entry["sha256"]
                except (ValueError, KeyError):
                    continue
    return checksums


def alert(title, body):
    """Publish a warning onto the fleet bus; best-effort."""
    payload = json.dumps({"title": title, "message": body, "type": "warning"})
    try:
        subprocess.run(  # nosec B603
            [NATS_BIN, "-s", "nats://localhost:4222", "pub", ALERT_SUBJECT, payload],
            check=True,
            capture_output=True,
            timeout=20,
        )
    except (subprocess.SubprocessError, OSError) as nats_error:
        print(f"WARNING: could not publish alert: {nats_error}")


def verify_offsite(now, dry_run):
    """Checksum the replica against the index; alert on any divergence.

    A replica that silently rots is worse than none, because it retires the
    worry without providing the protection. Daily runs verify today's archive;
    Sundays verify every archive on the remote. Verification happens on the
    remote host (ssh + sha256sum) so it reads the replica's actual bytes
    rather than the local copies that were just synced.
    """
    if not REMOTE or dry_run:
        return
    weekly = now.weekday() == 6
    expected = indexed_checksums()
    target = (
        f"{REMOTE_DIR}/"
        if weekly
        else f"{REMOTE_DIR}/daily/backup_{now.strftime('%Y%m%d')}.tar.zst"
    )
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        REMOTE_HOST,
        f"find {target} -name '*.tar.zst' -exec sha256sum {{}} +",
    ]
    try:
        result = subprocess.run(  # nosec B603 B607
            command, capture_output=True, text=True, timeout=300, check=True
        )
    except (subprocess.SubprocessError, OSError) as ssh_error:
        alert(
            "Backup replica unreachable",
            f"Could not verify pyvisionic backups on the Z2: {ssh_error}",
        )
        print(f"WARNING: offsite verification failed to run: {ssh_error}")
        return

    problems = []
    seen = 0
    for line in result.stdout.splitlines():
        digest, _, path = line.partition("  ")
        name = path.strip().rsplit("/", 1)[-1]
        if name not in expected:
            continue
        seen += 1
        if digest != expected[name]:
            problems.append(f"{path.strip()}: checksum mismatch")
    if seen == 0:
        problems.append(f"no indexed archives found under {target}")

    if problems:
        body = "Offsite backup verification failed:\n" + "\n".join(problems[:10])
        alert("Backup replica verification FAILED", body)
        print("WARNING:", body)
    else:
        scope = "all archives" if weekly else "today's archive"
        print(f"offsite verification OK ({scope}, {seen} checked)")


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

    sync_offsite(args.dry_run)
    verify_offsite(now, args.dry_run)


if __name__ == "__main__":
    main()
