"""Compatibility entry point: python auth.py [--write] [--force]."""

from gtasks_mcp.auth import main

if __name__ == "__main__":
    raise SystemExit(main())
