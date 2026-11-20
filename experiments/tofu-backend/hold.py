#!/usr/bin/env python3
"""A bounded, owned synthetic provisioner; no network or external resources."""
import os,time
from pathlib import Path
if os.environ.get('RBX_TOFU_HOLD')=='1':
    root=Path(os.environ['RBX_TOFU_GATE'])
    if not root.is_absolute() or root.resolve()!=root or root.stat().st_uid!=os.getuid():raise SystemExit(2)
    (root/'ready').write_text('synthetic fixture provisioner ready\n')
    deadline=time.monotonic()+45
    while not (root/'release').exists():
        if time.monotonic()>deadline:raise SystemExit(3)
        time.sleep(.1)
