#!/usr/bin/env python3
"""Start the bounded read-only daemon; see docs/edge-daemon-design.md."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from synafly_lab.edge_daemon import main
if __name__=='__main__': main()
