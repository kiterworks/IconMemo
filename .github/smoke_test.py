# -*- coding: utf-8 -*-
"""CI 冒烟测试：验证引擎可导入、.dsv 解析正常。

只做不依赖桌面环境的纯逻辑检查，保证在任何 Windows runner 上都能稳定通过。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import iconmemo as E  # noqa: E402


def main() -> int:
    # 1) 模块基本能力
    assert callable(E.parse_dsv), "parse_dsv 不存在"
    assert callable(E.import_dsv), "import_dsv 不存在"
    assert callable(E.read_icons), "read_icons 不存在"
    assert callable(E.set_icon_positions), "set_icon_positions 不存在"
    print("[OK] 引擎核心函数齐备")

    # 2) 解析内联 .dsv 样本（UTF-8 BOM + 含空格中文名）
    sample = (
        "\ufeffSampleTool 1.0\n"
        "# 1920x1080\n"
        "# 20260101000000\n"
        "# Grid\n"
        "100 200 此电脑\n"
        "300 400 火绒  安全软件\n"
        "500 600 带  多个   空格\n"
    ).encode("utf-8")

    fd, path = tempfile.mkstemp(suffix=".dsv")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(sample)
        r = E.parse_dsv(path)
        assert r["width"] == 1920 and r["height"] == 1080, r
        assert len(r["icons"]) == 3, len(r["icons"])
        assert r["icons"][0]["name"] == "此电脑", repr(r["icons"][0]["name"])
        assert r["icons"][1]["name"] == "火绒  安全软件", repr(r["icons"][1]["name"])
        assert r["icons"][2]["name"] == "带  多个   空格", repr(r["icons"][2]["name"])
        assert r["snap_to_grid"] is True, r["snap_to_grid"]
        print("[OK] .dsv 解析正常（3 图标，含空格中文名无损）")
    finally:
        os.unlink(path)

    # 3) 档案键推导
    key = E._profile_from_resolution(1920, 1080, 96, 1)
    assert key == "1920x1080@96dpi_1mon", key
    print("[OK] 档案键推导正常")

    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
