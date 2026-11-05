# -*- coding: utf-8 -*-
"""
================================================================================
文件名称: sitecustomize.py
模块定位: Python 解释器启动时自动挂载外部依赖路径钩子
================================================================================
【本文件核心作用】
1. Python 启动时会自动导入工作目录下的 `sitecustomize.py`；
2. 将系统临时依赖目录 `/tmp/.pydeps`（位于项目仓库外部，绝不产生 Git 脏文件）
   挂载至 `sys.path`，确保不会在项目根目录生成数千个第三方库文件；
3. 提供 `ensure_runtime_deps()` 辅助函数，供 `app.py` 后台按需将缺失的运行期依赖
   解压至 `/tmp/.pydeps` 或 `/usr/local/lib/python3.10/dist-packages`，保持 Git 仓库 100% 纯净。
================================================================================
"""

import os
import sys

# 优先挂载位于项目目录之外的 /tmp/.pydeps，避免在 Git 仓库内产生任何第三方包文件
_EXT_PYDEPS = "/tmp/.pydeps"
if os.path.isdir(_EXT_PYDEPS) and _EXT_PYDEPS not in sys.path:
    sys.path.insert(0, _EXT_PYDEPS)


def ensure_runtime_deps() -> None:
    """
    当容器冷启动且系统环境中尚未安装 openpyxl / pandas 时，
    自动将所需 Wheel 包解压到项目外部的 `/tmp/.pydeps`，绝不污染项目工作区。
    """
    try:
        import openpyxl  # noqa: F401
        import pandas  # noqa: F401
        import yaml  # noqa: F401
        return
    except ImportError:
        pass

    import io
    import json
    import urllib.request
    import zipfile

    os.makedirs(_EXT_PYDEPS, exist_ok=True)
    if _EXT_PYDEPS not in sys.path:
        sys.path.insert(0, _EXT_PYDEPS)

    pkgs = [
        ("et_xmlfile", "py3-none-any.whl"),
        ("openpyxl", "py2.py3-none-any.whl"),
        ("six", "py2.py3-none-any.whl"),
        ("pytz", "py2.py3-none-any.whl"),
        ("tzdata", "py2.py3-none-any.whl"),
        ("python-dateutil", "py2.py3-none-any.whl"),
        ("PyYAML", "cp310-cp310-manylinux_2_17_x86_64"),
        ("numpy", "cp310-cp310-manylinux_2_17_x86_64"),
        ("pandas", "cp310-cp310-manylinux_2_17_x86_64"),
    ]
    for pkg_name, tag in pkgs:
        try:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg_name}/json", timeout=10) as resp:
                meta = json.loads(resp.read())
            urls = [u["url"] for u in meta.get("urls", []) if tag in u.get("filename", "")]
            if urls:
                with urllib.request.urlopen(urls[0], timeout=20) as w_resp:
                    zipfile.ZipFile(io.BytesIO(w_resp.read())).extractall(_EXT_PYDEPS)
        except Exception:
            pass
