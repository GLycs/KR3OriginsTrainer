# -*- coding: utf-8 -*-
"""
Kingdom Rush Origins 修改器 (KR3 / kr3-desktop-6.4.46)

架构与 KingdomRushFrontiersTrainer 一致：
    外部 GUI 写状态文件  ->  游戏内 -custom_script 桥接 Lua 读取并生效
不注入 DLL、不读写进程内存、不修改游戏安装目录内的任何文件。

用法：
    1. 点「启动游戏」（自动带 -custom_script kr3_bridge 参数）
    2. 打开「启用修改」总开关
    3. 按需勾选功能，改动即时生效
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Kingdom Rush Origins 修改器"
APP_VERSION = "1.0.0"
BRIDGE_MODULE = "kr3_bridge"
BRIDGE_FILE = "kr3_bridge.lua"
STATE_FILE = "kr3t_state.lua"
STATUS_FILE = "kr3t_status.txt"
GAME_EXE = "Kingdom Rush Origins.exe"
STEAM_APPID = "816340"
GAME_FOLDER = "Kingdom Rush Origins"
SAVE_IDENTITY = "kingdom_rush_origins"

APP_DATA = os.environ.get("APPDATA", os.path.expanduser("~"))
CONFIG_DIR = os.path.join(APP_DATA, "KR3OriginsTrainer")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
GAME_SAVE_DIR = os.path.join(APP_DATA, SAVE_IDENTITY)

BG = "#f4efe4"
PANEL = "#fffdf8"
PANEL_ALT = "#e7dfd0"
FG = "#2f2a22"
FG_DIM = "#7b7264"
ACCENT = "#b5761c"
ACCENT_DARK = "#8a5a12"
OK_GREEN = "#2f7d4f"
WARN_RED = "#b4472f"
ENTRY_BG = "#ffffff"

DEFAULTS = {
    "active": False,
    "heartbeat": 0,
    "revision": 0,
    "speed_enabled": True,
    "speed": 1.0,
    "paused": False,
    "gold_enabled": False,
    "gold_value": 99999,
    "gems_enabled": False,
    "gems_value": 99999,
    "lives_enabled": False,
    "lives_value": 20,
    "kill_gold_enabled": False,
    "kill_gold_multiplier": 2,
    "invincible": False,
    "hero_no_cooldown": False,
    "damage_enabled": False,
    "damage_multiplier": 3,
    "tower_speed_enabled": False,
    "tower_speed_multiplier": 3,
    "tower_range_enabled": False,
    "tower_range_multiplier": 2,
    "barrack_enabled": False,
    "barrack_soldiers": 3,
    "barrack_respawn_scale": 1.0,
    "unlock_levels": False,
    "three_stars": False,
    "read_only": False,
    "cmd_skip_wave": 0,
    "cmd_force_wave": 0,
    "cmd_win": 0,
}

INT_KEYS = {
    "gold_value", "gems_value", "lives_value", "kill_gold_multiplier",
    "barrack_soldiers", "cmd_skip_wave", "cmd_force_wave", "cmd_win",
    "heartbeat", "revision", "damage_multiplier", "tower_speed_multiplier",
    "tower_range_multiplier",
}


# ---------------------------------------------------------------- 路径探测

def _steam_roots():
    roots = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            for name in ("SteamPath", "SteamExe"):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if value:
                    value = os.path.normpath(value.replace("/", "\\"))
                    roots.append(value if os.path.isdir(value) else os.path.dirname(value))
    except Exception:
        pass
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
                  r"D:\Steam", r"D:\SteamLibrary", r"E:\Steam", r"E:\SteamLibrary"):
        if os.path.isdir(guess):
            roots.append(guess)
    return roots


def _library_folders(steam_root):
    folders = [steam_root]
    vdf = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    if not os.path.isfile(vdf):
        return folders
    try:
        with open(vdf, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return folders
    for match in re.finditer(r'"path"\s*"([^"]+)"', text):
        path = os.path.normpath(match.group(1).replace("\\\\", "\\"))
        if os.path.isdir(path):
            folders.append(path)
    return folders


def detect_game_paths():
    found = []
    for root in _steam_roots():
        for library in _library_folders(root):
            exe = os.path.join(library, "steamapps", "common", GAME_FOLDER, GAME_EXE)
            if os.path.isfile(exe) and exe not in found:
                found.append(exe)
    return found


def find_save_dir():
    if os.path.isdir(GAME_SAVE_DIR) and os.path.isfile(os.path.join(GAME_SAVE_DIR, "global.lua")):
        return GAME_SAVE_DIR
    for candidate in (
        os.path.join(APP_DATA, SAVE_IDENTITY),
        os.path.join(APP_DATA, "LOVE", SAVE_IDENTITY),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Roaming", SAVE_IDENTITY),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Roaming", "LOVE", SAVE_IDENTITY),
    ):
        if os.path.isdir(candidate):
            return candidate
    return GAME_SAVE_DIR


# ---------------------------------------------------------------- 状态序列化

def lua_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return "%d" % int(value)
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"%s"' % text


def write_state(path, data):
    lines = ["-- generated by KR3 Origins Trainer --", "return {"]
    for key in sorted(data):
        lines.append('    ["%s"] = %s;' % (key, lua_literal(data[key])))
    lines.append("}")
    payload = "\n".join(lines) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_status(path):
    data = {}
    if not os.path.isfile(path):
        return data, 0.0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
        mtime = os.path.getmtime(path)
    except OSError:
        return {}, 0.0
    return data, mtime


# ---------------------------------------------------------------- 修改器核心

class Trainer(object):
    def __init__(self, log):
        self.log = log
        self.state = dict(DEFAULTS)
        self.game_exe = ""
        self.save_dir = find_save_dir()
        self.config = self._load_config()
        self.pending = set()
        self._last_write = 0.0

    # -- 配置
    def _load_config(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def save_config(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            payload = {"game_exe": self.game_exe}
            with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # -- 桥接部署
    def bridge_source(self):
        # 打包成 exe 后，脚本会被解压到 sys._MEIPASS，需要一并搜索这些位置
        bases = []
        if getattr(sys, "frozen", False):
            bases.append(getattr(sys, "_MEIPASS", ""))
            try:
                bases.append(os.path.dirname(os.path.abspath(sys.executable)))
            except Exception:
                pass
        if sys.argv and sys.argv[0]:
            bases.append(os.path.dirname(os.path.abspath(sys.argv[0])))
        bases.append(os.getcwd())
        for base in bases:
            if not base:
                continue
            candidate = os.path.join(base, BRIDGE_FILE)
            if os.path.isfile(candidate):
                return candidate
        return None

    def deploy_bridge(self, quiet=False):
        source = self.bridge_source()
        if not source:
            if not quiet:
                self.log("未找到 %s，请把它放在修改器同目录下。" % BRIDGE_FILE)
            return False
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            target = os.path.join(self.save_dir, BRIDGE_FILE)
            need_copy = True
            if os.path.isfile(target):
                try:
                    with open(source, "rb") as a, open(target, "rb") as b:
                        need_copy = a.read() != b.read()
                except OSError:
                    need_copy = True
            if need_copy:
                shutil.copyfile(source, target)
                if not quiet:
                    self.log("已部署桥接脚本到 %s" % target)
            elif not quiet:
                self.log("桥接脚本已是最新：%s" % target)
            return True
        except Exception as exc:
            self.log("部署桥接脚本失败：%s" % exc)
            return False

    # -- 游戏进程
    def game_running(self):
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq " + GAME_EXE, "/NH"],
                creationflags=0x08000000,
                stderr=subprocess.DEVNULL,
            ).decode("mbcs", "replace")
            return GAME_EXE.lower() in out.lower()
        except Exception:
            return False

    def launch_game(self):
        if not self.game_exe or not os.path.isfile(self.game_exe):
            self.log("游戏路径无效，请先选择 Kingdom Rush Origins.exe")
            return False
        if self.game_running():
            self.log("游戏已在运行。桥接脚本只在启动时加载，请先关闭游戏再点「启动游戏」。")
            return False
        if not self.deploy_bridge(quiet=True):
            self.log("桥接部署失败，已取消启动。")
            return False
        self.log("桥接脚本已就绪，正在启动游戏…")
        env = os.environ.copy()
        env["SteamAppId"] = STEAM_APPID
        env["SteamGameId"] = STEAM_APPID
        try:
            subprocess.Popen(
                [self.game_exe, "-custom_script", BRIDGE_MODULE],
                cwd=os.path.dirname(self.game_exe),
                env=env,
                close_fds=True,
            )
            self.log("已启动游戏（-custom_script %s）" % BRIDGE_MODULE)
            return True
        except Exception as exc:
            self.log("启动失败：%s" % exc)
            return False

    # -- 状态推送
    def push(self, force=False):
        now = time.time()
        if not force and now - self._last_write < 0.05:
            return
        self._last_write = now
        self.state["heartbeat"] = int(now)
        self.state["revision"] = int(self.state.get("revision", 0)) + 1
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            write_state(os.path.join(self.save_dir, STATE_FILE), self.state)
        except Exception as exc:
            self.log("写状态文件失败：%s" % exc)

    def shutdown(self):
        self.state["active"] = False
        self.state["heartbeat"] = int(time.time())
        try:
            write_state(os.path.join(self.save_dir, STATE_FILE), self.state)
        except Exception:
            pass

    def read_status(self):
        data, mtime = read_status(os.path.join(self.save_dir, STATUS_FILE))
        if not data:
            alt = os.path.join(os.path.dirname(self.game_exe or ""), STATUS_FILE)
            if self.game_exe and os.path.isfile(alt):
                alt_data, alt_mtime = read_status(alt)
                if alt_data:
                    return alt_data, alt_mtime
        return data, mtime

    def command(self, key):
        self.state[key] = int(self.state.get(key, 0)) + 1
        self.push(force=True)


# ---------------------------------------------------------------- 界面

class TrainerApp(object):
    def __init__(self, root):
        self.root = root
        self.trainer = Trainer(self.log_line)
        self.log_lines = []
        self._last_hotkey = ""
        self._build_style()
        self._build_ui()
        self._load_values()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        atexit.register(self.trainer.shutdown)
        self._tick()

    # -- 样式
    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(bg=BG)
        style.configure(".", background=BG, foreground=FG, fieldbackground=ENTRY_BG,
                        bordercolor=PANEL_ALT, lightcolor=PANEL_ALT, darkcolor=PANEL_ALT,
                        font=("Microsoft YaHei UI", 9))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG)
        style.configure("Dim.TLabel", background=PANEL, foreground=FG_DIM,
                        font=("Microsoft YaHei UI", 8))
        style.configure("Title.TLabel", background=BG, foreground=ACCENT,
                        font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Head.TLabel", background=BG, foreground=FG_DIM,
                        font=("Microsoft YaHei UI", 8))
        style.configure("Status.TLabel", background=PANEL_ALT, foreground=FG,
                        font=("Microsoft YaHei UI", 9))
        style.configure("TCheckbutton", background=PANEL, foreground=FG,
                        focuscolor=PANEL)
        style.map("TCheckbutton",
                  background=[("active", PANEL)],
                  foreground=[("disabled", FG_DIM)])
        style.configure("TRadiobutton", background=PANEL, foreground=FG, focuscolor=PANEL)
        style.map("TRadiobutton", background=[("active", PANEL)])
        style.configure("TButton", background=PANEL_ALT, foreground=FG,
                        borderwidth=1, focusthickness=0, padding=(10, 4))
        style.map("TButton",
                  background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)],
                  foreground=[("disabled", FG_DIM)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#2b1a04",
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#c98a2a")])
        style.configure("Danger.TButton", background=ACCENT_DARK, foreground="#fff8ef")
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
        style.configure("TNotebook.Tab", background=PANEL, foreground=FG_DIM,
                        padding=(14, 6), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", PANEL_ALT)],
                  foreground=[("selected", ACCENT)])
        style.configure("TLabelframe", background=PANEL, bordercolor=PANEL_ALT,
                        relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT,
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TSpinbox", fieldbackground=ENTRY_BG, foreground=FG,
                        background=PANEL_ALT, arrowcolor=FG, insertcolor=FG)
        style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=FG, insertcolor=FG)
        style.configure("Horizontal.TScale", background=PANEL, troughcolor=ENTRY_BG)
        style.configure("TProgressbar", background=ACCENT, troughcolor=ENTRY_BG,
                        bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT)

    # -- 组件
    def _build_ui(self):
        root = self.root
        root.title("%s  v%s" % (APP_TITLE, APP_VERSION))
        root.geometry("760x800")
        root.minsize(720, 700)

        header = ttk.Frame(root, style="Panel.TFrame", padding=(16, 12))
        header.pack(fill="x", side="top")
        ttk.Label(header, text=APP_TITLE, style="Panel.TLabel",
                  font=("Microsoft YaHei UI", 14, "bold"), foreground=ACCENT).pack(side="left")
        ttk.Label(header, text="  kr3-desktop-6.4.46 · 外部状态桥接 · 不注入进程",
                  style="Dim.TLabel").pack(side="left", pady=(4, 0))
        self.lbl_conn = tk.Label(header, text="未连接", bg=PANEL_ALT, fg=WARN_RED,
                                 font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=3)
        self.lbl_conn.pack(side="right")

        # 游戏路径行
        path_bar = ttk.Frame(root, style="Panel.TFrame", padding=(16, 8))
        path_bar.pack(fill="x", side="top")
        ttk.Label(path_bar, text="游戏", style="Dim.TLabel").pack(side="left")
        self.var_path = tk.StringVar()
        self.entry_path = ttk.Entry(path_bar, textvariable=self.var_path, width=52)
        self.entry_path.pack(side="left", padx=(8, 6), fill="x", expand=True)
        ttk.Button(path_bar, text="浏览", command=self.on_browse).pack(side="left", padx=2)
        self.btn_launch = ttk.Button(path_bar, text="启动游戏", style="Accent.TButton",
                                     command=self.on_launch)
        self.btn_launch.pack(side="left", padx=(6, 2))

        # 总开关
        power = ttk.Frame(root, style="Panel.TFrame", padding=(16, 10))
        power.pack(fill="x", side="top", pady=(6, 0))
        self.var_active = tk.BooleanVar(value=False)
        self.chk_active = tk.Checkbutton(
            power, text="启用修改", variable=self.var_active,
            bg=PANEL, fg=ACCENT, activebackground=PANEL, activeforeground=ACCENT,
            selectcolor=ENTRY_BG, font=("Microsoft YaHei UI", 11, "bold"),
            bd=0, highlightthickness=0, command=self.on_active)
        self.chk_active.pack(side="left")
        ttk.Label(power, text="关闭后游戏内所有改动按原值恢复；退出修改器同样会自动还原。",
                  style="Dim.TLabel").pack(side="left", padx=(12, 0))

        # 状态条
        self.status_bar = tk.Label(root, text="等待游戏启动…", bg=PANEL_ALT, fg=FG_DIM,
                                   anchor="w", padx=16, pady=6,
                                   font=("Microsoft YaHei UI", 9))
        self.status_bar.pack(fill="x", side="top", pady=(8, 0))

        # 页签
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=10)
        self.tab_resource = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.tab_combat = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.tab_tower = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.tab_progress = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.tab_settings = ttk.Frame(self.nb, style="Panel.TFrame", padding=12)
        self.nb.add(self.tab_resource, text=" 资源与速度 ")
        self.nb.add(self.tab_combat, text=" 战斗强化 ")
        self.nb.add(self.tab_tower, text=" 防御塔与兵营 ")
        self.nb.add(self.tab_progress, text=" 关卡进度 ")
        self.nb.add(self.tab_settings, text=" 设置与日志 ")

        self._build_tab_resource()
        self._build_tab_combat()
        self._build_tab_tower()
        self._build_tab_progress()
        self._build_tab_settings()

        self.var_status = tk.StringVar()
        self.chk_status = tk.BooleanVar(value=False)
        self.controls = []

    # ---- 资源页
    def _build_tab_resource(self):
        p = self.tab_resource
        speed_box = self._group(p, "游戏速度")
        row = ttk.Frame(speed_box, style="Panel.TFrame")
        row.pack(fill="x", pady=(0, 6))
        self.var_speed_preset = tk.IntVar(value=1)
        for label, value in (("1x", 1), ("2x", 2), ("3x", 3), ("4x", 4), ("5x", 5), ("8x", 8)):
            ttk.Radiobutton(row, text=label, value=value, variable=self.var_speed_preset,
                            command=self.on_speed_preset).pack(side="left", padx=(0, 10))
        row2 = ttk.Frame(speed_box, style="Panel.TFrame")
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="自定义倍率", style="Panel.TLabel").pack(side="left")
        self.var_speed = tk.DoubleVar(value=1.0)
        self.scale_speed = ttk.Scale(row2, from_=1.0, to=12.0, variable=self.var_speed,
                                     command=self.on_speed_scale)
        self.scale_speed.pack(side="left", fill="x", expand=True, padx=10)
        self.lbl_speed = ttk.Label(row2, text="1.0x", style="Panel.TLabel", width=6)
        self.lbl_speed.pack(side="left")
        self.var_paused = tk.BooleanVar(value=False)
        ttk.Checkbutton(speed_box, text="暂停游戏（时间停止，可继续操作界面）",
                        variable=self.var_paused, command=self.on_pause).pack(anchor="w", pady=(6, 0))

        gold_box = self._group(p, "金币与生命")
        self.var_gold_enabled = tk.BooleanVar(value=False)
        self.var_gold_value = tk.IntVar(value=99999)
        self._check_spin(gold_box, "锁定金币", self.var_gold_enabled, self.var_gold_value,
                         0, 999999, 500, self.on_gold)
        self.var_lives_enabled = tk.BooleanVar(value=False)
        self.var_lives_value = tk.IntVar(value=20)
        self._check_spin(gold_box, "锁定基地生命", self.var_lives_enabled, self.var_lives_value,
                         1, 9999, 5, self.on_lives)

        gem_box = self._group(p, "宝石与赏金")
        self.var_gems_enabled = tk.BooleanVar(value=False)
        self.var_gems_value = tk.IntVar(value=99999)
        self._check_spin(gem_box, "宝石数量（写入存档）", self.var_gems_enabled,
                         self.var_gems_value, 0, 999999, 500, self.on_gems)
        self.var_kill_gold = tk.BooleanVar(value=False)
        self.var_kill_gold_mult = tk.IntVar(value=2)
        self._check_spin(gem_box, "杀敌金币倍率", self.var_kill_gold,
                         self.var_kill_gold_mult, 1, 20, 1, self.on_kill_gold)
        ttk.Label(gem_box, text="倍率按游戏自己发放的金币补差额，不影响成就统计。",
                  style="Dim.TLabel").pack(anchor="w", pady=(2, 0))

    # ---- 战斗页
    def _build_tab_combat(self):
        p = self.tab_combat
        box = self._group(p, "友军")
        self.var_invincible = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="友军无敌（英雄 / 士兵 / 防御塔血量锁定）",
                        variable=self.var_invincible,
                        command=self.on_invincible).pack(anchor="w", pady=2)
        self.var_hero_cd = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="英雄技能无冷却",
                        variable=self.var_hero_cd,
                        command=self.on_hero_cd).pack(anchor="w", pady=2)
        self.var_damage = tk.BooleanVar(value=False)
        self.var_damage_mult = tk.IntVar(value=3)
        self._check_spin(box, "友军伤害倍率", self.var_damage, self.var_damage_mult,
                         1, 20, 1, self.on_damage)

        tips = self._group(p, "说明")
        ttk.Label(tips, text="· 无敌只锁定已有单位的血量上限，不改变敌人伤害数值。",
                  style="Dim.TLabel").pack(anchor="w")
        ttk.Label(tips, text="· 伤害倍率作用于友方攻击的 damage_min / damage_max 与塔的 damage_factor。",
                  style="Dim.TLabel").pack(anchor="w")
        ttk.Label(tips, text="· 关闭开关立即按原值恢复，不会把放大后的数值写进存档。",
                  style="Dim.TLabel").pack(anchor="w")

    # ---- 塔与兵营页
    def _build_tab_tower(self):
        p = self.tab_tower
        box = self._group(p, "防御塔")
        self.var_tower_speed = tk.BooleanVar(value=False)
        self.var_tower_speed_mult = tk.IntVar(value=3)
        self._check_spin(box, "攻击速度倍率", self.var_tower_speed, self.var_tower_speed_mult,
                         1, 20, 1, self.on_tower_speed)
        self.var_tower_range = tk.BooleanVar(value=False)
        self.var_tower_range_mult = tk.IntVar(value=2)
        self._check_spin(box, "攻击射程倍率", self.var_tower_range, self.var_tower_range_mult,
                         1, 10, 1, self.on_tower_range)

        bar = self._group(p, "兵营")
        self.var_barrack = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="启用兵营增强", variable=self.var_barrack,
                        command=self.on_barrack).pack(anchor="w", pady=2)
        row = ttk.Frame(bar, style="Panel.TFrame")
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="单座兵营士兵数", style="Panel.TLabel").pack(side="left")
        self.var_barrack_count = tk.IntVar(value=3)
        sp = ttk.Spinbox(row, from_=1, to=10, width=6, textvariable=self.var_barrack_count,
                         command=self.on_barrack_num)
        sp.pack(side="left", padx=8)
        self.var_barrack_count.trace_add("write", lambda *a: self.on_barrack_num())
        row2 = ttk.Frame(bar, style="Panel.TFrame")
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="补员时间倍率", style="Panel.TLabel").pack(side="left")
        self.var_barrack_respawn = tk.DoubleVar(value=1.0)
        sp2 = ttk.Spinbox(row2, from_=0.1, to=5.0, increment=0.1, width=6,
                          textvariable=self.var_barrack_respawn, command=self.on_barrack_respawn)
        sp2.pack(side="left", padx=8)
        self.var_barrack_respawn.trace_add("write", lambda *a: self.on_barrack_respawn())
        ttk.Label(bar, text="0.5 = 补员快一倍；原版 1.0。士兵数上限受等级与天赋限制，改了会直接覆盖。",
                  style="Dim.TLabel").pack(anchor="w", pady=(4, 0))

        wave = self._group(p, "战局控制")
        brow = ttk.Frame(wave, style="Panel.TFrame")
        brow.pack(fill="x", pady=2)
        ttk.Button(brow, text="提前下一波", command=self.on_skip_wave).pack(side="left", padx=(0, 8))
        ttk.Button(brow, text="强制推进波次", command=self.on_force_wave).pack(side="left", padx=(0, 8))
        ttk.Button(brow, text="立即通关", style="Danger.TButton",
                   command=self.on_win).pack(side="left")
        ttk.Label(wave, text="立即通关会清空场上敌人并驱动游戏自己的结算流程，"
                             "胜利星级与解锁由游戏正常写入存档。",
                  style="Dim.TLabel").pack(anchor="w", pady=(6, 0))

    # ---- 进度页
    def _build_tab_progress(self):
        p = self.tab_progress
        box = self._group(p, "存档进度")
        self.var_unlock = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="临时解锁全部关卡（关闭即还原）",
                        variable=self.var_unlock,
                        command=self.on_unlock).pack(anchor="w", pady=2)
        self.var_stars = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="临时全关三星（关闭即还原）",
                        variable=self.var_stars,
                        command=self.on_stars).pack(anchor="w", pady=2)
        self.var_readonly = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="只读模式：完全不写游戏存档",
                        variable=self.var_readonly,
                        command=self.on_readonly).pack(anchor="w", pady=(8, 2))
        ttk.Label(box, text="只读模式下宝石 / 解锁 / 三星三项强制失效，"
                            "速度、金币、生命、战斗类功能不受影响。",
                  style="Dim.TLabel").pack(anchor="w")

        info = self._group(p, "当前存档")
        self.lbl_slot = ttk.Label(info, text="存档槽：—    关卡：—", style="Panel.TLabel")
        self.lbl_slot.pack(anchor="w")
        self.lbl_level_name = ttk.Label(info, text="关卡名：—", style="Dim.TLabel")
        self.lbl_level_name.pack(anchor="w", pady=(2, 0))
        ttk.Button(info, text="打开存档目录", command=self.on_open_save).pack(anchor="w", pady=(8, 0))

        warn = self._group(p, "注意事项")
        ttk.Label(warn, text="· 解锁与三星属于存档级改动：开启期间游戏随时可能把当前状态写盘。",
                  style="Dim.TLabel").pack(anchor="w")
        ttk.Label(warn, text="· 想保持存档干净，请先勾选「只读模式」，或只在单局内使用运行时功能。",
                  style="Dim.TLabel").pack(anchor="w")
        ttk.Label(warn, text="· 首次使用建议先备份 %s 目录。" % SAVE_IDENTITY,
                  style="Dim.TLabel").pack(anchor="w")

    # ---- 设置页
    def _build_tab_settings(self):
        p = self.tab_settings
        box = self._group(p, "桥接")
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="状态文件目录", style="Panel.TLabel").pack(side="left")
        self.lbl_savedir = ttk.Label(row, text=self.trainer.save_dir, style="Dim.TLabel")
        self.lbl_savedir.pack(side="left", padx=8)
        brow = ttk.Frame(box, style="Panel.TFrame")
        brow.pack(fill="x", pady=(8, 0))
        ttk.Button(brow, text="重新部署桥接", command=self.on_deploy).pack(side="left", padx=(0, 6))
        ttk.Button(brow, text="打游戏目录", command=self.on_open_game_dir).pack(side="left", padx=6)
        ttk.Button(brow, text="恢复默认", command=self.on_reset).pack(side="left", padx=6)

        keys = self._group(p, "游戏内热键")
        ttk.Label(keys, text="Ctrl+Alt+1  金币锁定开关        Ctrl+Alt+2  生命锁定开关",
                  style="Dim.TLabel").pack(anchor="w")
        ttk.Label(keys, text="Ctrl+Alt+3  友军无敌开关        Ctrl+Alt+4  速度在 1x / 2x / 3x 间循环",
                  style="Dim.TLabel").pack(anchor="w")
        ttk.Label(keys, text="Ctrl+Alt+5  暂停开关",
                  style="Dim.TLabel").pack(anchor="w")

        logbox = self._group(p, "日志")
        frame = ttk.Frame(logbox, style="Panel.TFrame")
        frame.pack(fill="both", expand=True)
        self.txt_log = tk.Text(frame, height=9, bg=ENTRY_BG, fg=FG_DIM, bd=0,
                               insertbackground=FG, font=("Consolas", 8), wrap="word")
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.txt_log.yview)
        sb.pack(side="right", fill="y")
        self.txt_log.configure(yscrollcommand=sb.set, state="disabled")

    # -- 组合控件
    def _group(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=(12, 8))
        frame.pack(fill="x", pady=(0, 10))
        return frame

    def _check_spin(self, parent, text, bool_var, num_var, lo, hi, inc, callback):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=2)
        ttk.Checkbutton(row, text=text, variable=bool_var, command=callback).pack(side="left")
        spin = ttk.Spinbox(row, from_=lo, to=hi, increment=inc, width=9,
                           textvariable=num_var, command=callback)
        spin.pack(side="left", padx=(10, 0))
        num_var.trace_add("write", lambda *a: callback())
        return spin

    # ---------------------------------------------------------------- 事件
    def log_line(self, message):
        stamp = time.strftime("%H:%M:%S")
        self.log_lines.append("[%s] %s" % (stamp, message))
        if len(self.log_lines) > 300:
            self.log_lines = self.log_lines[-300:]
        if hasattr(self, "txt_log"):
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", "[%s] %s\n" % (stamp, message))
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")

    def _push(self):
        self.trainer.push(force=True)

    def on_active(self):
        self.trainer.state["active"] = bool(self.var_active.get())
        self._push()
        self.log_line("总开关：%s" % ("启用" if self.var_active.get() else "关闭（游戏内数值还原）"))

    def on_speed_preset(self):
        self.var_speed.set(float(self.var_speed_preset.get()))
        self.trainer.state["speed"] = float(self.var_speed_preset.get())
        self.lbl_speed.configure(text="%.1fx" % self.var_speed.get())
        self._push()

    def on_speed_scale(self, _value=None):
        value = round(float(self.var_speed.get()) * 2) / 2.0
        self.trainer.state["speed"] = value
        self.trainer.state["speed_enabled"] = True
        self.lbl_speed.configure(text="%.1fx" % value)
        self._push()

    def on_pause(self):
        self.trainer.state["paused"] = bool(self.var_paused.get())
        self._push()

    def on_gold(self):
        self.trainer.state["gold_enabled"] = bool(self.var_gold_enabled.get())
        self.trainer.state["gold_value"] = self._int(self.var_gold_value, 99999)
        self._push()

    def on_lives(self):
        self.trainer.state["lives_enabled"] = bool(self.var_lives_enabled.get())
        self.trainer.state["lives_value"] = self._int(self.var_lives_value, 20)
        self._push()

    def on_gems(self):
        self.trainer.state["gems_enabled"] = bool(self.var_gems_enabled.get())
        self.trainer.state["gems_value"] = self._int(self.var_gems_value, 99999)
        self._push()

    def on_kill_gold(self):
        self.trainer.state["kill_gold_enabled"] = bool(self.var_kill_gold.get())
        self.trainer.state["kill_gold_multiplier"] = self._int(self.var_kill_gold_mult, 2)
        self._push()

    def on_invincible(self):
        self.trainer.state["invincible"] = bool(self.var_invincible.get())
        self._push()

    def on_hero_cd(self):
        self.trainer.state["hero_no_cooldown"] = bool(self.var_hero_cd.get())
        self._push()

    def on_damage(self):
        self.trainer.state["damage_enabled"] = bool(self.var_damage.get())
        self.trainer.state["damage_multiplier"] = self._int(self.var_damage_mult, 3)
        self._push()

    def on_tower_speed(self):
        self.trainer.state["tower_speed_enabled"] = bool(self.var_tower_speed.get())
        self.trainer.state["tower_speed_multiplier"] = self._int(self.var_tower_speed_mult, 3)
        self._push()

    def on_tower_range(self):
        self.trainer.state["tower_range_enabled"] = bool(self.var_tower_range.get())
        self.trainer.state["tower_range_multiplier"] = self._int(self.var_tower_range_mult, 2)
        self._push()

    def on_barrack(self):
        self.trainer.state["barrack_enabled"] = bool(self.var_barrack.get())
        self._push()

    def on_barrack_num(self):
        self.trainer.state["barrack_soldiers"] = self._int(self.var_barrack_count, 3)
        self._push()

    def on_barrack_respawn(self):
        try:
            value = float(self.var_barrack_respawn.get())
        except (tk.TclError, ValueError):
            value = 1.0
        value = max(0.1, min(5.0, value))
        self.trainer.state["barrack_respawn_scale"] = value
        self._push()

    def on_unlock(self):
        self.trainer.state["unlock_levels"] = bool(self.var_unlock.get())
        self._push()

    def on_stars(self):
        self.trainer.state["three_stars"] = bool(self.var_stars.get())
        self._push()

    def on_readonly(self):
        self.trainer.state["read_only"] = bool(self.var_readonly.get())
        self._push()

    def on_skip_wave(self):
        self.trainer.command("cmd_skip_wave")
        self.log_line("指令：提前下一波")

    def on_force_wave(self):
        self.trainer.command("cmd_force_wave")
        self.log_line("指令：强制推进波次")

    def on_win(self):
        if not messagebox.askyesno("立即通关", "将清空场上敌人并触发游戏自己的胜利结算。\n"
                                               "星级与关卡解锁由游戏正常写入存档，继续吗？"):
            return
        self.trainer.command("cmd_win")
        self.log_line("指令：立即通关")

    def on_browse(self):
        path = filedialog.askopenfilename(
            title="选择 Kingdom Rush Origins.exe",
            filetypes=[("可执行文件", "*.exe")])
        if path:
            self.trainer.game_exe = path
            self.var_path.set(path)
            self.trainer.save_config()
            self.log_line("游戏路径：%s" % path)

    def on_launch(self):
        path = self.var_path.get().strip('"')
        if not path or not os.path.isfile(path):
            messagebox.showwarning("未找到游戏", "请先选择 Kingdom Rush Origins.exe 的路径。")
            return
        self.trainer.game_exe = path
        self.trainer.save_config()
        if self.trainer.launch_game():
            self.root.after(1500, self._tick_status)

    def on_deploy(self):
        if self.trainer.deploy_bridge():
            messagebox.showinfo("完成", "桥接脚本已写入：\n%s" % self.trainer.save_dir)

    def on_open_save(self):
        os.startfile(self.trainer.save_dir)

    def on_open_game_dir(self):
        directory = os.path.dirname(self.trainer.game_exe) if self.trainer.game_exe else ""
        if directory and os.path.isdir(directory):
            os.startfile(directory)
        else:
            messagebox.showwarning("提示", "还没有设置有效的游戏路径。")

    def on_reset(self):
        if not messagebox.askyesno("恢复默认", "把所有开关与数值复位为默认（总开关会关闭）？"):
            return
        self.trainer.state = dict(DEFAULTS)
        self._load_values()
        self._push()
        self.log_line("已恢复默认设置")

    def on_close(self):
        self.trainer.shutdown()
        self.root.destroy()

    # ---------------------------------------------------------------- 循环
    def _int(self, var, fallback):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return fallback

    def _load_values(self):
        state = self.trainer.state
        self.var_active.set(bool(state.get("active")))
        self.var_speed.set(float(state.get("speed", 1)))
        self.var_speed_preset.set(int(round(float(state.get("speed", 1)))))
        self.var_paused.set(bool(state.get("paused")))
        self.var_gold_enabled.set(bool(state.get("gold_enabled")))
        self.var_gold_value.set(int(state.get("gold_value", 99999)))
        self.var_lives_enabled.set(bool(state.get("lives_enabled")))
        self.var_lives_value.set(int(state.get("lives_value", 20)))
        self.var_gems_enabled.set(bool(state.get("gems_enabled")))
        self.var_gems_value.set(int(state.get("gems_value", 99999)))
        self.var_kill_gold.set(bool(state.get("kill_gold_enabled")))
        self.var_kill_gold_mult.set(int(state.get("kill_gold_multiplier", 2)))
        self.var_invincible.set(bool(state.get("invincible")))
        self.var_hero_cd.set(bool(state.get("hero_no_cooldown")))
        self.var_damage.set(bool(state.get("damage_enabled")))
        self.var_damage_mult.set(int(state.get("damage_multiplier", 3)))
        self.var_tower_speed.set(bool(state.get("tower_speed_enabled")))
        self.var_tower_speed_mult.set(int(state.get("tower_speed_multiplier", 3)))
        self.var_tower_range.set(bool(state.get("tower_range_enabled")))
        self.var_tower_range_mult.set(int(state.get("tower_range_multiplier", 2)))
        self.var_barrack.set(bool(state.get("barrack_enabled")))
        self.var_barrack_count.set(int(state.get("barrack_soldiers", 3)))
        self.var_barrack_respawn.set(float(state.get("barrack_respawn_scale", 1.0)))
        self.var_unlock.set(bool(state.get("unlock_levels")))
        self.var_stars.set(bool(state.get("three_stars")))
        self.var_readonly.set(bool(state.get("read_only")))
        self.lbl_speed.configure(text="%.1fx" % float(state.get("speed", 1)))

    def _tick(self):
        # 心跳：每 400ms 重写状态文件，让游戏侧知道修改器还活着
        if self.trainer.state.get("active"):
            self.trainer.push()
        self._tick_status()
        self.root.after(400, self._tick)

    def _consume_hotkey(self, raw):
        """消费游戏内热键请求；桥接只上报序号+动作，真正改状态在这里做。"""
        raw = (raw or "").strip()
        if not raw or raw == self._last_hotkey:
            return
        self._last_hotkey = raw
        try:
            _seq, action = raw.split(":", 1)
        except ValueError:
            return
        if action == "toggle_gold":
            self.var_gold_enabled.set(not self.var_gold_enabled.get())
            self.on_gold()
        elif action == "toggle_lives":
            self.var_lives_enabled.set(not self.var_lives_enabled.get())
            self.on_lives()
        elif action == "toggle_invincible":
            self.var_invincible.set(not self.var_invincible.get())
            self.on_invincible()
        elif action == "cycle_speed":
            current = int(round(float(self.var_speed.get())))
            nxt = {1: 2, 2: 3, 3: 1}.get(current, 1)
            self.var_speed_preset.set(nxt)
            self.on_speed_preset()
        elif action == "toggle_pause":
            self.var_paused.set(not self.var_paused.get())
            self.on_pause()
        self.log_line("游戏内热键：%s" % action)

    def _tick_status(self):
        data, mtime = self.trainer.read_status()
        now = time.time()
        online = bool(data) and (now - mtime) < 3.0 and data.get("bridge") == "1"
        if online:
            self._consume_hotkey(data.get("hotkey", ""))
        if online:
            self.lbl_conn.configure(text="已连接", bg="#dcecd9", fg=OK_GREEN)
            active = data.get("active") == "1"
            scene = "关卡内" if data.get("scene") == "game" else "主菜单/地图"
            parts = ["桥接 v%s" % data.get("bridge_version", "?"), scene]
            if data.get("level") not in (None, "", "0"):
                parts.append("关卡 %s" % data.get("level"))
            if data.get("gold"):
                parts.append("金币 %s" % data.get("gold"))
            if data.get("lives"):
                parts.append("生命 %s" % data.get("lives"))
            if data.get("entities") not in (None, "", "0"):
                parts.append("实体 %s" % data.get("entities"))
            parts.append("生效中" if active else "未启用")
            if data.get("read_only") == "1":
                parts.append("只读模式")
            self.status_bar.configure(text="  ·  ".join(parts), fg=FG)
            self.lbl_slot.configure(
                text="存档槽：%s    关卡：%s    关卡名：%s" % (
                    data.get("slot", "—"), data.get("level", "—"),
                    data.get("level_name") or "—"))
            self.lbl_level_name.configure(
                text="桥接版本 %s    游戏版本 %s    存档可写：%s" % (
                    data.get("bridge_version", "?"), data.get("version_string", "?"),
                    "否" if data.get("slot_writable") == "0" else "是"))
        else:
            if self.trainer.game_running():
                self.lbl_conn.configure(text="桥接未加载", bg="#f7e6cf", fg=ACCENT)
                self.status_bar.configure(
                    text="游戏在运行，但没收到桥接心跳：请关闭游戏后用「启动游戏」按钮重新启动。",
                    fg=ACCENT)
            else:
                self.lbl_conn.configure(text="未连接", bg=PANEL_ALT, fg=WARN_RED)
                self.status_bar.configure(text="游戏未运行。点「启动游戏」会自动部署桥接并带上 custom_script 参数。",
                                          fg=FG_DIM)


def main():
    paths = detect_game_paths()
    root = tk.Tk()
    app = TrainerApp(root)
    saved = app.trainer.config.get("game_exe")
    if saved and os.path.isfile(saved):
        app.var_path.set(saved)
        app.trainer.game_exe = saved
    elif paths:
        app.var_path.set(paths[0])
        app.trainer.game_exe = paths[0]
        app.log_line("已自动定位游戏：%s" % paths[0])
    else:
        app.log_line("未能自动定位游戏，请手动选择 exe。")
    app.log_line("存档目录：%s" % app.trainer.save_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
