import os
import sys

# the app imports its modules from lib/ (see kiln-controller.py)
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, ROOT)
