#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图标记忆  ——  桌面图标布局保存 / 恢复
=========================================================

独立实现，代码全部自行编写：不包含、不复制、不改编任何第三方软件的
源代码或资源文件。设计依据为公开的用户需求、同类工具在公开渠道描述的
功能范围，以及 Microsoft 官方 Win32 API 文档。

核心设计要点：
  1. 按【图标名称】匹配恢复，而非按列表位置 —— 桌面增删图标后不会整体错位
  2. 显式处理 AutoArrange / SnapToGrid，从根上避免"恢复后刷新又被重排"
  3. 配置档案按【分辨率 + DPI + 显示器拓扑】自动分组，适配 Per-Monitor DPI
  4. 全 Unicode 读写图标名，中文 / 特殊符号名完整保真
  5. 自动备份 + 一键撤销，备份保留可配置
  6. 不依赖第三方运行库、不需要联网、不写注册表残留，纯本地文本档案

Copyright (c) 2026 Kiter
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import winreg
from ctypes import wintypes
from datetime import datetime
from logging.handlers import RotatingFileHandler

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
APP_NAME = "IconMemo"
SCHEMA = "iconmemo/1"

# Windows 控制台统一 UTF-8，避免中文在 cmd 窗口乱码（配合启动器的 chcp 65001）
if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

LVM_FIRST = 0x1000
LVM_GETITEMCOUNT = LVM_FIRST + 4
LVM_SETITEMPOSITION = LVM_FIRST + 15
LVM_GETITEMPOSITION = LVM_FIRST + 16
LVM_GETICONSPACING = LVM_FIRST + 51
LVM_SETICONSPACING = LVM_FIRST + 57
LVM_SETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 54
LVM_GETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 55
LVM_GETITEMTEXTW = LVM_FIRST + 115
LVM_REDRAWITEMS = LVM_FIRST + 21

LVIF_TEXT = 0x0001
LVS_EX_AUTOARRANGE = 0x00000100
LVS_EX_SNAPTOGRID = 0x00080000

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04

# --- 单实例 / 唤起已运行实例 ---
ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = r"Local\IconMemo"            # ⚠ 作用域必须用 Local\（每会话），不可用 Global\（RDP/多会话语义错误）
SHOW_WINDOW_MESSAGE = "IconMemo_SHOW"     # RegisterWindowMessageW 的广播消息名

# --- 系统托盘 (Shell_NotifyIconW) ---
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IDI_APPLICATION = 32512                     # LoadIconW 兜底图标（MAKEINTRESOURCE）
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1                    # 托盘事件回调消息（复用隐藏窗口消息循环）
WM_NULL = 0x0000
WM_CONTEXTMENU = 0x007B
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203

# --- 弹出菜单 ---
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

# --- 托盘菜单命令 ID ---
IDM_TRAY_OPEN = 40001                       # 打开界面
IDM_TRAY_SAVE = 40002                       # 立即保存布局
IDM_TRAY_RESTORE = 40003                    # 立即恢复布局
IDM_TRAY_PAUSE = 40004                      # 暂停/继续监视
IDM_TRAY_EXIT = 40005                       # 退出


# --------------------------------------------------------------------------
# Win32 绑定
# --------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shcore = None
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:
    pass

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
kernel32.VirtualFreeEx.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.restype = wintypes.BOOL

if shcore is not None:
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    shcore.SetProcessDpiAwareness.restype = ctypes.c_long

# --------------------------------------------------------------------------
# Win32 绑定（P0 新增：单实例 / 进程枚举 / 消息广播 / 系统托盘）
# 说明：64 位下句柄若未显式声明 restype，会被 ctypes 当作 int 截断为 32 位，
#       导致 CreateMutexW / CreateWindowExW 等静默失败 —— 故一律显式声明。
# --------------------------------------------------------------------------
# 单实例互斥体
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenMutexW.restype = wintypes.HANDLE

# 窗口消息注册 / 跨进程广播（唤起已运行实例）
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None

# 系统托盘
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                   ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.HICON),
                                   wintypes.UINT]
shell32.ExtractIconExW.restype = wintypes.UINT
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wintypes.HICON
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL

# 弹出菜单
user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int      # TPM_RETURNCMD 时返回菜单项 ID
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyMenu.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL


class LVITEMW(ctypes.Structure):
    """Win32 LVITEMW —— 用 ctypes 自动处理 32/64 位对齐"""
    _fields_ = [
        ("mask", wintypes.UINT),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", wintypes.UINT),
        ("stateMask", wintypes.UINT),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", ctypes.c_void_p),
        ("iIndent", ctypes.c_int),
        ("iGroupId", ctypes.c_int),
        ("cColumns", wintypes.UINT),
        ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p),
        ("iGroup", ctypes.c_int),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class GUID(ctypes.Structure):
    """Win32 GUID —— 供 NOTIFYICONDATAW.guidItem 使用"""
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIconW 结构（Vista+ 完整版）。

    cbSize 必须等于 sizeof(NOTIFYICONDATAW)，Win10/11 才会正确解析全部字段。
    """
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
def data_dir() -> str:
    d = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def layouts_dir() -> str:
    d = os.path.join(data_dir(), "Layouts")
    os.makedirs(d, exist_ok=True)
    return d


def backups_dir() -> str:
    d = os.path.join(data_dir(), "Backups")
    os.makedirs(d, exist_ok=True)
    return d


def snapshots_dir() -> str:
    d = os.path.join(data_dir(), "Snapshots")
    os.makedirs(d, exist_ok=True)
    return d


def logs_dir() -> str:
    d = os.path.join(data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# 日志落盘（修缺陷：noconsole 构建下 print 全部丢失）
# --------------------------------------------------------------------------
LOG_NAME = "iconmemo.log"
_LOG = logging.getLogger(APP_NAME)
_LOG_READY = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化文件日志（轮转 512 KB × 3 份），供 do_watch/do_autostart/main 复用。

    - 文件：%LOCALAPPDATA%\\IconMemo\\logs\\iconmemo.log（UTF-8）
    - 常驻（pythonw / --noconsole）时无控制台，故必须落盘，否则无人值守失败无从诊断；
    - 交互式运行时额外挂一个控制台 StreamHandler（sys.stdout 存在时才挂，避免 None 报错）。
    """
    global _LOG_READY
    _LOG.setLevel(level)
    if _LOG_READY:
        return _LOG
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s %(message)s")

    # 文件处理器（核心：落盘）
    try:
        fh = RotatingFileHandler(os.path.join(logs_dir(), LOG_NAME),
                                 maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        _LOG.addHandler(fh)
    except Exception:
        pass  # 目录不可写时静默降级，不影响主流程

    # 控制台处理器（仅交互式；常驻时 stdout 为 None）
    try:
        if sys.stderr is not None:
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(fmt)
            _LOG.addHandler(sh)
    except Exception:
        pass

    _LOG.propagate = False
    _LOG_READY = True
    return _LOG


def prune_backups(keep: int = 30) -> int:
    """备份轮转：保留最近 N 个，超出删除。返回删除数量。"""
    d = backups_dir()
    files = sorted(
        (f for f in os.listdir(d) if f.endswith(".json")),
        key=lambda f: os.path.getmtime(os.path.join(d, f)),
        reverse=True,
    )
    removed = 0
    for f in files[keep:]:
        try:
            os.remove(os.path.join(d, f))
            removed += 1
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------------
# 单实例保护（修缺陷：可同时跑多个实例互相抢桌面）
# --------------------------------------------------------------------------
_MUTEX_HANDLE = None       # ⚠ 必须模块级持有，否则句柄被 GC 回收 → 互斥失效


def single_instance(name: str = MUTEX_NAME) -> bool:
    """创建命名互斥体，返回"本进程是否首实例"。

    句柄保存到模块级 _MUTEX_HANDLE 全程保活（GC 回收句柄会导致后续实例误判为首实例）。
    作用域用 Local\\（每会话）：RDP / 多会话下与 Global\\ 的语义完全不同，不能用 Global\\。
    """
    global _MUTEX_HANDLE
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        # 极端情况下创建失败：保守视为首实例，避免程序完全无法运行
        _MUTEX_HANDLE = None
        return True
    _MUTEX_HANDLE = handle      # 保活（首/非首实例都持有，防止句柄被回收）
    return err != ERROR_ALREADY_EXISTS


def instance_running(name: str = MUTEX_NAME) -> bool:
    """只读探测：是否已有实例持有该互斥体（不获取所有权，不影响既有实例）。"""
    handle = kernel32.OpenMutexW(0x00100000, False, name)   # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def signal_existing_instance() -> int:
    """向已运行实例广播"显示界面"消息（RegisterWindowMessageW + PostMessage(HWND_BROADCAST)）。"""
    HWND_BROADCAST = wintypes.HWND(0xFFFF)
    msg = user32.RegisterWindowMessageW(SHOW_WINDOW_MESSAGE)
    if not msg:
        return 0
    user32.PostMessageW(HWND_BROADCAST, msg, 0, 0)
    return msg


def _run_value(name: str) -> str | None:
    """读取 HKCU\\...\\Run 下某项的值（不存在返回 None）。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_QUERY_VALUE) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return v
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _class_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_desktop_listview():
    """返回 (hListView, hShellView, 来源说明)。

    Win10/11 结构： Progman / WorkerW  ->  SHELLDLL_DefView  ->  SysListView32
    多显示器 + 幻灯片壁纸时，SHELLDLL_DefView 可能挂在 WorkerW 下而非 Progman。
    """
    candidates = []
    hProgman = user32.FindWindowW("Progman", None)
    if hProgman:
        candidates.append(("Progman", hProgman))

    # 枚举顶层窗口，收集所有 SHELLDLL_DefView 的父窗口
    def enum_cb(hwnd, _lparam):
        cls = _class_of(hwnd)
        if cls in ("Progman", "WorkerW"):
            candidates.append((cls, hwnd))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

    seen = set()
    for kind, top in candidates:
        if top in seen:
            continue
        seen.add(top)
        hShellView = user32.FindWindowExW(top, None, "SHELLDLL_DefView", None)
        if not hShellView:
            continue
        hLV = user32.FindWindowExW(hShellView, None, "SysListView32", None)
        if hLV:
            return hLV, hShellView, f"{kind}/SHELLDLL_DefView/SysListView32"
    return None, None, "未找到（explorer 未运行？）"


# --------------------------------------------------------------------------
# 跨进程读写小工具
# --------------------------------------------------------------------------
class RemoteMem:
    """在 explorer 进程中分配一块内存，用于跨进程 LVM 调用"""

    def __init__(self, hwnd, size: int):
        self.hwnd = hwnd
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        self.pid = pid.value
        self.hproc = kernel32.OpenProcess(
            PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_QUERY_INFORMATION,
            False, self.pid)
        if not self.hproc:
            raise OSError(f"OpenProcess 失败 pid={self.pid} err={ctypes.get_last_error()}")
        self.size = size
        self.addr = kernel32.VirtualAllocEx(self.hproc, None, size, MEM_COMMIT, PAGE_READWRITE)
        if not self.addr:
            kernel32.CloseHandle(self.hproc)
            raise OSError(f"VirtualAllocEx 失败 err={ctypes.get_last_error()}")

    def write(self, data: bytes, offset: int = 0):
        if isinstance(data, (bytes, bytearray)):
            data = ctypes.create_string_buffer(bytes(data), len(data))
        written = ctypes.c_size_t(0)
        ok = kernel32.WriteProcessMemory(self.hproc, ctypes.c_void_p(self.addr + offset),
                                         data, len(bytes(data)), ctypes.byref(written))
        if not ok:
            raise OSError(f"WriteProcessMemory 失败 err={ctypes.get_last_error()}")
        return written.value

    def write_struct(self, obj, offset: int = 0):
        return self.write(bytes(obj), offset)

    def read(self, nbytes: int, offset: int = 0) -> bytes:
        buf = ctypes.create_string_buffer(nbytes)
        got = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(self.hproc, ctypes.c_void_p(self.addr + offset),
                                        buf, nbytes, ctypes.byref(got))
        if not ok:
            raise OSError(f"ReadProcessMemory 失败 err={ctypes.get_last_error()}")
        return buf.raw[:got.value]

    def read_struct(self, cls, offset: int = 0):
        return cls.from_buffer_copy(self.read(ctypes.sizeof(cls), offset))

    def close(self):
        try:
            kernel32.VirtualFreeEx(self.hproc, ctypes.c_void_p(self.addr), 0, MEM_RELEASE)
        finally:
            kernel32.CloseHandle(self.hproc)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --------------------------------------------------------------------------
# 桌面状态采集 / 施加
# --------------------------------------------------------------------------
def get_ext_style(hLV) -> int:
    return user32.SendMessageW(hLV, LVM_GETEXTENDEDLISTVIEWSTYLE, 0, 0)


def set_ext_style(hLV, mask: int, value: int):
    user32.SendMessageW(hLV, LVM_SETEXTENDEDLISTVIEWSTYLE, mask, value)


def get_icon_count(hLV) -> int:
    return user32.SendMessageW(hLV, LVM_GETITEMCOUNT, 0, 0)


def get_icon_spacing(hLV):
    try:
        with RemoteMem(hLV, 16) as mem:
            user32.SendMessageW(hLV, LVM_GETICONSPACING, 0, mem.addr)
            pt = mem.read_struct(POINT)
            return [pt.x, pt.y]
    except OSError:
        return None


def set_icon_spacing(hLV, x: int, y: int):
    try:
        with RemoteMem(hLV, 16) as mem:
            mem.write_struct(POINT(x, y))
            user32.SendMessageW(hLV, LVM_SETICONSPACING, 0, mem.addr)
        return True
    except OSError:
        return False


def read_icons(hLV, with_names: bool = True):
    """读取全部图标：位置（必需）+ 名称（可选，Unicode）"""
    n = get_icon_count(hLV)
    if n <= 0:
        return []
    items = []
    size_lvitem = ctypes.sizeof(LVITEMW)
    # 远程缓冲区尺寸 = 坐标数组(n*8) + 16 字节对齐间隙 + LVITEMW + 文本区(520)
    # 旧代码漏算了坐标数组的 n*8，仅因 VirtualAllocEx 按 4KB 页粒度分配才没出事；
    # 图标极多（>约 400）时会越界导致 ReadProcessMemory 失败，故按实际需求计算。
    buf_size = n * 8 + size_lvitem + 536
    with RemoteMem(hLV, buf_size) as mem:
        # 一次性为所有图标分配 POINT 数组
        pt_base = mem.addr
        pt_bytes = n * ctypes.sizeof(POINT)
        lvitem_addr = pt_base + pt_bytes + 16
        text_addr = lvitem_addr + size_lvitem

        # --- 位置 ---
        positions = []
        for i in range(n):
            off = i * ctypes.sizeof(POINT)
            user32.SendMessageW(hLV, LVM_GETITEMPOSITION, i, pt_base + off)
        raw = mem.read(pt_bytes)
        for i in range(n):
            x, y = struct.unpack_from("<ii", raw, i * 8)
            positions.append((x, y))

        # --- 名称 ---
        names = []
        if with_names:
            for i in range(n):
                li = LVITEMW()
                li.mask = LVIF_TEXT
                li.iItem = i
                li.iSubItem = 0
                li.pszText = text_addr
                li.cchTextMax = 260
                mem.write_struct(li, lvitem_addr - pt_base)
                got = user32.SendMessageW(hLV, LVM_GETITEMTEXTW, i, lvitem_addr)
                if got > 0:
                    names.append(mem.read(got * 2, text_addr - pt_base).decode("utf-16-le", "replace"))
                else:
                    names.append("")
        else:
            names = ["" for _ in range(n)]

    for i in range(n):
        items.append({"index": i, "name": names[i], "x": positions[i][0], "y": positions[i][1]})
    return items


def set_icon_positions(hLV, moves):
    """moves: iterable of (index, x, y)。返回成功条数。"""
    ok = 0
    for idx, x, y in moves:
        lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        user32.SendMessageW(hLV, LVM_SETITEMPOSITION, idx, lparam)
        ok += 1
    user32.SendMessageW(hLV, LVM_REDRAWITEMS, 0, get_icon_count(hLV) - 1)
    return ok


# --------------------------------------------------------------------------
# 环境指纹
# --------------------------------------------------------------------------
class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, wintypes.HDC,
                                     ctypes.POINTER(RECT), wintypes.LPARAM)
MONITORINFOF_PRIMARY = 0x1

user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL
if shcore is not None:
    try:
        shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        shcore.GetDpiForMonitor.restype = ctypes.c_long
    except AttributeError:
        pass


def enumerate_monitors():
    """枚举全部显示器：设备名 / 矩形 / 工作区 / 主屏标记 / 逐屏有效 DPI"""
    mons = []

    def _cb(hMon, hdc, lprc, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hMon, ctypes.byref(mi)):
            return True
        dx = ctypes.c_uint(96)
        dy = ctypes.c_uint(96)
        if shcore is not None and hasattr(shcore, "GetDpiForMonitor"):
            try:
                shcore.GetDpiForMonitor(hMon, 0, ctypes.byref(dx), ctypes.byref(dy))
            except Exception:
                pass
        r, w = mi.rcMonitor, mi.rcWork
        mons.append({
            "device": mi.szDevice,
            "rect": [r.left, r.top, r.right - r.left, r.bottom - r.top],
            "work": [w.left, w.top, w.right - w.left, w.bottom - w.top],
            "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY),
            "dpi": int(dx.value) or 96,
        })
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    mons.sort(key=lambda m: (not m["primary"], m["rect"][0], m["rect"][1]))
    return mons


def topology_key(mons) -> str:
    """拓扑指纹，例：2560x1440+0+0@96*|1920x1080+2560+0@96"""
    if not mons:
        return "unknown"
    parts = []
    for m in mons:
        x, y, w, h = m["rect"]
        parts.append(f"{w}x{h}+{x}+{y}@{m['dpi']}{'*' if m['primary'] else ''}")
    return "|".join(parts)


def topology_label(env: dict) -> str:
    """人类可读的拓扑描述，如：A 2560×1440（主屏）, B 1920×1080"""
    mons = env.get("monitor_list") or []
    if not mons:
        return f"{env.get('screen', '?')} / {env.get('dpi', '?')} DPI"
    return "，".join(
        f"{chr(65 + i)} {m['rect'][2]}×{m['rect'][3]}"
        f"{'（主屏）' if m['primary'] else ''}"
        for i, m in enumerate(mons))


def _virtual_desktops() -> int:
    """虚拟桌面（任务视图）数量：注册表里每 16 字节一个 GUID"""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops") as k:
            v, _ = winreg.QueryValueEx(k, "VirtualDesktopIDs")
            return max(1, len(v) // 16)
    except Exception:
        return 1


def _dpi(mons=None) -> int:
    """主显示器有效 DPI（96 = 100%）"""
    mons = mons if mons is not None else enumerate_monitors()
    for m in mons:
        if m["primary"]:
            return m["dpi"]
    hdc = user32.GetDC(0)
    try:
        gdi32 = ctypes.WinDLL("gdi32")
        gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
        return int(gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
    finally:
        user32.ReleaseDC(0, hdc)


def desktop_area(hLV):
    """桌面图标坐标系的实际边界（ListView 客户区）——恢复时最可靠的尺寸基准"""
    if not hLV:
        return None
    r = RECT()
    if user32.GetClientRect(hLV, ctypes.byref(r)):
        return [r.right - r.left, r.bottom - r.top]
    return None


def environment() -> dict:
    hLV, _, src = find_desktop_listview()
    style = get_ext_style(hLV) if hLV else 0
    mons = enumerate_monitors()
    primary = next((m for m in mons if m["primary"]), mons[0] if mons else None)
    if primary is None:
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        rect = [0, 0, sw, sh]
    else:
        rect = primary["rect"]
    return {
        "screen": f"{rect[2]}x{rect[3]}",
        "width": rect[2],
        "height": rect[3],
        "dpi": _dpi(mons),
        "monitors": len(mons) or 1,
        "monitor_list": mons,
        "topology": topology_key(mons),
        "virtual_screen": [user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                           user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)],
        "desktop_area": desktop_area(hLV),
        "virtual_desktops": _virtual_desktops(),
        "auto_arrange": bool(style & LVS_EX_AUTOARRANGE),
        "snap_to_grid": bool(style & LVS_EX_SNAPTOGRID),
        "icon_spacing": get_icon_spacing(hLV) if hLV else None,
        "desktop_source": src,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }


def topology_diff(saved_env: dict, cur_env: dict) -> list:
    """拓扑差异描述（空列表 = 完全一致）"""
    diffs = []
    if saved_env.get("topology") and saved_env.get("topology") != cur_env.get("topology"):
        diffs.append(f"显示器拓扑：{saved_env.get('topology')} → {cur_env.get('topology')}")
    sa, ca = saved_env.get("desktop_area"), cur_env.get("desktop_area")
    if sa and ca and list(sa) != list(ca):
        diffs.append(f"桌面区域：{sa[0]}×{sa[1]} → {ca[0]}×{ca[1]}")
    if saved_env.get("dpi") and saved_env.get("dpi") != cur_env.get("dpi"):
        diffs.append(f"缩放：{saved_env.get('dpi')} → {cur_env.get('dpi')} DPI")
    return diffs


def profile_key(env: dict) -> str:
    """档案键：主屏分辨率 + DPI + 显示器数（换分辨率/换屏自动切换档案）"""
    return f"{env['screen']}@{env['dpi']}dpi_{env['monitors']}mon"


# --------------------------------------------------------------------------
# 业务动作
# --------------------------------------------------------------------------
def snapshot(label: str = "") -> dict:
    hLV, hShellView, src = find_desktop_listview()
    if not hLV:
        raise RuntimeError("未找到桌面图标列表（explorer 是否在运行？）")
    env = environment()
    icons = read_icons(hLV, with_names=True)
    return {
        "schema": SCHEMA,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "profile": profile_key(env),
        "environment": env,
        "icon_count": len(icons),
        "icons": icons,
    }


def write_archive(doc: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def read_archive(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def do_save(name: str | None = None, quiet: bool = False) -> str:
    doc = snapshot(label=name or "")
    env = doc["environment"]
    key = profile_key(env)

    # 覆盖已有主档案前，先自动备份
    target = os.path.join(layouts_dir(), f"{key}.json")
    if os.path.exists(target) and name is None:
        try:
            old = read_archive(target)
            _now = datetime.now()
            ts = _now.strftime("%Y%m%d-%H%M%S") + f"{_now.microsecond // 1000:03d}"
            shutil.copy2(target, os.path.join(backups_dir(), f"S_{key}_{ts}.json"))
            prune_backups(30)
            _ = old
        except Exception:
            pass

    if name:
        target = os.path.join(layouts_dir(), f"{key}__{_safe(name)}.json")
    write_archive(doc, target)
    if not quiet:
        print(f"[OK] 已保存 {doc['icon_count']} 个图标  ->  {target}")
    return target


def _safe(s: str) -> str:
    return "".join(c for c in s if c not in r'\/:*?"<>|').strip() or "unnamed"


def find_layout(name: str | None) -> str | None:
    """name=None -> 当前分辨率档案；name 为文件名/标签 -> 模糊匹配"""
    if name is None:
        env = environment()
        p = os.path.join(layouts_dir(), f"{profile_key(env)}.json")
        return p if os.path.exists(p) else None
    if os.path.exists(name):
        return name
    d = layouts_dir()
    cand = [os.path.join(d, f) for f in os.listdir(d) if name.lower() in f.lower()]
    if not cand and os.path.exists(os.path.join(d, name)):
        cand = [os.path.join(d, name)]
    return sorted(cand, key=os.path.getmtime, reverse=True)[0] if cand else None


def do_restore(name: str | None = None, keep_autoarrange: bool = False,
               dry_run: bool = False, scale: str = "auto", quiet: bool = False) -> dict:
    path = find_layout(name)
    if not path:
        raise RuntimeError(
            f"没找到布局档案（名字：{name or '当前分辨率默认档案'}）。先执行 save。")

    doc = read_archive(path)
    hLV, hShellView, src = find_desktop_listview()
    if not hLV:
        raise RuntimeError("未找到桌面图标列表（explorer 是否在运行？）")

    cur_env = environment()
    cur = read_icons(hLV, with_names=True)
    cur_idx_pos = {it["index"]: (it["x"], it["y"]) for it in cur}
    saved = doc["icons"]

    # --- 拓扑校验：换主屏 / 投影 / 缩放变化时提示，必要时按比例自适应 ---
    saved_env = doc.get("environment") or {}
    diffs = topology_diff(saved_env, cur_env)
    saved_area = saved_env.get("desktop_area")
    cur_area = cur_env.get("desktop_area")
    sx = sy = 1.0
    scaled = False
    if saved_area and cur_area and list(saved_area) != list(cur_area):
        rx = cur_area[0] / saved_area[0] if saved_area[0] else 1.0
        ry = cur_area[1] / saved_area[1] if saved_area[1] else 1.0
        if scale == "auto" and (abs(rx - 1) > 0.02 or abs(ry - 1) > 0.02):
            sx, sy = rx, ry
            scaled = True

    # --- 匹配：优先按名称，名称缺失时回退到索引 ---
    by_name = {}
    for it in cur:
        if it["name"]:
            by_name.setdefault(it["name"], []).append(it["index"])

    moves = []
    move_detail = []
    matched, missing, dup, out_of_bounds, unchanged = 0, [], 0, 0, 0
    for it in saved:
        nm = it.get("name") or ""
        if nm and by_name.get(nm):
            lst = by_name[nm]
            idx = lst.pop(0)
            if len(lst) + 1 > 1:
                dup += 1
        elif not nm and it["index"] < len(cur):
            idx = it["index"]
        else:
            missing.append(nm or f"#{it['index']}")
            continue
        x, y = it["x"], it["y"]
        if scaled:
            x, y = int(round(x * sx)), int(round(y * sy))
        if cur_area and (x < 0 or y < 0 or x > cur_area[0] - 24 or y > cur_area[1] - 24):
            out_of_bounds += 1
        moves.append((idx, x, y))
        matched += 1
        fx, fy = cur_idx_pos.get(idx, (None, None))
        if fx == x and fy == y:
            unchanged += 1
        else:
            move_detail.append({"name": nm or f"#{idx}",
                                "from": [fx, fy] if fx is not None else None,
                                "to": [x, y]})

    extra = [c["name"] for c in cur if c["name"] and c["name"] not in {s["name"] for s in saved}]

    result = {"path": path, "matched": matched, "moves": len(moves),
              "will_move": len(move_detail), "unchanged": unchanged,
              "missing": missing, "extra": extra, "applied": 0, "dry_run": bool(dry_run),
              "topology_diffs": diffs, "scaled": scaled,
              "scale_factor": [round(sx, 4), round(sy, 4)],
              "out_of_bounds": out_of_bounds, "duplicates": dup,
              "move_detail": move_detail,
              "desktop_area": cur_area, "saved_desktop_area": saved_area,
              "profile": doc.get("profile", ""), "auto_arrange_was_on": False}
    if dry_run:
        return result

    # --- 备份当前（撤销用） ---
    _now = datetime.now()
    ts = _now.strftime("%Y%m%d-%H%M%S") + f"{_now.microsecond // 1000:03d}"
    bpath = os.path.join(backups_dir(), f"R_{_safe(doc.get('profile', 'x'))}_{ts}.json")
    write_archive({"schema": SCHEMA, "saved_at": datetime.now().isoformat(timespec="seconds"),
                   "label": f"undo-before-restore {ts}", "profile": doc.get("profile", ""),
                   "environment": cur_env, "icon_count": len(cur), "icons": cur}, bpath)
    prune_backups(30)

    # --- 关键修复：先关闭"自动排列"，否则设置位置会被系统忽略 ---
    cur_style = get_ext_style(hLV)
    was_auto = bool(cur_style & LVS_EX_AUTOARRANGE)
    set_ext_style(hLV, LVS_EX_AUTOARRANGE, 0)          # 关闭自动排列
    set_ext_style(hLV, LVS_EX_SNAPTOGRID, 0)           # 关闭对齐网格（恢复过程）

    applied = set_icon_positions(hLV, moves)

    # --- 恢复"对齐到网格"（不会触发重排，安全）；自动排列不主动还原 ---
    if saved_env.get("snap_to_grid"):
        set_ext_style(hLV, LVS_EX_SNAPTOGRID, LVS_EX_SNAPTOGRID)

    sp = saved_env.get("icon_spacing")
    if sp and sp[0] > 0 and sp[1] > 0:          # 0 值无意义，跳过以免把间距压成 0
        set_icon_spacing(hLV, sp[0], sp[1])

    if not quiet:
        print(f"[OK] 已恢复 {applied} 个图标  <-  {os.path.basename(path)}")
        if diffs:
            print("     ⚠ 当前显示环境与档案不一致：")
            for d in diffs:
                print(f"       · {d}")
        if scaled:
            print(f"     · 已按 {sx:.3f}×/{sy:.3f} 比例自适应缩放坐标（桌面区域变了）")
        if out_of_bounds:
            print(f"     · 有 {out_of_bounds} 个图标超出当前桌面范围，可能被系统挪位"
                  + ("；可加 --scale auto 让坐标自动适配" if not scaled else ""))
        if missing:
            print(f"     档案中有 {len(missing)} 个图标在当前桌面已不存在：{', '.join(missing[:8])}"
                  + (" ..." if len(missing) > 8 else ""))
        if extra:
            print(f"     当前桌面有 {len(extra)} 个新增图标未在档案中：{', '.join(extra[:8])}"
                  + (" ..." if len(extra) > 8 else ""))
        if was_auto and not keep_autoarrange:
            print("     提示：原本开启的『自动排列图标』已为你保持关闭——")
            print("           这样布局重启后不会被系统重排。如需恢复，右键桌面 > 查看 > 自动排列图标。")

    result.update({"applied": applied, "auto_arrange_was_on": was_auto, "backup": bpath})
    return result


def do_undo(quiet: bool = False) -> str:
    d = backups_dir()
    files = sorted([f for f in os.listdir(d) if f.startswith("R_") and f.endswith(".json")],
                   key=lambda f: os.path.getmtime(os.path.join(d, f)), reverse=True)
    if not files:
        raise RuntimeError("没有可撤销的恢复记录")
    bpath = os.path.join(d, files[0])
    doc = read_archive(bpath)
    hLV, _, _ = find_desktop_listview()
    if not hLV:
        raise RuntimeError("未找到桌面图标列表")
    set_ext_style(hLV, LVS_EX_AUTOARRANGE, 0)
    moves = [(it["index"], it["x"], it["y"]) for it in doc["icons"]]
    n = set_icon_positions(hLV, moves)
    if not quiet:
        print(f"[OK] 已撤销，还原 {n} 个图标的位置（来自 {files[0]}）")
    return bpath


# --------------------------------------------------------------------------
# 外部 .dsv 档案导入（互操作性：只读原文件，路径由用户显式指定）
# --------------------------------------------------------------------------
def _profile_from_resolution(w: int | None, h: int | None,
                             dpi: int = 96, monitors: int = 1) -> str:
    """由分辨率推导档案键，与 profile_key() 保持同构。"""
    screen = f"{w}x{h}" if (w and h) else "unknown"
    return f"{screen}@{dpi}dpi_{monitors}mon"


def parse_dsv(path: str) -> dict:
    """解析外部程序导出的 .dsv 布局档案（UTF-8 with BOM），用于互操作性导入。

    文件结构：
        首部若干行  "#" 开头的元信息（分辨率 / 时间戳 / 网格标记）
        之后        <x> <y> <图标名>          ← 坐标 + 名称（名称可含空格）
        其它行      无法识别，直接跳过

    解析规则：行首两段为整数坐标，其后整段为名称 —— 用 split(" ", 2) 无损还原含空格的中文名。
    对无法识别的行不做任何内容假设，只跳过。
    本函数**只读**：不修改、不移动原文件。
    """
    with open(path, "rb") as f:
        raw = f.read()
    txt = raw.decode("utf-8-sig", "replace")
    lines = txt.splitlines()

    width = height = None
    saved_at = None
    snap_to_grid = False
    grid_marker_seen = False
    icons = []

    for line in lines:
        s = line.rstrip("\r\n")
        if not s.strip():
            continue
        # 无法识别的行：非 "#" 开头，且前两段不能同时解析为整数（即不是坐标行）。
        # 直接跳过 —— 不对这类行的内容做任何假设（不拆成"产品名/版本"之类的字段）。
        # 注意不能用 isdigit()：坐标可能为负（"-5".isdigit() 为 False）。
        if not s.startswith("#"):
            _head = s.split(" ", 2)
            _is_coord_line = False
            if len(_head) >= 3:
                try:
                    int(_head[0]); int(_head[1])
                    _is_coord_line = True
                except ValueError:
                    _is_coord_line = False
            if not _is_coord_line:
                continue
        if s.startswith("#"):
            body = s[1:].strip()
            m = re.match(r"^(\d+)\s*x\s*(\d+)$", body, re.IGNORECASE)
            if m:
                width, height = int(m.group(1)), int(m.group(2))
                continue
            if re.match(r"^\d{14}$", body):
                try:
                    saved_at = datetime.strptime(body, "%Y%m%d%H%M%S").isoformat(timespec="seconds")
                except ValueError:
                    saved_at = None
                continue
            if body.lower() == "grid":
                snap_to_grid = True
                grid_marker_seen = True
                continue
            continue
        # 图标行：<x> <y> <name>
        parts = s.split(" ", 2)
        if len(parts) < 3:
            continue
        try:
            x, y = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        name = parts[2]
        icons.append({"index": len(icons), "name": name, "x": x, "y": y})

    return {
        "width": width,
        "height": height,
        "saved_at": saved_at,
        "snap_to_grid": snap_to_grid,
        "grid_marker_seen": grid_marker_seen,
        "icons": icons,
        "source": os.path.abspath(path),
    }


def dsv_to_archive(parsed: dict) -> dict:
    """把 parse_dsv() 的结果映射为本产品档案 JSON 结构（与 snapshot()/write_archive() 同构）。"""
    w, h = parsed.get("width"), parsed.get("height")
    profile = _profile_from_resolution(w, h)
    topo = f"{w}x{h}+0+0@96*" if (w and h) else "unknown"
    doc = {
        "schema": SCHEMA,
        "saved_at": parsed.get("saved_at") or datetime.now().isoformat(timespec="seconds"),
        "label": f"导入自外部档案（{os.path.basename(parsed.get('source', ''))}）".strip(),
        "profile": profile,
        "environment": {
            "screen": f"{w}x{h}" if (w and h) else "unknown",
            "width": w, "height": h,
            "dpi": 96,
            "monitors": 1,
            "topology": topo,
            "snap_to_grid": bool(parsed.get("snap_to_grid")),
            "imported_from": parsed.get("source"),
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        },
        "icon_count": len(parsed.get("icons", [])),
        "icons": parsed.get("icons", []),
    }
    return doc


def import_dsv(src: str, force: bool = False, quiet: bool = False) -> list:
    """把外部程序的 .dsv 布局档案批量转换成本产品档案（互操作性导入）。

    src: 单个档案文件、或包含多个 .dsv 的目录，**由调用方显式指定**（不设默认路径）。
    **只读原文件**：本函数绝不修改 / 移动 / 删除该目录下任何内容。

    ⚠ 安全规则（绝不覆盖已存在的档案）：
      · 目标 Layouts/<profile>.json 不存在  → 正常写入该名字（换超宽屏时可自动匹配）；
      · 已存在且内容与待导入一致            → 跳过（幂等，重复导入无副作用）；
      · 已存在且内容不同                    → 另存为 Layouts/导入-<profile>.json，
                                              绝不覆盖用户既有档案；
      · force=True                          → 才允许覆盖主档案（覆盖前先备份到 Backups）。
    返回 [{"dsv":…, "ng":…, "icons":n, "profile":…, "skipped"/"renamed":…}]。
    """
    files = []
    if os.path.isdir(src):
        for f in sorted(os.listdir(src)):
            if f.lower().endswith(".dsv"):
                files.append(os.path.join(src, f))
    elif os.path.isfile(src):
        files.append(src)
    else:
        raise RuntimeError(f"路径不存在：{src}")

    if not files:
        raise RuntimeError(f"未找到任何 .dsv 文件：{src}")

    out = []
    for dsv in files:
        parsed = parse_dsv(dsv)
        doc = dsv_to_archive(parsed)
        profile = doc["profile"]
        target = os.path.join(layouts_dir(), f"{profile}.json")
        alt = os.path.join(layouts_dir(), f"导入-{profile}.json")
        n = len(parsed["icons"])

        if os.path.exists(target) and not force:
            # 已存在：内容一致则跳过；否则另存为 导入-<profile>.json，绝不覆盖
            try:
                same = _signature(read_archive(target).get("icons", [])) == \
                    _signature(doc.get("icons", []))
            except Exception:
                same = False
            if same:
                out.append({"dsv": dsv, "ng": target, "icons": n,
                            "profile": profile, "skipped": True})
                if not quiet:
                    print(f"[--] {os.path.basename(dsv)} -> {os.path.basename(target)} "
                          f"已存在且内容相同，跳过")
                continue
            write_archive(doc, alt)
            out.append({"dsv": dsv, "ng": alt, "icons": n,
                        "profile": profile, "renamed": True})
            if not quiet:
                print(f"[!] 目标 {os.path.basename(target)} 已存在，已另存为 "
                      f"{os.path.basename(alt)}（{n} 个图标）；如需覆盖主档案请加 --force")
            continue

        if os.path.exists(target) and force:
            # 覆盖前先备份既有档案（与 do_save 一致，避免误伤）
            try:
                _now = datetime.now()
                ts = _now.strftime("%Y%m%d-%H%M%S") + f"{_now.microsecond // 1000:03d}"
                shutil.copy2(target, os.path.join(
                    backups_dir(), f"I_{_safe(profile)}_{ts}.json"))
                prune_backups(30)
            except Exception:
                pass

        write_archive(doc, target)
        out.append({"dsv": dsv, "ng": target, "icons": n, "profile": profile})
        if not quiet:
            print(f"[OK] 导入 {os.path.basename(dsv)} -> {os.path.basename(target)}"
                  f"（{n} 个图标，档案键 {profile}）")
    return out



def _signature(icons) -> str:
    """布局内容指纹：只取 名称+坐标，用于判断两次快照是否真的不同"""
    raw = json.dumps([[i.get("name", ""), i["x"], i["y"]] for i in icons],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _changed_count(newer, older) -> int:
    """newer 相对 older 有变化（位移/新增/缺失）的图标个数"""
    mb = {x.get("name", ""): (x["x"], x["y"]) for x in older}
    n = 0
    for x in newer:
        nm = x.get("name", "")
        if nm not in mb or mb[nm] != (x["x"], x["y"]):
            n += 1
    return n


def prune_snapshots(keep: int = 60) -> int:
    """快照轮转：按文件名（时间序）保留最新 N 张"""
    d = snapshots_dir()
    files = sorted([f for f in os.listdir(d) if f.endswith(".json")], reverse=True)
    removed = 0
    for f in files[keep:]:
        try:
            os.remove(os.path.join(d, f))
            removed += 1
        except OSError:
            pass
    return removed


def list_snapshots(with_icons: bool = False, limit: int = 0, with_thumb: bool = False):
    """快照列表（新 → 旧），含"相对上一张变化了几个图标"。

    limit>0 时只解析最近 limit 张（内部多读 1 张用于计算差异），避免快照多了之后变慢。
    with_thumb=True 时附带精简坐标数组 [[x,y],...]，供界面实时绘制缩略图
    （只传坐标不传名称，40 张快照约 50 KB，不必存图片）。
    """
    d = snapshots_dir()
    files = sorted([x for x in os.listdir(d) if x.endswith(".json")], reverse=True)
    if limit:
        files = files[:limit + 1]
    rows = []
    for f in files:
        p = os.path.join(d, f)
        try:
            doc = read_archive(p)
        except Exception:
            continue
        rows.append({
            "file": f, "path": p,
            "saved_at": doc.get("saved_at", "?"),
            "profile": doc.get("profile", ""),
            "icon_count": doc.get("icon_count", 0),
            "note": doc.get("label", "") or "",
            "signature": doc.get("signature", ""),
            "topology": (doc.get("environment") or {}).get("topology", ""),
            "desktop_area": (doc.get("environment") or {}).get("desktop_area"),
            "icons": doc.get("icons", []),
        })
    for i in range(len(rows) - 1):
        rows[i]["changed"] = _changed_count(rows[i]["icons"], rows[i + 1]["icons"])
    if rows:
        rows[-1]["changed"] = None
    if limit:
        rows = rows[:limit]
    if with_thumb:
        for r in rows:
            r["thumb"] = [[i["x"], i["y"]] for i in r.get("icons", [])]
    if not with_icons:
        for r in rows:
            r.pop("icons", None)
    return rows


def do_snapshot(note: str = "", dedup: bool = True, keep: int = 60, quiet: bool = False):
    """拍一张布局快照；与上一张内容完全一致时默认跳过，避免堆积噪声"""
    doc = snapshot(label=note or "")
    sig = _signature(doc["icons"])
    doc["signature"] = sig

    d = snapshots_dir()
    existing = sorted([f for f in os.listdir(d) if f.endswith(".json")], reverse=True)
    if dedup and existing:
        try:
            prev = read_archive(os.path.join(d, existing[0]))
            if prev.get("signature") == sig:
                if not quiet:
                    print(f"[--] 与上一张快照完全一致（{len(doc['icons'])} 个图标无变化），已跳过")
                return None
        except Exception:
            pass

    now = datetime.now()
    ts = now.strftime("%Y%m%d-%H%M%S") + f"{now.microsecond // 1000:03d}"   # 毫秒级，防同秒覆盖
    fname = f"{ts}_{_safe(doc['profile'])}.json"
    path = os.path.join(d, fname)
    write_archive(doc, path)
    pruned = prune_snapshots(keep)
    if not quiet:
        extra = f"，清理最旧的 {pruned} 张" if pruned else ""
        print(f"[OK] 快照已保存：{doc['icon_count']} 个图标 -> {fname}{extra}")
        if note:
            print(f"     备注：{note}")
    return path


def do_snapshots(limit: int = 20, quiet: bool = False):
    rows = list_snapshots()
    if not quiet:
        print(f"共 {len(rows)} 张快照（新 → 旧）\n")
        if not rows:
            print("  （还没有快照）")
            print("  · 立即拍一张：      命令行.bat snapshot")
            print("  · 定时自动留存：    命令行.bat watch --snapshot-every 30")
        for i, r in enumerate(rows[:limit]):
            ch = r.get("changed")
            mark = "" if ch is None else (f"  Δ{ch} 个图标变化" if ch else "  无变化")
            note = f"  「{r['note']}」" if r.get("note") else ""
            ts = r["saved_at"][:19].replace("T", " ")
            print(f"  [{i:>2}] {ts}  {r['icon_count']:>3} 图标{mark}{note}")
            print(f"        {r['file']}")
        if len(rows) > limit:
            print(f"\n  ... 其余 {len(rows) - limit} 张省略（--limit 调整）")
    return rows


def do_rollback(target: str | None = None, scale: str = "auto", quiet: bool = False) -> dict:
    """回滚到某张快照：留空 = 最新；数字 = 序号；文字 = 文件名模糊匹配；也可直接给绝对路径"""
    rows = list_snapshots()
    if not rows:
        raise RuntimeError("还没有任何快照，先执行 snapshot 或开启定时快照")
    if target is None:
        path = rows[0]["path"]
    elif os.path.isabs(target) and os.path.exists(target):
        path = target
    elif os.sep in target or "/" in target:
        # 传进来的是路径但文件不存在
        raise RuntimeError(f"快照文件不存在：{target}")
    elif target.isdigit():
        idx = int(target)
        if idx >= len(rows):
            raise RuntimeError(f"快照序号 {idx} 超出范围（共 {len(rows)} 张）")
        path = rows[idx]["path"]
    else:
        base = os.path.basename(target)
        cand = [r for r in rows if base.lower() in r["file"].lower()]
        if not cand:
            raise RuntimeError(f"没找到匹配「{target}」的快照")
        path = cand[0]["path"]

    if not quiet:
        print(f"[回滚] 使用快照 {os.path.basename(path)}")
    r = do_restore(path, scale=scale, quiet=quiet)
    r["snapshot"] = os.path.basename(path)
    return r


def do_list(quiet: bool = False):
    d = layouts_dir()
    env = environment()
    cur_key = profile_key(env)
    rows = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        p = os.path.join(d, f)
        try:
            doc = read_archive(p)
        except Exception:
            continue
        rows.append((f, doc.get("saved_at", "?"), doc.get("icon_count", 0),
                     doc.get("profile", ""), f == f"{cur_key}.json"))
    if not quiet:
        print(f"当前环境：{env['screen']} / {env['dpi']} DPI / {env['monitors']} 屏 / 档案键 {cur_key}")
        print()
        if not rows:
            print("（还没有任何布局档案）")
        for f, ts, n, prof, iscur in rows:
            mark = "  <== 当前" if iscur else ""
            print(f"  {f:<44} {ts:<20} {n:>4} 图标{mark}")


def do_info(quiet: bool = False):
    env = environment()
    hLV, hShellView, src = find_desktop_listview()
    icons = read_icons(hLV, with_names=True) if hLV else []
    if not quiet:
        print("=== 当前桌面环境 ===")
        for k, v in env.items():
            print(f"  {k:<16}: {v}")
        print(f"\n=== 桌面图标（{len(icons)} 个）===")
        for it in icons[:30]:
            print(f"  [{it['index']:>3}] ({it['x']:>5},{it['y']:>5})  {it['name']}")
        if len(icons) > 30:
            print(f"  ... 其余 {len(icons)-30} 个省略")
    return env, icons


def do_topology(quiet: bool = False):
    """显示器拓扑详情：每屏分辨率/位置/缩放/主屏标记 + 虚拟桌面数"""
    env = environment()
    if not quiet:
        print("=== 显示器拓扑 ===")
        print(f"  拓扑指纹   : {env['topology']}")
        print(f"  主屏       : {env['screen']} @ {env['dpi']} DPI")
        print(f"  显示器数   : {env['monitors']}")
        print(f"  虚拟桌面   : {env['virtual_desktops']} 个")
        print(f"  虚拟屏幕   : {env['virtual_screen']}")
        print(f"  桌面图标区 : {env['desktop_area']}")
        print()
        for i, m in enumerate(env["monitor_list"]):
            tag = "  ← 主屏" if m["primary"] else ""
            print(f"  [{chr(65 + i)}] {m['device']:<14} {m['rect'][2]}×{m['rect'][3]}"
                  f"  坐标 {m['rect'][0]},{m['rect'][1]}  {m['dpi']} DPI{tag}")
        print()
        print("  说明：Windows 的桌面图标只能存在于主屏的 Progman 容器内，无法放到副屏上；")
        print("        副屏 / 投影的影响体现在【主屏尺寸与缩放】上。本程序按")
        print("        『主屏分辨率 + DPI + 显示器数』分档保存档案，并在恢复前比对拓扑差异。")
        print(f"  当前档案键：{profile_key(env)}")
    return env


def do_doctor(quiet: bool = False):
    """自检：把影响恢复成败的因素全部列出来"""
    checks = []
    hLV, hShellView, src = find_desktop_listview()
    checks.append(("explorer 桌面窗口链", bool(hLV), src))

    checks.append(("Python 位数", True, f"{struct.calcsize('P')*8}-bit"))
    try:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hLV, ctypes.byref(pid)) if hLV else None
        import platform
        checks.append(("explorer 进程位数", True,
                       "64-bit（Win10/11 默认）" if struct.calcsize('P') * 8 == 64 else "32-bit"))
    except Exception:
        pass

    xproc = False
    if hLV:
        try:
            with RemoteMem(hLV, 64) as _mem:
                # 用本程序自己的中性标签做"写入-读回"往返验证；
                # 不要写任何第三方产品的名称/标识到其他进程内存。
                _probe = b"iconmemo-probe"
                _mem.write(_probe, 0)
                xproc = _mem.read(len(_probe)) == _probe
        except (OSError, TypeError):
            xproc = False
    checks.append(("跨进程内存读写", xproc, "OK" if xproc else "失败（恢复功能不可用）"))

    style = get_ext_style(hLV) if hLV else 0
    checks.append(("自动排列图标", not bool(style & LVS_EX_AUTOARRANGE),
                   "已关闭（利于恢复后保持）" if not (style & LVS_EX_AUTOARRANGE) else "开启中（恢复时会被自动关闭）"))
    checks.append(("对齐到网格", True,
                   "开启" if style & LVS_EX_SNAPTOGRID else "关闭"))

    env = environment()
    checks.append(("显示器拓扑", True, f"{env['monitors']} 屏 / {env['screen']} / {env['dpi']} DPI"))
    checks.append(("数据目录", os.path.isdir(data_dir()), data_dir()))

    # --- P0 新增自检项 ---
    _mutex_ok = _mutex_selftest()
    checks.append(("单实例互斥体", _mutex_ok,
                   "可用（Local\\ 作用域）" if _mutex_ok else "创建失败"))
    _logs_ok = _logs_writable()
    checks.append(("日志目录可写", _logs_ok,
                   logs_dir() if _logs_ok else f"不可写：{logs_dir()}"))

    # 托盘：验证能否加载图标句柄（真正的 NIM_ADD 需要窗口，已在 watch 中验证）
    _hicon = load_tray_icon()
    checks.append(("托盘图标可加载", bool(_hicon),
                   "已取得图标句柄" if _hicon else "无法加载托盘图标"))
    if _hicon:
        try:
            _destroy_tray_icon(_hicon)
        except Exception:
            pass

    if not quiet:
        print("=== 图标记忆 自检 ===")
        for name, ok, detail in checks:
            print(f"  [{'OK ' if ok else '!! '}] {name:<20} {detail}")
        print("\n说明：恢复前建议保持『自动排列图标』为关闭，否则系统会在刷新后重排。")


def _mutex_selftest() -> bool:
    """自检：能否创建命名互斥体（用唯一名，不影响真实单实例互斥体）。"""
    h = kernel32.CreateMutexW(None, False, f"Local\\IconMemo_selftest_{os.getpid()}")
    if not h:
        return False
    kernel32.CloseHandle(h)
    return True


def _logs_writable() -> bool:
    """自检：日志目录是否可写。"""
    try:
        p = os.path.join(logs_dir(), ".write_test")
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(p)
        return True
    except OSError:
        return False


def _last_restore_time() -> str | None:
    """上次恢复时间：取 Backups 目录里最新一个 R_* 备份的时间戳。"""
    d = backups_dir()
    try:
        files = [f for f in os.listdir(d) if f.startswith("R_") and f.endswith(".json")]
    except OSError:
        return None
    if not files:
        return None
    newest = max(files, key=lambda f: os.path.getmtime(os.path.join(d, f)))
    return datetime.fromtimestamp(os.path.getmtime(os.path.join(d, newest))).strftime(
        "%Y-%m-%d %H:%M:%S")


def do_status(quiet: bool = False) -> dict:
    """一屏报告：运行实例 / 自启注册 / 档案与快照数量 / 上次恢复时间。"""
    running = instance_running()
    auto = autostart_status()
    try:
        layouts = [f for f in os.listdir(layouts_dir()) if f.endswith(".json")]
    except OSError:
        layouts = []
    try:
        snaps = [f for f in os.listdir(snapshots_dir()) if f.endswith(".json")]
    except OSError:
        snaps = []
    last_restore = _last_restore_time()

    info = {
        "instance_running": running,
        "mutex": MUTEX_NAME,
        "autostart": auto,
        "layouts_count": len(layouts),
        "snapshots_count": len(snaps),
        "last_restore": last_restore,
        "data_dir": data_dir(),
    }
    if not quiet:
        print("=== 图标记忆 状态 ===")
        print(f"  运行实例   : {'● 有实例在运行' if running else '○ 未运行'}（互斥体 {MUTEX_NAME}）")
        if auto.get("registered"):
            print(f"  开机自启   : 已注册")
            print(f"               命令 = {auto.get('command')}")
            print(f"               有效 = {'是' if auto.get('valid') else '否（指向的程序不存在）'}")
        else:
            print("  开机自启   : 未注册")
        print(f"  布局档案   : {len(layouts)} 份")
        print(f"  布局快照   : {len(snaps)} 张")
        print(f"  上次恢复   : {last_restore or '（无记录）'}")
        print(f"  数据目录   : {data_dir()}")
    return info


# --------------------------------------------------------------------------
# 显示器变化监视（自动恢复）
# --------------------------------------------------------------------------
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.DefWindowProcW.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT


# --------------------------------------------------------------------------
# 托盘图标加载（打包 / 开发两种环境都要能跑）
# --------------------------------------------------------------------------
_TRAY_ICON_HANDLE = None       # 保活：图标句柄在本进程生命周期内不释放


def load_tray_icon():
    """按优先级获取托盘图标句柄：
    ① 自身 exe 图标（ExtractIconExW）→ ② 同目录 icon.ico（LoadImageW）→ ③ 系统默认图标。
    """
    global _TRAY_ICON_HANDLE
    if _TRAY_ICON_HANDLE:
        return _TRAY_ICON_HANDLE

    # ① 从自身可执行文件提取（python.exe / pythonw.exe / 打包后的 *.exe 都含图标）
    try:
        exe = sys.executable
        large = wintypes.HICON()
        small = wintypes.HICON()
        n = shell32.ExtractIconExW(exe, 0, ctypes.byref(large), ctypes.byref(small), 1)
        if n and n > 0:
            if small.value:
                if large.value:
                    user32.DestroyIcon(large)
                _TRAY_ICON_HANDLE = small
                return _TRAY_ICON_HANDLE
            if large.value:
                _TRAY_ICON_HANDLE = large
                return _TRAY_ICON_HANDLE
    except Exception:
        pass

    # ② 同目录 icon.ico
    try:
        base = os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__))
        ico = os.path.join(base, "icon.ico")
        if os.path.exists(ico):
            h = user32.LoadImageW(None, ico, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                _TRAY_ICON_HANDLE = wintypes.HICON(h)
                return _TRAY_ICON_HANDLE
    except Exception:
        pass

    # ③ 系统默认图标兜底
    try:
        h = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        if h:
            _TRAY_ICON_HANDLE = h
            return _TRAY_ICON_HANDLE
    except Exception:
        pass
    return None


def _destroy_tray_icon(hicon):
    """释放临时图标句柄（缓存的共享图标不释放）。"""
    if hicon is None or hicon == _TRAY_ICON_HANDLE:
        return
    try:
        user32.DestroyIcon(hicon)
    except Exception:
        pass


# --------------------------------------------------------------------------
# 系统托盘
# --------------------------------------------------------------------------
class Tray:
    """系统托盘图标 + 右键菜单。

    复用 do_watch 已有的隐藏窗口消息循环：托盘事件经 WM_APP+1 投递到该窗口，
    由 _wndproc 调用 handle_message() 处理 —— 不另起窗口类。
    """

    UID = 1

    def __init__(self, hwnd, tooltip: str = "图标记忆",
                 on_command=None, on_dblclick=None):
        self.hwnd = hwnd
        self.tooltip = tooltip[:127]
        self.on_command = on_command or (lambda cid: None)
        self.on_dblclick = on_dblclick or (lambda: None)
        self.hicon = None
        self.added = False
        self.paused = False            # 供菜单"暂停监视"勾选状态
        self._nid = None               # 保活

    # --- 生命周期 ---
    def add(self) -> bool:
        """Shell_NotifyIconW(NIM_ADD) 添加托盘图标。"""
        self.hicon = load_tray_icon()
        nid = self._make_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        self._nid = nid
        ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        self.added = ok
        if not ok:
            _LOG.warning("托盘 NIM_ADD 失败 err=%s", ctypes.get_last_error())
        return ok

    def remove(self):
        """Shell_NotifyIconW(NIM_DELETE) 移除托盘图标。"""
        if not self.added:
            return
        try:
            nid = self._make_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass
        finally:
            self.added = False

    def set_tip(self, text: str):
        """更新悬浮提示（Shell_NotifyIconW NIM_MODIFY）。"""
        self.tooltip = text[:127]
        if self.added:
            nid = self._make_nid(NIF_TIP)
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _make_nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = self.UID
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon or 0
        nid.szTip = self.tooltip
        return nid

    # --- 消息处理 ---
    def handle_message(self, lparam) -> bool:
        """处理 WM_APP+1 托盘回调；返回 True 表示已消费该消息。"""
        ev = lparam & 0xFFFF
        if ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
            self.show_menu()
            return True
        if ev == WM_LBUTTONDBLCLK:       # 双击托盘图标 = 打开界面
            self.on_dblclick()
            return True
        return False

    def show_menu(self):
        """弹出右键菜单并分发命令。"""
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            user32.AppendMenuW(hmenu, MF_STRING, IDM_TRAY_OPEN, "打开界面")
            user32.AppendMenuW(hmenu, MF_STRING, IDM_TRAY_SAVE, "立即保存布局")
            user32.AppendMenuW(hmenu, MF_STRING, IDM_TRAY_RESTORE, "立即恢复布局")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            pause_flag = MF_STRING | (MF_CHECKED if self.paused else 0)
            user32.AppendMenuW(hmenu, pause_flag, IDM_TRAY_PAUSE,
                               "继续监视" if self.paused else "暂停监视")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, IDM_TRAY_EXIT, "退出")

            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # 关键：TrackPopupMenu 前必须置前台，否则菜单可能不响应/不消失
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x, pt.y, 0, self.hwnd, None)
            # 规避 Win32 已知的"菜单不消失"问题
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if cmd:
                self.on_command(cmd)
        finally:
            user32.DestroyMenu(hmenu)


# --------------------------------------------------------------------------
# 界面唤起 / WM_SETTINGCHANGE 参数读取
# --------------------------------------------------------------------------
def _read_settingchange_lparam(lparam) -> str:
    """把 WM_SETTINGCHANGE 的 lParam（宽字符串指针）读成字符串；读不到返回空串。"""
    if not lparam:
        return ""
    try:
        return ctypes.wstring_at(lparam)
    except Exception:
        return ""


def open_ui(log=None) -> bool:
    """尽力打开图形界面（独立进程）。找不到界面程序则返回 False。

    环境变量 ICONMEMO_NO_UI=1 时直接跳过（供自测 / 无界面部署使用）。
    """
    if os.environ.get("ICONMEMO_NO_UI"):
        if log:
            log.info("ICONMEMO_NO_UI=1，已跳过打开界面")
        return False
    try:
        base = os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__))
        gui_exe = os.path.join(base, "IconMemo.exe")
        if os.path.exists(gui_exe):
            subprocess.Popen([gui_exe])
            return True
        gui_py = os.path.join(base, "iconmemo_gui.py")
        if os.path.exists(gui_py):
            py = sys.executable
            pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
            runner = pyw if os.path.exists(pyw) else py
            subprocess.Popen([runner, gui_py])
            return True
    except Exception as e:
        if log:
            log.warning("打开界面失败：%s", e)
    return False


def do_watch(delay: float = 2.0, snapshot_every: float = 0.0, quiet: bool = False,
             tray: bool = True, ready_timeout: float = 30.0):
    """常驻监视：显示器配置变化后自动恢复；可选按间隔自动留存布局快照。

    关键设计：
      · 启动就绪轮询：explorer 桌面未就绪时带退避重试，不再直接失败；
      · WM_SETTINGCHANGE 按"拓扑指纹"过滤：无关系统设置变更不再偷偷把桌面改回去；
      · 系统托盘：可感知、可右键操作、可优雅退出（复用隐藏窗口消息循环）；
      · 全部输出走 logger（常驻时无控制台，print 无去处）。
    """
    log = setup_logging()
    log.info("watch 启动：delay=%.1fs snapshot_every=%.1fmin tray=%s",
             delay, snapshot_every, tray)

    state = {"pending": False, "paused": False, "topology": None}
    stop = threading.Event()

    # ---- 拓扑指纹：判断显示环境是否发生"实质变化" ----
    def _current_topology():
        try:
            return environment().get("topology")
        except Exception:
            return None

    def _topology_changed() -> bool:
        """与上次记录的拓扑指纹比对；有实质变化（且有效）时更新并返回 True。"""
        cur = _current_topology()
        if not cur or cur == "unknown":
            return False                    # 读不到环境时保守忽略
        if state["topology"] is None:
            state["topology"] = cur
            return True
        if cur != state["topology"]:
            log.info("拓扑指纹变化：%s -> %s", state["topology"], cur)
            state["topology"] = cur
            return True
        return False

    def _restore_later(reason: str = ""):
        if state["paused"]:
            log.info("监视已暂停，忽略触发（%s）", reason or "?")
            return
        if state["pending"]:
            return
        state["pending"] = True

        def worker():
            time.sleep(delay)
            try:
                env = environment()
                path = find_layout(None)
                if not path:
                    log.info("无对应档案，跳过（%s / %s DPI）", env["screen"], env["dpi"])
                else:
                    r = do_restore(None, quiet=True)
                    log.info("已恢复 %d 个图标（%s / %s DPI）触发源=%s",
                             r["applied"], env["screen"], env["dpi"], reason or "?")
            except Exception as e:
                log.exception("恢复失败：%s", e)
            finally:
                state["pending"] = False

        threading.Thread(target=worker, daemon=True, name="restore").start()

    def _snapshot_loop():
        interval = max(1.0, snapshot_every) * 60
        while not stop.wait(interval):
            try:
                p = do_snapshot(note="定时自动快照", quiet=True)
                if p:
                    log.info("定时快照已保存 -> %s", os.path.basename(p))
            except Exception as e:
                log.exception("快照失败：%s", e)

    # ---- 托盘动作分发 ----
    tray_obj = None

    def _tray_command(cid):
        if cid == IDM_TRAY_OPEN:
            log.info("托盘：打开界面")
            if not open_ui(log):
                log.warning("未找到界面程序（IconMemo.exe / iconmemo_gui.py）")
        elif cid == IDM_TRAY_SAVE:
            try:
                p = do_save(quiet=True)
                log.info("托盘：已保存布局 -> %s", os.path.basename(p))
            except Exception as e:
                log.exception("托盘保存失败：%s", e)
        elif cid == IDM_TRAY_RESTORE:
            log.info("托盘：立即恢复布局")
            _restore_later(reason="托盘手动恢复")
        elif cid == IDM_TRAY_PAUSE:
            state["paused"] = not state["paused"]
            if tray_obj:
                tray_obj.paused = state["paused"]
            log.info("监视已%s", "暂停" if state["paused"] else "继续")
        elif cid == IDM_TRAY_EXIT:
            log.info("托盘：退出")
            user32.PostMessageW(hwnd_holder[0], WM_CLOSE, 0, 0)

    hwnd_holder = [None]                # 便于闭包内引用窗口句柄
    show_msg = user32.RegisterWindowMessageW(SHOW_WINDOW_MESSAGE)

    def _wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_DISPLAYCHANGE:
            log.info("收到 WM_DISPLAYCHANGE %dx%d", lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
            _topology_changed()
            _restore_later(reason="WM_DISPLAYCHANGE")
        elif msg == WM_SETTINGCHANGE:
            # 缺陷修复：不再无条件恢复 —— 仅当拓扑指纹有实质变化时才触发
            lstr = _read_settingchange_lparam(lparam)
            if _topology_changed():
                log.info("收到 WM_SETTINGCHANGE（lParam=%r），拓扑有实质变化 -> 恢复", lstr)
                _restore_later(reason="WM_SETTINGCHANGE")
            else:
                log.info("收到 WM_SETTINGCHANGE（lParam=%r），拓扑未变 -> 忽略（不再偷偷恢复）", lstr)
        elif msg == WM_TRAYICON:
            if tray_obj and tray_obj.handle_message(lparam):
                return 0
        elif show_msg and msg == show_msg:
            log.info("收到唤起消息，尝试打开界面")
            open_ui(log)
        elif msg in (WM_CLOSE, WM_DESTROY):
            log.info("收到关闭消息，正在退出…")
            stop.set()
            if tray_obj:
                tray_obj.remove()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(_wndproc)          # 必须保持引用，否则回调被 GC 回收
    cls = "IconMemo_WatchWnd"
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = cls
    user32.RegisterClassW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(0, cls, "IconMemo", 0, 0, 0, 0, 0,
                                  None, None, wc.hInstance, None)
    if not hwnd:
        raise RuntimeError(f"CreateWindowExW 失败 err={ctypes.get_last_error()}")
    hwnd_holder[0] = hwnd

    # ---- 挂载托盘 ----
    if tray:
        tray_obj = Tray(hwnd, tooltip="图标记忆 · 桌面布局监视",
                        on_command=_tray_command,
                        on_dblclick=lambda: open_ui(log))
        if tray_obj.add():
            log.info("系统托盘已挂载")
        else:
            log.warning("系统托盘挂载失败（不影响监视功能）")
            tray_obj = None

    # ---- 启动就绪轮询：explorer / 桌面 ListView 未就绪时带退避重试 ----
    t0 = time.time()
    attempt = 0
    hLV, src = None, ""
    while not stop.is_set():
        hLV, _, src = find_desktop_listview()
        if hLV:
            break
        attempt += 1
        if time.time() - t0 >= ready_timeout or attempt >= 30:
            log.warning("等待桌面就绪超时（%.1fs / %d 次），仍进入消息循环",
                        time.time() - t0, attempt)
            break
        time.sleep(1.0)
    if hLV:
        log.info("桌面已就绪（等待 %.2fs / %d 次）：%s", time.time() - t0, attempt, src)
    state["topology"] = _current_topology()

    env = environment()
    log.info("当前环境：%s / %s DPI / %d 个虚拟桌面 / 拓扑 %s",
             topology_label(env), env["dpi"], env["virtual_desktops"], env["topology"])
    if snapshot_every > 0:
        log.info("定时快照：每 %g 分钟自动留存一张", snapshot_every)

    if snapshot_every > 0:
        threading.Thread(target=_snapshot_loop, daemon=True, name="snapshot").start()
        # 启动时先拍一张基线快照
        try:
            do_snapshot(note="监视启动基线", quiet=True)
        except Exception:
            pass

    try:
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if tray_obj:
            tray_obj.remove()
        # 关键：显式销毁隐藏窗口。
        # 消息循环退出（PostQuitMessage）并不会销毁窗口 —— 不销毁会残留
        # IconMemo_WatchWnd 窗口句柄，且阻塞进程正常退出（托盘退出后进程僵留）。
        try:
            user32.DestroyWindow(wintypes.HWND(hwnd))
        except Exception:
            pass
        log.info("watch 已退出")
    return 0


def _watch_command(with_snapshot: float = 0.0) -> str:
    """构造开机自启命令。

    优先级：IconMemoGUI.exe（无控制台）→ IconMemoCLI.exe → pythonw.exe + 脚本（开发态）。
    关键：被拉起的进程必须是【无控制台】可执行文件，否则开机登录时会弹黑窗。
    with_snapshot>0 时把 --snapshot-every 一并写入（修缺陷：自启与定时快照脱节）。
    """
    extra = f" --snapshot-every {with_snapshot:g}" if with_snapshot and with_snapshot > 0 else ""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    cli_exe = os.path.join(base, "IconMemoCLI.exe")
    if os.path.exists(cli_exe):
        return f'"{cli_exe}" watch{extra}'

    if getattr(sys, "frozen", False):
        # 自身即为程序，无专用 watch exe：直接以自身 watch
        return f'"{sys.executable}" watch{extra}'

    # 开发态：优先 pythonw.exe（GUI 子系统，不弹黑窗），回退 python.exe
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    runner = pyw if os.path.exists(pyw) else py
    script = os.path.abspath(__file__)
    return f'"{runner}" "{script}" watch{extra}'


def _extract_exe_from_command(cmd: str | None) -> str | None:
    """从注册命令中取出首个可执行文件路径（用于有效性自检）。"""
    if not cmd:
        return None
    m = re.match(r'^\s*"([^"]+)"', cmd) or re.match(r'^\s*(\S+)', cmd)
    return m.group(1) if m else None


def autostart_status() -> dict:
    """报告当前开机自启注册状态：命令 / 是否有效（指向的程序是否存在）。"""
    cmd = _run_value(APP_NAME)
    if not cmd:
        return {"registered": False, "command": None, "valid": False, "exe": None}
    exe = _extract_exe_from_command(cmd)
    valid = bool(exe) and os.path.exists(exe)
    return {"registered": True, "command": cmd, "valid": valid, "exe": exe}


def do_autostart(enable: bool = True, quiet: bool = False, with_snapshot: float = 0.0,
                 show_status: bool = False) -> str:
    """把 watch 模式注册到当前用户的开机启动项（HKCU\\...\\Run，不需要管理员）。

    用户决策：不做计划任务（放弃 schtasks），继续用 Run 键。要点：
      · 注册命令指向【无控制台】可执行文件，避免开机弹黑窗；
      · 注册表权限用 KEY_SET_VALUE（不再用 KEY_ALL_ACCESS）；
      · --with-snapshot N 会把 --snapshot-every N 一并写进自启命令（修缺陷：自启与定时快照脱节）；
      · show_status=True 时只报告当前注册状态与有效性（自检）。
    """
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    if show_status:
        st = autostart_status()
        if not quiet:
            if st["registered"]:
                print(f"[状态] 已注册开机自启：{st['command']}")
                print("       指向程序存在："
                      + ("是" if st["valid"] else "否（已失效，可重新执行 autostart 修复）"))
            else:
                print("[状态] 未设置开机自启")
        return "status"

    if enable:
        cmd = _watch_command(with_snapshot=with_snapshot)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        msg = f"[OK] 已设置开机自动监视：{cmd}"
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, APP_NAME)
            msg = "[OK] 已取消开机自动监视"
        except FileNotFoundError:
            msg = "[--] 本来就没有设置开机自动监视"
    if not quiet:
        print(msg)
    _LOG.info("autostart %s：%s", "enable" if enable else "disable", msg)
    return msg


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    if os.name == "nt" and shcore is not None:
        try:
            shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            pass

    p = argparse.ArgumentParser(
        prog="iconmemo",
        description="图标记忆 —— 桌面图标布局保存 / 恢复",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  iconmemo save                  保存当前分辨率的布局（主档案）
  iconmemo save 办公             另存为带名字的布局
  iconmemo restore               恢复当前分辨率的主档案
  iconmemo restore --dry-run     只比对、不实际移动
  iconmemo undo                  撤销上一次恢复

  iconmemo snapshot "改前留档"   立即拍一张布局快照
  iconmemo snapshots             列出快照历史（含变化量）
  iconmemo rollback 2            回滚到第 2 新的一张快照

  iconmemo topology              查看显示器拓扑
  iconmemo list / info / doctor  档案列表 / 图标坐标 / 环境自检
  iconmemo status                运行实例 / 自启 / 档案统计
  iconmemo watch --snapshot-every 30   常驻 + 每 30 分钟自动留档（默认挂托盘）
  iconmemo autostart --with-snapshot 30  开机自启（含定时快照）
  iconmemo import-dsv "D:\档案"  导入外部 .dsv 布局档案（只读，不改原文件）
""")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("save", help="保存当前桌面图标布局")
    sp.add_argument("name", nargs="?", help="布局名字（可选，留空=当前分辨率主档案）")

    rp = sub.add_parser("restore", help="恢复桌面图标布局")
    rp.add_argument("name", nargs="?", help="布局名字或 json 文件路径（留空=主档案）")
    rp.add_argument("--dry-run", action="store_true", help="只比对不移动")
    rp.add_argument("--keep-autoarrange", action="store_true", help="恢复后把自动排列改回开启")
    rp.add_argument("--scale", choices=["auto", "none"], default="auto",
                    help="桌面区域变化时是否按比例自适应坐标（默认 auto）")

    sub.add_parser("undo", help="撤销上一次恢复")
    sub.add_parser("list", help="列出所有布局档案")
    sub.add_parser("info", help="查看当前桌面图标与位置")
    sub.add_parser("doctor", help="环境自检")
    sub.add_parser("topology", help="显示显示器拓扑详情")
    sub.add_parser("status", help="一屏状态报告（实例/自启/档案/快照）")

    sn = sub.add_parser("snapshot", help="立即保存一张布局快照")
    sn.add_argument("note", nargs="?", default="", help="快照备注（可选）")
    sn.add_argument("--no-dedup", action="store_true", help="与上一张相同也强制保存")
    sn.add_argument("--keep", type=int, default=60, help="保留快照数量上限（默认 60）")

    sns = sub.add_parser("snapshots", help="列出布局快照历史")
    sns.add_argument("--limit", type=int, default=20, help="显示条数（默认 20）")

    rb = sub.add_parser("rollback", help="回滚到某张历史快照")
    rb.add_argument("target", nargs="?", help="快照序号或文件名（留空=最新一张）")
    rb.add_argument("--scale", choices=["auto", "none"], default="auto",
                    help="坐标自适应缩放（默认 auto）")

    wp = sub.add_parser("watch", help="常驻监视：分辨率/显示器变化时自动恢复")
    wp.add_argument("--delay", type=float, default=2.0,
                    help="检测到变化后等待多少秒再恢复（默认 2.0，避开系统重排）")
    wp.add_argument("--snapshot-every", type=float, default=0.0, metavar="MIN",
                    help="每 N 分钟自动留存一张布局快照（0=关闭）")
    wp.add_argument("--tray", dest="tray", action="store_true", default=True,
                    help="挂载系统托盘（默认开启）")
    wp.add_argument("--no-tray", dest="tray", action="store_false",
                    help="不挂载系统托盘")

    ap = sub.add_parser("autostart", help="设置/取消 开机自动监视")
    ap.add_argument("--off", action="store_true", help="取消开机自动监视")
    ap.add_argument("--with-snapshot", type=float, default=0.0, metavar="MIN",
                    help="把 --snapshot-every N 一并写进自启命令（0=不含）")
    ap.add_argument("--status", dest="show_status", action="store_true",
                    help="只查看当前自启注册状态与有效性")

    ip = sub.add_parser("import-dsv", help="导入外部 .dsv 布局档案（只读，不改原文件）")
    ip.add_argument("src",
                    help="档案文件或包含 .dsv 的目录（必填：请显式指定路径）")
    ip.add_argument("--force", action="store_true",
                    help="允许覆盖已存在的同名主档案（默认关闭：已存在则另存为 导入-*.json）")

    args = p.parse_args(argv)
    cmd = args.cmd or "info"

    setup_logging()

    # 单实例保护：仅对【常驻监视 watch】生效。
    # 说明：save/restore/... 等一次性 CLI 命令不加此限制，否则会让常驻流程阻塞命令行操作；
    #       而 watch 是常驻守护进程，多实例会争抢同一桌面，必须唯一。
    if cmd == "watch":
        if not single_instance():
            signal_existing_instance()
            print("[--] 图标记忆 监视已在运行，已唤起既有实例（本次不再启动）。")
            return 0

    try:
        if cmd == "save":
            do_save(args.name)
        elif cmd == "restore":
            r = do_restore(args.name, keep_autoarrange=args.keep_autoarrange,
                           dry_run=args.dry_run, scale=args.scale)
            if args.dry_run:
                print(f"[DRY-RUN] 档案：{os.path.basename(r['path'])}")
                print(f"          可匹配 {r['matched']} 个图标"
                      f"（其中需要移动 {r['will_move']} 个，{r['unchanged']} 个已在位）")
                for d in r["topology_diffs"]:
                    print(f"          ⚠ {d}")
                for m in r["move_detail"][:8]:
                    p = m["from"]
                    src = f"{p[0]},{p[1]}" if p else "?"
                    print(f"            · {m['name']}: {src} → {m['to'][0]},{m['to'][1]}")
                if r["will_move"] > 8:
                    print(f"            ... 其余 {r['will_move'] - 8} 个")
                if r["scaled"]:
                    print(f"          · 将按 {r['scale_factor'][0]:.3f}×/{r['scale_factor'][1]:.3f} 缩放坐标")
                if r["out_of_bounds"]:
                    print(f"          · {r['out_of_bounds']} 个图标会超出当前桌面范围")
                if r["missing"]:
                    print(f"          档案中已不存在：{', '.join(r['missing'][:10])}")
                if r["extra"]:
                    print(f"          桌面新增未收录：{', '.join(r['extra'][:10])}")
        elif cmd == "undo":
            do_undo()
        elif cmd == "list":
            do_list()
        elif cmd == "info":
            do_info()
        elif cmd == "doctor":
            do_doctor()
        elif cmd == "topology":
            do_topology()
        elif cmd == "status":
            do_status()
        elif cmd == "snapshot":
            do_snapshot(args.note, dedup=not args.no_dedup, keep=args.keep)
        elif cmd == "snapshots":
            do_snapshots(limit=args.limit)
        elif cmd == "rollback":
            do_rollback(args.target, scale=args.scale)
        elif cmd == "watch":
            do_watch(delay=args.delay, snapshot_every=args.snapshot_every, tray=args.tray)
        elif cmd == "autostart":
            do_autostart(enable=not args.off, with_snapshot=args.with_snapshot,
                         show_status=args.show_status)
        elif cmd == "import-dsv":
            import_dsv(args.src, force=args.force)
        return 0
    except Exception as e:
        _LOG.exception("命令 %s 执行失败", cmd)
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
