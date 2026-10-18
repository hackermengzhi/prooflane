"""A fixed completion claim that runs only the deliverable's simple self-test."""

import subprocess
import sys

if __name__ == "__main__":
    subprocess.run([sys.executable, "-m", "unittest", "self_test"], check=True)
    print("Completed: shipping.py delivered; my self-test passed.")
