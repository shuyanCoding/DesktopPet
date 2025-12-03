import sys
import os
import random
import psutil

# 尝试导入 pynvml 用于 GPU 监测
try:
    import pynvml

    HAS_PYNVML = True
except ImportError:
    HAS_PYNVML = False

from PyQt5.QtWidgets import QApplication, QMainWindow, QMenu, QAction, QSystemTrayIcon, QActionGroup
from PyQt5.QtCore import QTimer, Qt, QPoint
from PyQt5.QtGui import QPixmap, QPainter, QTransform, QIcon

def resource_path(relative_path):
    """ 获取资源的绝对路径，适配开发环境和打包后的环境 """
    try:
        # PyInstaller 创建的临时目录
        base_path = sys._MEIPASS
    except Exception:
        # 正常运行时的当前目录
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


# ==========================================
# 1. 配置区域
# ==========================================
# 原代码: IMG_DIR = "./img_quan"
# 修改为:
IMG_DIR = resource_path("img_quan")

# 原代码: RUNCAT_DIR = "./icons/cat2/processed"
# 修改为:
RUNCAT_DIR = resource_path("icons")
MAX_PETS = 5
FLOOR_OFFSET = 50
RIGHT_WALL_OFFSET = 55

ACTIONS = {
    # --- 分身 ---
    "born": [{"img": f"born{i:05d}.png", "dur": 300} for i in range(6)],

    # --- 空中/抛掷 ---
    "fly": [{"img": "fly.png", "dur": 100}],
    "drag_throw": [{"img": "drag00004.png", "dur": 100}],
    "drop": [{"img": "drop.png", "dur": 100}],

    # --- 地面动作 ---
    "idle": [{"img": "idle.png", "dur": 3000}],
    "walk": [{"img": f"walk{i:05d}.png", "dur": 150} for i in range(11)],
    "run": [{"img": f"walk{i:05d}.png", "dur": 100} for i in range(11, 20)],
    "standup": [{"img": f"standup{i:05d}.png", "dur": 150} for i in range(3)],

    # --- 坐下 ---
    "sit": [{"img": f"sit{i:05d}.png", "dur": 150} for i in range(10)],
    "sit_idle": [{"img": "sit00009.png", "dur": 2000}],
    "sitloop": [{"img": f"sitloop{i:05d}.png", "dur": 150} for i in range(19)],

    # --- 墙壁/天花板 ---
    "wall_idle": [{"img": "wall00000.png", "dur": 3000}],
    "wall_climb": [{"img": f"wall{i:05d}.png", "dur": 180} for i in range(1, 12)],
    "wall_descend": [{"img": f"wall{i:05d}.png", "dur": 180} for i in range(1, 12)],
    "ceiling_walk": [{"img": f"ceiling{i:05d}.png", "dur": 120} for i in range(8)],

    # --- 拖拽 ---
    "drag_left_slow": [{"img": "drag00001.png", "dur": 100}],
    "drag_left_fast": [{"img": "drag00002.png", "dur": 100}],
    "drag_right_slow": [{"img": "drag00003.png", "dur": 100}],
    "drag_right_fast": [{"img": "drag00002.png", "dur": 100}],

    # --- 其他 ---
    "ie_walk": [{"img": f"ie{i:05d}.png", "dur": 150} for i in range(11)],
    "struggle": [{"img": f"struggle{i:05d}.png", "dur": 120} for i in range(3)],
}


# ==========================================
# 2. 资源单例 (SharedAssets)
# ==========================================
"""
    单例模式：负责只加载一次图片资源，供所有宠物共享。
"""
class SharedAssets:

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(SharedAssets, cls).__new__(cls, *args, **kwargs)
            cls._instance.initialized = False
        return cls._instance

    def load_all(self):
        if self.initialized: return
        self.img_cache = {}
        self.runcat_icons = []

        # 1. 加载宠物动作图片
        if os.path.exists(IMG_DIR):
            transform = QTransform().scale(-1, 1)  # 用于生成镜像图片

            # 预加载所有定义的图片
            for frames_list in ACTIONS.values():
                for frame_data in frames_list:
                    name = frame_data["img"]
                    if name in self.img_cache: continue

                    path = os.path.join(IMG_DIR, name)
                    pix = QPixmap(path)
                    if pix.isNull():
                        pix = QPixmap(128, 128)
                        pix.fill(Qt.transparent)

                    self.img_cache[name] = pix
                    self.img_cache[name + "_r"] = pix.transformed(transform)

        # 2. 加载 RunCat 图标
        if os.path.exists(RUNCAT_DIR):
            for i in range(10):
                p = os.path.join(RUNCAT_DIR, f"{i}.png")
                if os.path.exists(p):
                    self.runcat_icons.append(QIcon(p))
                else:
                    fallback = self.get_pixmap("idle.png")
                    if fallback:
                        self.runcat_icons.append(QIcon(fallback))

        self.initialized = True
        # print("所有资源加载完成。")

    def get_pixmap(self, name, look_right=False):
        key = name + "_r" if look_right else name
        return self.img_cache.get(key)


# ==========================================
# 3. 宠物管理器 (PetManager)
# ==========================================
"""
    继承自 QSystemTrayIcon，作为程序的控制中心。
    负责：托盘图标、硬件监控线程、所有宠物实例的管理。
"""
class PetManager(QSystemTrayIcon):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pets = []
        self.assets = SharedAssets()
        self.app = QApplication.instance()  # 获取APP实例

        # --- 硬件监控初始化 ---
        self.monitor_mode = "cpu"
        self.current_usage = 0.0
        self.runcat_frame_index = 0
        self.has_gpu = False
        self.gpu_handle = None

        if HAS_PYNVML:
            try:
                pynvml.nvmlInit()
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self.has_gpu = True
            except Exception as e:
                print(f"GPU Init Failed: {e}")

        # --- 初始化托盘 ---
        self.init_tray_ui()

        # --- 启动定时器 ---
        # 1. 硬件采样定时器 (1秒1次)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.update_monitor_data)
        self.monitor_timer.start(1000)

        # 2. 窗口层级排序定时器 (500ms1次)
        self.sort_timer = QTimer(self)
        self.sort_timer.timeout.connect(self.sort_windows)
        self.sort_timer.start(500)

        # 3. 启动 RunCat 动画
        self.update_runcat_icon()

    def init_tray_ui(self):
        # 设置默认图标
        default_pix = self.assets.get_pixmap("idle.png")
        if default_pix:
            self.setIcon(QIcon(default_pix))

        # --- 【修改】托盘菜单逻辑 ---
        tray_menu = QMenu()

        monitor_menu = QMenu("监测指标", tray_menu)
        monitor_group = QActionGroup(self)

        act_cpu = QAction("CPU", self, checkable=True)
        # 默认勾选 CPU
        if self.monitor_mode == 'cpu': act_cpu.setChecked(True)
        act_cpu.triggered.connect(lambda: self.set_monitor_mode("cpu"))
        monitor_menu.addAction(monitor_group.addAction(act_cpu))

        act_mem = QAction("内存", self, checkable=True)
        act_mem.triggered.connect(lambda: self.set_monitor_mode("mem"))
        monitor_menu.addAction(monitor_group.addAction(act_mem))

        act_gpu = QAction("显卡 (GPU)", self, checkable=True)
        if not self.has_gpu:
            act_gpu.setEnabled(False)
            act_gpu.setText("显卡 (未检测到)")
        else:
            act_gpu.triggered.connect(lambda: self.set_monitor_mode("gpu"))
        monitor_menu.addAction(monitor_group.addAction(act_gpu))

        tray_menu.addMenu(monitor_menu)
        tray_menu.addSeparator()

        act_spawn = QAction("生成分身", self)
        act_spawn.triggered.connect(self.spawn_pet)
        tray_menu.addAction(act_spawn)

        act_clean = QAction("清除所有分身", self)
        act_clean.triggered.connect(self.remove_all_pets)
        tray_menu.addAction(act_clean)

        tray_menu.addSeparator()
        act_exit = QAction("退出程序", self)
        act_exit.triggered.connect(self.app.quit)
        tray_menu.addAction(act_exit)

        self.setContextMenu(tray_menu)
        self.show()

    def add_pet(self, pet):
        self.pets.append(pet)

    def remove_pet(self, pet):
        if pet in self.pets:
            self.pets.remove(pet)

    def remove_all_pets(self):
        """清除所有分身"""
        # 倒序遍历删除，防止列表索引错乱
        for pet in self.pets[:]:
            pet.close()  # 触发 closeEvent -> manager.remove_pet
        # 这里的 self.pets 会在 pet.close() 调用 remove_pet 时自动清空

    def spawn_pet(self, source_x=None, source_y=None):
        """
        生成分身
        :param source_x: 参考源X坐标 (如果有)
        :param source_y: 参考源Y坐标 (如果有)
        """
        if len(self.pets) >= MAX_PETS: return

        start_x, start_y = None, None

        # 优先级1：如果指定了坐标（来自右键点击的宠物），就从那里生成
        if source_x is not None and source_y is not None:
            start_x, start_y = source_x + 20, source_y - 20
        # 优先级2：如果没有指定（来自托盘菜单），则默认找第一只宠物
        elif self.pets:
            target = self.pets[0]
            start_x, start_y = target.x + 20, target.y - 20

        new_pet = DesktopPet(self, start_pos=(start_x, start_y) if start_x else None, start_state="drop")
        new_pet.vx = random.choice([-2, 2])
        new_pet.vy = -5
        self.add_pet(new_pet)

    def sort_windows(self):
        for pet in self.pets:
            pet.raise_()

    def set_monitor_mode(self, mode):
        self.monitor_mode = mode
        self.update_monitor_data()

    def update_monitor_data(self):
        if self.monitor_mode == "cpu":
            self.current_usage = psutil.cpu_percent(interval=None) / 100.0
            label = "CPU"
        elif self.monitor_mode == "mem":
            self.current_usage = psutil.virtual_memory().percent / 100.0
            label = "MEM"
        elif self.monitor_mode == "gpu" and self.has_gpu:
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
                self.current_usage = mem.used / mem.total
                label = "GPU"
            except:
                self.current_usage = 0.0
                label = "GPU Err"
        else:
            self.current_usage = 0.0
            label = "None"

        self.setToolTip(f"{label}: {self.current_usage:.1%}")

    def update_runcat_icon(self):
        icons = self.assets.runcat_icons
        if not icons: return

        self.setIcon(icons[self.runcat_frame_index])
        self.runcat_frame_index = (self.runcat_frame_index + 1) % len(icons)

        # 延迟算法
        delay_sec = 0.2 - (self.current_usage * 0.18)
        delay_ms = int(delay_sec * 1000)
        if delay_ms < 20: delay_ms = 20

        QTimer.singleShot(delay_ms, self.update_runcat_icon)


# ==========================================
# 4. 桌面宠物类 (轻量化)
# ==========================================
class DesktopPet(QMainWindow):
    def __init__(self, manager, start_pos=None, start_state="drop"):
        super().__init__()
        self.manager = manager  # 持有 Manager 引用
        self.assets = SharedAssets()

        # --- 窗口设置 ---
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # --- 屏幕管理 ---
        self.desktop = QApplication.desktop()
        if start_pos:
            self.x, self.y = start_pos
        else:
            primary_rect = self.desktop.availableGeometry(self.desktop.primaryScreen())
            self.x = primary_rect.center().x() - 64
            self.y = primary_rect.top() - 128

        self.update_screen_info(force_update=True)

        # --- 状态与物理 ---
        self.state = start_state
        self.look_right = True
        self.vx = 0
        self.vy = 0
        self.gravity = 2

        # 开关
        self.is_fixed = False
        self.wander_mode = None

        # 动画
        self.frame_index = 0
        self.frame_timer = 0

        # 交互
        self.is_dragging = False
        self.mouse_history = []
        self.drag_offset = QPoint(0, 0)
        self.last_drag_x = 0
        self.ceiling_dist = 0

        # --- 定时器 ---
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_tick)
        self.timer.start(30)

        self.update_image()
        self.move(int(self.x), int(self.y))
        self.show()

    def update_tick(self):
        self.update_screen_info()
        self.update_animation_frame()

        if self.is_dragging:
            return

        # 物理逻辑
        if self.is_fixed and self.state not in ["drag_throw", "fly", "drop"]:
            pass
        else:
            if self.state in ["born", "sit", "sitloop", "sit_idle", "standup", "struggle"]:
                self.vx = 0
                self.vy = 0
            elif self.state in ["fly", "drop", "drag_throw"]:
                self.update_physics_air()
            elif self.state in ["wall_idle", "wall_climb", "wall_descend"]:
                self.update_physics_wall()
            elif self.state == "ceiling_walk":
                self.update_physics_ceiling()
            elif self.state in ["idle", "walk", "run", "ie_walk"]:
                self.update_physics_floor()

        # 移动窗口
        if self.is_fixed and self.state not in ["fly", "drop", "drag_throw"]:
            pass
        else:
            self.move(int(self.x), int(self.y))

    def update_image(self):
        conf = ACTIONS.get(self.state, ACTIONS["idle"])
        if self.frame_index >= len(conf): self.frame_index = 0

        img_name = conf[self.frame_index]["img"]
        pix = self.assets.get_pixmap(img_name, self.look_right)

        if pix:
            self.pixmap = pix
            self.resize(pix.size())
            self.update()

    def paintEvent(self, event):
        if hasattr(self, 'pixmap'):
            painter = QPainter(self)
            painter.drawPixmap(0, 0, self.pixmap)

    def update_animation_frame(self):
        conf = ACTIONS.get(self.state, ACTIONS["idle"])
        current_frame = conf[self.frame_index]

        self.frame_timer += 30
        if self.frame_timer >= current_frame["dur"]:
            self.frame_timer = 0
            self.frame_index = (self.frame_index + 1)

            if self.frame_index >= len(conf):
                self.frame_index = 0
                self.on_action_finished()
        self.update_image()

    def on_action_finished(self):
        if self.state == "born":
            # 原来的代码：
            # self.manager.spawn_pet()

            # 修改后的代码 (让分身从自己身边生出来)：
            self.manager.spawn_pet(self.x, self.y)
            self.set_state("idle")
        elif self.state == "sit":
            self.set_state("sit_idle")
        elif self.state == "sit_idle":
            if random.random() < 0.3:
                self.set_state("standup")
            else:
                self.set_state("sitloop")
        elif self.state == "sitloop":
            if random.random() < 0.5:
                self.set_state("sit_idle")
            else:
                self.set_state("standup")
        elif self.state == "standup":
            self.set_state("idle")
        elif self.state in ["idle", "walk", "run", "ie_walk"]:
            self.decide_ai()

    def decide_ai(self):
        if self.is_fixed:
            if self.state != "idle": self.set_state("idle")
            return

        r = random.random()
        if self.state == "idle":
            if r < 0.3:
                self.set_state("walk")
            elif r < 0.35:
                self.set_state("run")
            elif r < 0.4:
                self.set_state("sit")
            elif r < 0.5:
                self.look_right = not self.look_right
        elif self.state in ["walk", "run"]:
            if r < 0.1: self.set_state("idle")

    def set_state(self, new_state):
        if self.state == new_state: return
        if new_state == "ceiling_walk": self.ceiling_dist = 0
        self.state = new_state
        self.frame_index = 0
        self.frame_timer = 0
        self.update_image()

    # --- 辅助方法 (用于菜单逻辑) ---
    def snap_to_nearest_wall(self):
        left_wall_x = self.screen_rect.left()
        right_wall_x = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET

        dist_left = abs(self.x - left_wall_x)
        dist_right = abs(self.x - right_wall_x)

        if dist_left < dist_right:
            self.x = left_wall_x
            self.look_right = False
        else:
            self.x = right_wall_x
            self.look_right = True

    def start_wall_wander(self):
        """强制开始墙壁漫游"""
        self.wander_mode = "wall"
        self.snap_to_nearest_wall()
        self.set_state("wall_climb")

    # --- 物理逻辑 ---
    def update_screen_info(self, force_update=False):
        if not force_update and self.state in ["wall_climb", "wall_descend", "wall_idle", "ceiling_walk"]:
            return
        pet_center = QPoint(int(self.x + 64), int(self.y + 64))
        screen_num = self.desktop.screenNumber(pet_center)
        rect = self.desktop.availableGeometry(screen_num)
        if force_update or getattr(self, 'screen_rect', None) != rect:
            self.screen_rect = rect

    def respawn_at_top(self):
        self.x = self.screen_rect.center().x() - 64
        self.y = self.screen_rect.top() - 128
        self.vx = 0;
        self.vy = 2
        self.set_state("drop")

    def update_physics_air(self):
        self.x += self.vx
        self.y += self.vy
        self.vy += self.gravity
        self.vx *= 0.98

        if self.vx < -2:
            if self.state != "fly": self.set_state("fly"); self.look_right = False
        elif self.vx > 2:
            if self.state != "drag_throw": self.set_state("drag_throw"); self.look_right = True
        else:
            if self.state not in ["drop"]: self.set_state("drop")

        if self.y > self.screen_rect.bottom() - 80:
            self.respawn_at_top()
            return

        floor_y = self.screen_rect.bottom() - 128 - FLOOR_OFFSET
        if self.y >= floor_y:
            self.y = floor_y
            self.vx = 0;
            self.vy = 0
            self.set_state("idle")

        left = self.screen_rect.left()
        right = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET
        if self.x <= left:
            self.x = left;
            self.vx = 0;
            self.vy = 0;
            self.look_right = False;
            self.set_state("wall_idle")
        elif self.x >= right:
            self.x = right;
            self.vx = 0;
            self.vy = 0;
            self.look_right = True;
            self.set_state("wall_idle")

    def update_physics_wall(self):
        left = self.screen_rect.left()
        right = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET

        if self.x < left + 64:
            self.x = left; self.look_right = False
        else:
            self.x = right; self.look_right = True

        if self.state == "wall_climb":
            if not self.is_fixed: self.y -= 5
            if self.y <= self.screen_rect.top():
                self.y = self.screen_rect.top()
                self.to_ceiling(left, right)
        elif self.state == "wall_descend":
            if not self.is_fixed: self.y += 5
            floor_y = self.screen_rect.bottom() - 128 - FLOOR_OFFSET
            if self.y >= floor_y:
                self.y = floor_y
                self.set_state("idle")
        elif self.state == "wall_idle":
            if random.random() < 0.02: self.set_state("wall_climb")

    def to_ceiling(self, l, r):
        self.set_state("ceiling_walk")
        if abs(self.x - l) < 50:
            self.x = l + 5; self.look_right = True
        else:
            self.x = r - 5; self.look_right = False

    def update_physics_ceiling(self):
        self.y = self.screen_rect.top()
        speed = 3
        if self.is_fixed: speed = 0
        self.x += speed if self.look_right else -speed
        self.ceiling_dist += speed

        if not self.wander_mode and self.ceiling_dist > 300 and random.random() < 0.005:
            self.set_state("drop");
            self.vy = 0;
            return

        left = self.screen_rect.left()
        right = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET
        if self.x <= left:
            self.x = left
            if self.wander_mode == "ceiling":
                self.look_right = True
            elif self.wander_mode == "full":
                self.set_state("wall_descend")
            else:
                self.set_state("drop"); self.vy = 0
        elif self.x >= right:
            self.x = right
            if self.wander_mode == "ceiling":
                self.look_right = False
            elif self.wander_mode == "full":
                self.set_state("wall_descend")
            else:
                self.set_state("drop"); self.vy = 0

    def update_physics_floor(self):
        if self.state in ["walk", "run", "ie_walk"]:
            speed = 5 if self.state == "run" else 2
            if self.is_fixed: speed = 0
            self.x += speed if self.look_right else -speed

            left = self.screen_rect.left()
            right = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET
            if self.x <= left:
                self.x = left;
                self.look_right = False
                if self.wander_mode == "full":
                    self.set_state("wall_climb")
                else:
                    self.set_state("wall_idle")
            elif self.x >= right:
                self.x = right;
                self.look_right = True
                if self.wander_mode == "full":
                    self.set_state("wall_climb")
                else:
                    self.set_state("wall_idle")

    # --- 鼠标交互 ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.mouse_history = []
            self.drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            self.last_drag_x = event.globalPos().x()
            self.raise_()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_dragging and event.buttons() == Qt.LeftButton:
            g_pos = event.globalPos()
            new_pos = g_pos - self.drag_offset

            curr_x = g_pos.x()
            dx = curr_x - self.last_drag_x
            self.last_drag_x = curr_x

            if dx < -2:
                self.set_state("drag_left_fast"); self.look_right = False
            elif dx > 2:
                self.set_state("drag_right_fast"); self.look_right = True

            self.x = new_pos.x()
            self.y = new_pos.y()
            self.move(int(self.x), int(self.y))

            self.mouse_history.append(g_pos)
            if len(self.mouse_history) > 6: self.mouse_history.pop(0)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            vx, vy = 0, 0
            if len(self.mouse_history) >= 2:
                p_last = self.mouse_history[-1]
                p0 = self.mouse_history[0]
                vx = (p_last.x() - p0.x()) * 0.3
                vy = (p_last.y() - p0.y()) * 0.3
            self.vx = vx;
            self.vy = vy

            if vx < -2:
                self.set_state("fly"); self.look_right = False
            elif vx > 2:
                self.set_state("drag_throw"); self.look_right = True
            else:
                self.set_state("drop")
            event.accept()

    # --- 【修正】右键菜单逻辑 ---
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        debug_menu = menu.addMenu("动作调试")

        debug_list = [
            ("Born (分身)", "born"),
            ("Sit Start (坐下)", "sit"),
            ("Sit Idle  (坐下停留)", "sit_idle"),
            ("Sit Loop (摇腿)", "sitloop"),
            ("Struggle (挣扎)", "struggle"),
            ("Walk (行走)", "walk"),
            ("Run (跑步)", "run"),
            ("Wall Climb (向上爬墙)", "wall_climb"),
            ("Ceiling Walk (爬天花板)", "ceiling_walk"),
            ("IE Walk (健走)", "ie_walk"),

            ("---", "sep"),

            ("Wall Wander (侧边循环)", "wall_wander"),
            ("Ceiling Wander (天花板循环)", "ceiling_wander"),
            ("Full Wander (全图循环)", "wander"),
            ("Stop Wander (停止漫游)", "stop_wander"),
        ]

        for name, key in debug_list:
            if name == "---":
                debug_menu.addSeparator()
                continue

            act = QAction(name, self)

            # --- 核心修改开始 ---
            if key == "wall_wander":
                act.triggered.connect(self.start_wall_wander)

            elif key == "wall_climb":
                # 【修正】：先吸附到最近墙壁，再开始爬墙
                act.triggered.connect(lambda: [self.snap_to_nearest_wall(), self.set_state("wall_climb")])

            elif key == "ceiling_wander":
                act.triggered.connect(
                    lambda: [setattr(self, 'wander_mode', "ceiling"), self.set_state("ceiling_walk")])
            elif key == "wander":
                act.triggered.connect(lambda: [setattr(self, 'wander_mode', "full"), self.set_state("walk")])
            elif key == "stop_wander":
                act.triggered.connect(lambda: [setattr(self, 'wander_mode', None), self.set_state("idle")])
            else:
                act.triggered.connect(lambda chk, k=key: self.set_state(k))
            # --- 核心修改结束 ---

            debug_menu.addAction(act)

        menu.addSeparator()

        act_fix = QAction("固定位置", self)
        act_fix.setCheckable(True)
        act_fix.setChecked(self.is_fixed)
        act_fix.toggled.connect(lambda val: setattr(self, 'is_fixed', val))
        menu.addAction(act_fix)

        menu.addAction("重置到顶部", self.respawn_at_top)
        # 修改后的代码 (使用 lambda 传入当前宠物的坐标)：
        menu.addAction("生成分身", lambda: self.manager.spawn_pet(self.x, self.y))
        menu.addAction("关闭此分身", self.close)

        menu.exec_(event.globalPos())

    def closeEvent(self, event):
        self.manager.remove_pet(self)
        event.accept()


# ==========================================
# 5. 主程序入口
# ==========================================
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 1. 预加载资源 (单例)
    assets = SharedAssets()
    assets.load_all()

    # 2. 启动管理器 (托盘、监控)
    manager = PetManager()

    # 3. 生成第一只宠物
    manager.spawn_pet()

    sys.exit(app.exec_())