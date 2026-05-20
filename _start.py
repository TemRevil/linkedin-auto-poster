"""Launcher script to avoid path-with-spaces issues."""
import os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_server import run_server
run_server(open_browser=False)
