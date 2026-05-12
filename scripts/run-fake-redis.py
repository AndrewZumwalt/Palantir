"""Stand-alone TCP fake-redis server for the Windows multi-process launcher.

start-laptop.ps1 spawns six service processes; an in-process fakeredis only
gives each process its OWN copy of the keyspace, so heartbeats, wake-word
events, and every other pub/sub never crosses the process boundary -- the
dashboard shows "NO HEARTBEAT" for every service and the wake-word -> brain
-> tts pipeline is silently broken.

This wraps fakeredis.TcpFakeServer so every service can speak the real
Redis protocol over TCP to a single shared in-memory store.  No external
Redis install required (uses fakeredis from the [dev] extras).
"""

from __future__ import annotations

import argparse
import sys

from fakeredis import TcpFakeServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6390)
    args = parser.parse_args()

    server = TcpFakeServer((args.host, args.port))
    print(f"fakeredis listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
