# -*- coding: utf-8 -*-
"""合规门禁：确保仓库不含竞品名、逆向工程痕迹或个人隐私信息。

任何一次提交若引入以下内容，本脚本都会让 CI 失败：
- 第三方产品的名称或其作者名
- 反向工程相关的术语（这类内容与「独立实现」的产品声明直接冲突）
- 个人隐私信息（QQ 号、本机绝对路径等）

新增禁用词时，往 FORBIDDEN 列表里加一条 (正则, 标签) 即可。
"""
import os
import re
import sys

# GitHub 的 Windows runner 控制台默认 cp1252，直接 print 中文会抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

FORBIDDEN = [
    (r"DeskSave", "竞品名"),
    (r"Blauhut", "原版作者名"),
    # 注意：短英文词必须加 \b 词边界，否则会把 interval 里的 "rva" 之类误伤
    (r"反编译|反汇编|逆向|pefile|镜像基址|代码段熵|导出函数|\bRVA\b", "逆向工程痕迹"),
    (r"494300316", "个人隐私（QQ 号）"),
    (r"[A-Za-z]:\\\\kiter", "本机绝对路径"),
]

# 二进制 / 图片类不扫
SKIP_EXT = {".png", ".jpg", ".jpeg", ".ico", ".exe", ".dll", ".zip", ".pdf"}
# 目录类不做换行转换（保持与 .gitignore 一致的防御面）
SKIP_DIRS = {".git", "__pycache__"}
# 扫描器自身包含禁用词的正则定义，属于误报源，排除
SKIP_FILES = {"compliance_scan.py"}


def main() -> int:
    hits = []
    checked = 0
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name in SKIP_FILES:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in SKIP_EXT:
                continue
            path = os.path.join(root, name)
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            checked += 1
            for pattern, label in FORBIDDEN:
                for m in re.finditer(pattern, text, re.IGNORECASE):
                    line_no = text[: m.start()].count("\n") + 1
                    hits.append(f"  {path}:{line_no}  [{label}]  {m.group(0)}")

    if hits:
        print(f"合规扫描未通过（扫描 {checked} 个文本文件，命中 {len(hits)} 处）：")
        for h in hits:
            print(h)
        print("\n处理原则：这类内容不能靠过滤隐藏，必须从源头删除。")
        return 1

    print(f"合规扫描通过（0 命中，扫描 {checked} 个文本文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
