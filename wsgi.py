import sys
import os

# Add your project directory to the sys.path
path = '/home/allbibek/ytta'
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application