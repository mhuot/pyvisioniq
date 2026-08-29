"""Publish notifications to NATS via the local CLI.

Shared by the readiness CLI and the collector so both reach humans the same
way. Degrades to a no-op when the NATS binary is absent rather than making
notification a hard dependency of data collection.
"""

import json
import logging
import os
import shutil
import subprocess  # nosec B404 - used only for the pinned NATS CLI publish

logger = logging.getLogger(__name__)

NATS_BIN = os.environ.get("NATS_BIN", "/usr/local/bin/nats")
DEFAULT_SERVER = os.environ.get("NATS_SERVER", "nats://localhost:4222")

# One subject only. fleet.digest and any *.notify subject are both forwarded by
# fleet-notifier to the same Telegram chat, so publishing to both delivers
# every alert twice rather than reaching two places.
DEFAULT_SUBJECT = os.environ.get("TRIP_ALERT_SUBJECT", "fleet.karwyn.notify")


def available():
    """Whether the NATS CLI is present to publish with."""
    return bool(shutil.which(NATS_BIN) or os.path.exists(NATS_BIN))


def publish(subjects, title, body, status, server=None):
    """Publish a notification to each subject. Returns whether it was sent.

    The fleet notifier forwards a subject to humans when it ends in .alert or
    .notify, or matches its NOTIFY_SUBJECTS prefixes. It renders the payload as
    "title\\nmessage", so both keys are set here. Deliberately not published to
    fleet.<host>.cmd: that is a fixed action registry, and an unrecognised
    action is escalated rather than executed.
    """
    if not available():
        logger.debug("NATS CLI not found at %s; skipping notification", NATS_BIN)
        return False

    payload = json.dumps(
        {
            "title": title,
            "message": body,
            "body": body,
            "type": "warning" if status != "on_track" else "info",
            "source": "trip_readiness",
        }
    )
    for subject in subjects:
        subprocess.run(  # nosec B603 - fixed argv, no shell, values are not user input
            [NATS_BIN, "-s", server or DEFAULT_SERVER, "pub", subject, payload],
            check=True,
            capture_output=True,
            timeout=20,
        )
    return True
