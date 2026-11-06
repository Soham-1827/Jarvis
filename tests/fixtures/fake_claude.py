#!/usr/bin/env python3
"""Stand-in for the `claude` CLI in tests. Controlled by FAKE_* environment variables."""

import json
import os
import sys
import time
from pathlib import Path

prompt = sys.stdin.read()
if out := os.environ.get("FAKE_ARGV_OUT"):
    Path(out).write_text(json.dumps({"argv": sys.argv[1:], "stdin": prompt}))
if delay := os.environ.get("FAKE_SLEEP"):
    time.sleep(float(delay))
if fixture := os.environ.get("FAKE_FIXTURE"):
    sys.stdout.write(Path(fixture).read_text())
    sys.stdout.flush()
if err := os.environ.get("FAKE_STDERR"):
    sys.stderr.write(err)
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
