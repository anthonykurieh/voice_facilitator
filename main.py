"""Main entry point for the Voice Assistant."""
import sys
import os

# Check if we're using the right Python (conda vs system)
# This helps catch the common issue of using system Python instead of conda Python
try:
    import openai
except ImportError:
    python_path = sys.executable
    print(f"ERROR: Required packages not found in Python at: {python_path}")
    print("\nThis usually means you're using the wrong Python interpreter.")
    print("\nSolutions:")
    print("1. Use conda Python: python main.py (not /usr/local/bin/python3)")
    print("2. Or install packages: pip install -r requirements.txt")
    print("\nTo check which Python you're using:")
    print(f"  Current: {python_path}")
    print(f"  Conda Python: {os.path.expanduser('~/anaconda3/bin/python')}")
    sys.exit(1)

from src.voice_loop import main

if __name__ == "__main__":
    main()

