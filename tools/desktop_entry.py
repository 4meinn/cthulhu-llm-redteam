"""Entry point for the packaged desktop app."""

import os
import sys

# A windowed build has no console, so the standard streams are None. Give them a
# sink before anything (uvicorn's logging, print) writes to them.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w"))

from cthulhu.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["app"]))
