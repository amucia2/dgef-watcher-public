"""
run.py — thin wrapper that loads .env before running the watcher.
Usage:  python run.py
"""
from dotenv import load_dotenv
load_dotenv()          # reads .env in the current directory
from watcher import run
run()