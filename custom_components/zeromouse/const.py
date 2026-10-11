"""Constants for the ZeroMOUSE integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "zeromouse"
MANUFACTURER: Final = "ZeroMOUSE"

UPDATE_INTERVAL: Final = timedelta(seconds=60)
# Polling is only a safety net while the live connection is up.
UPDATE_INTERVAL_LIVE: Final = timedelta(minutes=5)
# After a live event, poll again shortly to pick up images and counters.
LIVE_FOLLOWUP_DELAY: Final = 20  # seconds

# Backend configuration, taken from the ZeroMOUSE Android app (v1.14.18).
REGION: Final = "eu-central-1"
USER_POOL_ID: Final = "eu-central-1_LS6CKN0t1"
USER_POOL_CLIENT_ID: Final = "7pdec0rbivg5hg8u3pke4veg0f"
IDENTITY_POOL_ID: Final = "eu-central-1:2b2f7d40-d6f9-474e-a06b-6441c4059601"
GRAPHQL_URL: Final = (
    "https://f36gc6o7jnewxe37dhn3fochza.appsync-api.eu-central-1.amazonaws.com/graphql"
)
REST_BASE_URL: Final = "https://4oj9bcxedi.execute-api.eu-central-1.amazonaws.com/DEV"
IMAGE_BUCKET: Final = "mbr-ptf-images-eu-central-1-dev"
APP_VERSION: Final = "1.14.18"

# Number of events fetched per poll; the newest visible one is used.
EVENT_FETCH_LIMIT: Final = 5

# Values of `classification_byNet` seen in the app.
CLASSIFICATION_UNKNOWN: Final = "unknown"
CLASSIFICATIONS: Final = [
    "prey",
    "clean",
    "early",
    "late",
    "out",
    "free",
    "test",
    "undecidable",
    CLASSIFICATION_UNKNOWN,
]
CLASSIFICATION_PREY: Final = "prey"

# Shadow `system.kkzmBlockModeIn` / `system.kkzmBlockModeOut`.
ENTRY_MODES: Final = {
    0: "free_entry",
    1: "blocking_prey",
    2: "face_detection",
    3: "locked",
}
EXIT_MODES: Final = {
    0: "open",
    1: "locked",
    2: "time_based",
}

EVENT_NEW_DETECTION: Final = f"{DOMAIN}_event"

IMAGE_SUBFOLDER: Final = "zeromouse"
MAX_IMAGES_PER_EVENT: Final = 20
