#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAP2FAST 1104 版本等价入口别名 (sapfast_full_fix.py -> sap2fast_full_fix.py)
确保无论通过 `python sap2fast_full_fix.py` 还是 `python sapfast_full_fix.py` 调用均 100% 一致。
"""
from sap2fast_full_fix import *  # noqa: F401,F403
from sap2fast_full_fix import main

if __name__ == "__main__":
    main()
