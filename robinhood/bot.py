"""Entry point: run with `python bot.py` from the project root.

This adds the local ``src`` directory to the import path so the bot works
without needing to `pip install` the package first.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from robinhood_pump_bot.bot import main

if __name__ == "__main__":
    main()
