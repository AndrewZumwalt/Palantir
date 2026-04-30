"""Pi <-> laptop relay.

When `[input.source] = "relay"` (and `[output.sink] = "relay"`) the Pi runs a
single thin client (`palantir-pi-relay`) that streams mic + camera frames
to the laptop's web service and plays back synthesized speech the laptop
sends in return.  All ML and orchestration runs on the laptop unchanged —
only the *capture* and *output* layers swap from local hardware to a
network bridge.
"""
