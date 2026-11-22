#!/usr/bin/env python3
"""Fixed reply adapter using the frozen first-party mailbox package only."""
import argparse
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
sys.pycache_prefix = '/tmp/rbx-unused-pyc-'+os.urandom(16).hex()
sys.path.insert(0, str(Path(__file__).resolve().parent/'deps'))

from strategos_irc_bot.agent_config import load_agent_config
from strategos_irc_bot.agent_store import Mailbox
import strategos_irc_bot.agent_config as config_module
import strategos_irc_bot.agent_store as store_module

_frozen = Path(__file__).resolve().parent/'deps'/'strategos_irc_bot'
if not all(Path(module.__file__).resolve().is_relative_to(_frozen)
           for module in (config_module, store_module)):
    raise RuntimeError('refuse_mutable_original_mailbox_import')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--text', required=True)
    args = parser.parse_args()
    if (not re.fullmatch('[0-9a-f]{32}', args.request) or len(args.text.encode()) > 240
            or not args.text.startswith(('PONG task=recovery-kits', 'STATUS task=recovery-kits ',
                                         'CONTEXT_OK task=', 'CONTEXT_REJECT task='))):
        raise ValueError('invalid_fixed_reply')
    Mailbox(load_agent_config(args.config)).reply(args.request, args.text)


if __name__ == '__main__':
    os.umask(0o077)
    main()
