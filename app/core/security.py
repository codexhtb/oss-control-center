"""Runtime security guardrails for the API process."""

import os


def ensure_non_root_runtime() -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError("The API process must not run as root")
