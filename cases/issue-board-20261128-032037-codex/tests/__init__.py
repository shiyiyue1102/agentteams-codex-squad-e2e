"""开发自测包：把 case 根目录加入 sys.path，保证任意工作目录下都能 import app。"""

import os
import sys

CASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CASE_ROOT not in sys.path:
    sys.path.insert(0, CASE_ROOT)
