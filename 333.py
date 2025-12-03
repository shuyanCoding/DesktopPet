import sys
import os
import random
import threading
import psutil  # pip install psutil

# [新增] 尝试导入 pynvml 用于 GPU 监测
try:
    import pynvml  # pip install nvidia-ml-py

    HAS_PYNVML = True
except ImportError:
    HAS_PYNVML = False

from PyQt5.QtWidgets import QApplication, QMainWindow, QMenu, QAction, QSystemTrayIcon, QActionGroup
from PyQt5.QtCore import QTimer, Qt, QPoint
from PyQt5.QtGui import QPixmap, QPainter, QTransform, QIcon

# ==========================================
# 1. 配置区域
# ==========================================
IMG_DIR = "./img_quan"
RUNCAT_DIR = "./icons/runcat"
MAX_PETS = 5
FLOOR_OFFSET = 50
RIGHT_WALL_OFFSET = 55

ACTIONS = {
    # --- 出生 ---
    "born": [{"img": f"born{i:05d}.png", "dur": 300} for i in range(6)],
    # --- 空中/抛掷 ---
    "fly": [{"img": "fly.png", "dur": 100}],
    "drag_throw": [{"img": "drag00004.png", "dur": 100}],
    "drop": [{"img": "drop.png", "dur": 100}],
    # --- 地面 ---
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
# 2. 全局管理器
# ==========================================
class PetManager:
    def __init__(self):
        self.pets = []

    def add_pet(self, pet):
        self.pets.append(pet)
        self.sort_windows()

    def remove_pet(self, pet):
        if pet in self.pets:
            self.pets.remove(pet)

    def can_spawn(self):
        return len(self.pets) < MAX_PETS

    def sort_windows(self):
        for pet in reversed(self.pets):
            pet.raise_()


manager = PetManager()


# ==========================================
# 3. 桌面宠物类
# ==========================================
class DesktopPet(QMainWindow):
    def __init__(self, start_pos=None, start_state="drop"):
        super().__init__()

        # --- 窗口设置 ---
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # --- 多屏幕管理 ---
        self.desktop = QApplication.desktop()

        if start_pos:
            self.x, self.y = start_pos
        else:
            primary_rect = self.desktop.availableGeometry(self.desktop.primaryScreen())
            center_x = primary_rect.center().x() - 64
            self.x = center_x
            self.y = primary_rect.top() - 128

        self.update_screen_info(force_update=True)

        self.state = start_state
        self.look_right = True

        # --- 物理参数 ---
        self.vx = 0
        self.vy = 0
        self.gravity = 2

        # --- 控制开关 ---
        self.is_fixed = False
        self.wander_mode = None

        # --- 交互参数 ---
        self.frame_index = 0
        self.frame_timer = 0
        self.is_dragging = False
        self.mouse_history = []
        self.drag_offset = QPoint(0, 0)
        self.last_drag_x = 0
        self.ceiling_dist = 0

        # --- 资源加载 ---
        self.img_cache = {}
        self.preload_images()

        # --- 核心定时器 ---
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_tick)
        self.timer.start(30)

        # --- 托盘图标与 RunCat 初始化 ---
        # 即使不是第一个宠物，为了支持 RunCat 的数据更新逻辑，
        # 我们这里简化处理：只有第一个宠物负责创建托盘和 RunCat 逻辑
        if len(manager.pets) == 0:
            self.init_runcat()  # 先初始化数据和 GPU
            self.init_tray_icon()  # 再初始化托盘和菜单
        # ----------------------------

        self.update_image()
        self.move(int(self.x), int(self.y))
        self.show()

    def preload_images(self):
        if not os.path.exists(IMG_DIR):
            return
        transform = QTransform().scale(-1, 1)

        def load_file(name):
            if name in self.img_cache: return
            path = os.path.join(IMG_DIR, name)
            pix = QPixmap(path)
            if pix.isNull():
                pix = QPixmap(128, 128)
                pix.fill(Qt.transparent)
            self.img_cache[name] = pix
            self.img_cache[name + "_r"] = pix.transformed(transform)

        for frames_list in ACTIONS.values():
            for frame_data in frames_list:
                load_file(frame_data["img"])

    # ==========================================
    # [新增] 托盘菜单与 RunCat 逻辑
    # ==========================================
    def init_tray_icon(self):
        self.tray = QSystemTrayIcon(self)

        # 默认图标
        icon_path = os.path.join(IMG_DIR, "idle.png")
        if os.path.exists(icon_path):
            self.tray.setIcon(QIcon(icon_path))

        # --- 创建菜单 ---
        tray_menu = QMenu()

        # 1. 监测指标切换菜单 (使用 QActionGroup 实现单选)
        monitor_menu = QMenu("监测指标", tray_menu)
        monitor_group = QActionGroup(self)

        # CPU 选项
        self.act_mon_cpu = QAction("CPU", self, checkable=True)
        self.act_mon_cpu.setChecked(True)  # 默认选中
        self.act_mon_cpu.triggered.connect(lambda: self.set_monitor_mode("cpu"))
        monitor_menu.addAction(monitor_group.addAction(self.act_mon_cpu))

        # Memory 选项
        self.act_mon_mem = QAction("内存 (Memory)", self, checkable=True)
        self.act_mon_mem.triggered.connect(lambda: self.set_monitor_mode("mem"))
        monitor_menu.addAction(monitor_group.addAction(self.act_mon_mem))

        # GPU 选项
        self.act_mon_gpu = QAction("显卡 (GPU)", self, checkable=True)
        if not self.has_gpu:
            self.act_mon_gpu.setEnabled(False)
            self.act_mon_gpu.setText("显卡 (GPU) - 未检测到")
        else:
            self.act_mon_gpu.triggered.connect(lambda: self.set_monitor_mode("gpu"))
        monitor_menu.addAction(monitor_group.addAction(self.act_mon_gpu))

        tray_menu.addMenu(monitor_menu)
        tray_menu.addSeparator()

        # 2. 其他功能
        act_spawn = QAction("生成分身", self)
        act_spawn.triggered.connect(self.spawn_clone)
        tray_menu.addAction(act_spawn)

        tray_menu.addSeparator()

        act_exit = QAction("退出程序", self)
        act_exit.triggered.connect(QApplication.quit)
        tray_menu.addAction(act_exit)

        self.tray.setContextMenu(tray_menu)
        self.tray.show()

        # 启动 RunCat 动画循环
        self.update_runcat_icon()

    def init_runcat(self):
        """初始化监测资源"""
        self.monitor_mode = "cpu"  # 默认模式: 'cpu', 'mem', 'gpu'
        self.current_usage = 0.0
        self.runcat_frame_index = 0
        self.runcat_icons = []
        self.has_gpu = False
        self.gpu_handle = None

        # 加载动画图片
        if os.path.exists(RUNCAT_DIR):
            for i in range(5):
                p = os.path.join(RUNCAT_DIR, f"{i}.png")
                if os.path.exists(p):
                    self.runcat_icons.append(QIcon(p))
                else:
                    self.runcat_icons.append(QIcon(os.path.join(IMG_DIR, "idle.png")))

        # 初始化 GPU (pynvml)
        if HAS_PYNVML:
            try:
                pynvml.nvmlInit()
                # 获取第0号 GPU
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self.has_gpu = True
            except Exception as e:
                print(f"GPU Init Failed: {e}")
                self.has_gpu = False

        # 启动数据采样定时器 (1秒1次)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.update_monitor_data)
        self.monitor_timer.start(1000)

    def set_monitor_mode(self, mode):
        self.monitor_mode = mode
        # 立即更新一次数据
        self.update_monitor_data()

    # --- [核心] 检测函数 ---
    def get_cpu_usage(self):
        # interval=None 非阻塞
        return psutil.cpu_percent(interval=None) / 100.0

    def get_mem_usage(self):
        return psutil.virtual_memory().percent / 100.0

    def get_gpu_usage(self):
        if not self.has_gpu or not self.gpu_handle:
            return 0.0
        try:
            # 获取显存使用率 (RunCat 通常逻辑)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)
            usage = mem_info.used / mem_info.total
            return usage
            # 如果你想用 GPU 计算利用率，可以用这个替代：
            # rates = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)
            # return rates.gpu / 100.0
        except:
            return 0.0

    def update_monitor_data(self):
        """根据当前模式获取对应数据"""
        val = 0.0
        label = "Unknown"

        if self.monitor_mode == "cpu":
            val = self.get_cpu_usage()
            label = "CPU"
        elif self.monitor_mode == "mem":
            val = self.get_mem_usage()
            label = "Memory"
        elif self.monitor_mode == "gpu":
            val = self.get_gpu_usage()
            label = "GPU"

        self.current_usage = val
        if hasattr(self, 'tray'):
            self.tray.setToolTip(f"{label}: {self.current_usage:.1%}")

    def update_runcat_icon(self):
        """动画刷新循环"""
        if not hasattr(self, 'tray') or not self.runcat_icons:
            return

        # 切换图标
        current_icon = self.runcat_icons[self.runcat_frame_index]
        self.tray.setIcon(current_icon)
        self.runcat_frame_index = (self.runcat_frame_index + 1) % len(self.runcat_icons)

        # 根据当前使用率计算延迟
        # usage: 0.0 ~ 1.0
        # 延迟: 200ms (空闲) ~ 20ms (满载)
        delay_sec = 0.2 - (self.current_usage * 0.18)
        delay_ms = int(delay_sec * 1000)
        if delay_ms < 20: delay_ms = 20

        QTimer.singleShot(delay_ms, self.update_runcat_icon)

    # ==========================================
    # 4. 核心循环 Update (未改动)
    # ==========================================
    def update_tick(self):
        self.update_screen_info()
        self.update_animation_frame()

        if self.is_dragging:
            return

        # 3. 物理逻辑
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

        # 4. 移动窗口
        if self.is_fixed and self.state not in ["fly", "drop", "drag_throw"]:
            pass
        else:
            self.move(int(self.x), int(self.y))

    def update_screen_info(self, force_update=False):
        if not force_update and self.state in ["wall_climb", "wall_descend", "wall_idle", "ceiling_walk"]:
            return

        pet_center = QPoint(int(self.x + 64), int(self.y + 64))
        screen_num = self.desktop.screenNumber(pet_center)
        if screen_num == -1:
            screen_num = self.desktop.primaryScreen()

        rect = self.desktop.availableGeometry(screen_num)
        if force_update or getattr(self, 'screen_rect', None) != rect:
            self.screen_rect = rect

    def update_animation_frame(self):
        frames = ACTIONS.get(self.state, ACTIONS["idle"])
        if self.frame_index >= len(frames): self.frame_index = 0
        current_frame = frames[self.frame_index]

        self.frame_timer += 30
        if self.frame_timer >= current_frame["dur"]:
            self.frame_timer = 0
            self.frame_index += 1
            if self.frame_index >= len(frames):
                self.frame_index = 0
                self.on_action_finished()
        self.update_image()

    def on_action_finished(self):
        if self.state == "born":
            self.spawn_clone()
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

    # ==========================================
    # 5. 辅助功能
    # ==========================================
    def respawn_at_top(self):
        center_x = self.screen_rect.center().x() - 64
        self.x = center_x
        self.y = self.screen_rect.top() - 128
        self.vx = 0
        self.vy = 2
        self.set_state("drop")

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
        self.wander_mode = "wall"
        self.snap_to_nearest_wall()
        self.set_state("wall_climb")

    def start_wall_climb_action(self):
        self.wander_mode = None
        self.snap_to_nearest_wall()
        self.set_state("wall_climb")

    # ==========================================
    # 6. 物理逻辑
    # ==========================================
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

        stand_y = self.screen_rect.bottom() - 128 - FLOOR_OFFSET
        abyss_threshold = self.screen_rect.bottom() - 80

        if self.y > abyss_threshold:
            self.respawn_at_top()
            return

        if self.y >= stand_y:
            self.y = stand_y
            self.vx = 0
            self.vy = 0
            self.set_state("idle")

        left_bound = self.screen_rect.left()
        right_bound = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET

        if self.x <= left_bound:
            self.x = left_bound;
            self.vx = 0;
            self.vy = 0
            self.look_right = False
            self.set_state("wall_idle")
        elif self.x >= right_bound:
            self.x = right_bound;
            self.vx = 0;
            self.vy = 0
            self.look_right = True
            self.set_state("wall_idle")

    def update_physics_wall(self):
        left_bound = self.screen_rect.left()
        right_bound = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET

        if self.x < left_bound + 64:
            self.x = left_bound
            self.look_right = False
        else:
            self.x = right_bound
            self.look_right = True

        if self.state == "wall_idle":
            if self.wander_mode in ["wall", "full"]:
                self.set_state("wall_climb")
            elif random.random() < 0.02:
                if self.y <= self.screen_rect.top() + 10:
                    self.set_state("wall_descend")
                else:
                    self.set_state("wall_climb")
            return

        if self.state == "wall_climb":
            if not self.is_fixed: self.y -= 5

            ceiling_y = self.screen_rect.top()

            if self.y <= ceiling_y:
                self.y = ceiling_y
                if self.wander_mode == "wall":
                    self.set_state("wall_descend")
                elif self.wander_mode in ["ceiling", "full"]:
                    self.to_ceiling(left_bound, right_bound)
                else:
                    weights = [20, 30, 30, 20]
                    options = ["idle", "descend", "ceiling", "jump"]
                    choice = random.choices(options, weights=weights)[0]

                    if choice == "idle":
                        self.set_state("wall_idle")
                    elif choice == "descend":
                        self.set_state("wall_descend")
                    elif choice == "ceiling":
                        self.to_ceiling(left_bound, right_bound)
                    elif choice == "jump":
                        self.vy = -4
                        if not self.look_right:
                            self.vx = 8
                            self.set_state("drag_throw")
                            self.look_right = True
                        else:
                            self.vx = -8
                            self.set_state("fly")
                            self.look_right = False

                        if self.look_right:
                            self.x += 10
                        else:
                            self.x -= 10

        elif self.state == "wall_descend":
            if not self.is_fixed: self.y += 5

            stand_y = self.screen_rect.bottom() - 128 - FLOOR_OFFSET

            if self.y >= stand_y:
                self.y = stand_y
                if self.wander_mode == "wall":
                    self.set_state("wall_climb")
                else:
                    self.set_state("idle")

    def to_ceiling(self, left, right):
        self.set_state("ceiling_walk")
        if abs(self.x - left) < 50:
            self.look_right = True
            self.x = left + 5
        else:
            self.look_right = False
            self.x = right - 5

    def update_physics_ceiling(self):
        self.y = self.screen_rect.top()
        speed = 3
        if self.is_fixed: speed = 0

        if self.look_right:
            self.x += speed
        else:
            self.x -= speed

        self.ceiling_dist += speed

        if not self.wander_mode:
            if self.ceiling_dist > 300:
                drop_prob = 0.005
                if self.ceiling_dist > 800: drop_prob = 0.05
                if random.random() < drop_prob:
                    self.set_state("drop");
                    self.vy = 0
                    return

        left_bound = self.screen_rect.left()
        right_bound = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET

        if self.x <= left_bound:
            self.x = left_bound
            if self.wander_mode == "ceiling":
                self.look_right = True
            elif self.wander_mode == "full":
                self.set_state("wall_descend")
            else:
                self.set_state("drop");
                self.vy = 0

        elif self.x >= right_bound:
            self.x = right_bound
            if self.wander_mode == "ceiling":
                self.look_right = False
            elif self.wander_mode == "full":
                self.set_state("wall_descend")
            else:
                self.set_state("drop");
                self.vy = 0

    def update_physics_floor(self):
        if self.state in ["walk", "run", "ie_walk"]:
            speed = 2
            if self.state == "run": speed = 5
            if self.is_fixed: speed = 0

            self.x += speed if self.look_right else -speed

            left_bound = self.screen_rect.left()
            right_bound = self.screen_rect.right() - 128 - RIGHT_WALL_OFFSET

            if self.x <= left_bound:
                self.x = left_bound;
                self.look_right = False
                if self.wander_mode == "full":
                    self.set_state("wall_climb")
                else:
                    self.set_state("wall_idle")

            elif self.x >= right_bound:
                self.x = right_bound;
                self.look_right = True
                if self.wander_mode == "full":
                    self.set_state("wall_climb")
                else:
                    self.set_state("wall_idle")

    # ==========================================
    # 7. AI 与 其他
    # ==========================================
    def set_state(self, new_state):
        if self.state == new_state: return
        if new_state == "ceiling_walk": self.ceiling_dist = 0
        self.state = new_state
        self.frame_index = 0
        self.frame_timer = 0
        self.update_image()

    def decide_ai(self):
        if self.is_fixed:
            if self.state != "idle": self.set_state("idle")
            return

        if self.wander_mode == "full" and self.state == "idle":
            self.set_state("walk")
            return

        r = random.random()
        if self.state == "idle":
            if r < 0.05:
                if manager.can_spawn(): self.set_state("born")
            elif r < 0.3:
                self.set_state("walk")
            elif r < 0.35:
                self.set_state("run")
            elif r < 0.4:
                self.set_state("sit")
            elif r < 0.5:
                self.look_right = not self.look_right
        elif self.state in ["walk", "run", "ie_walk"]:
            if r < 0.1: self.set_state("idle")

    def spawn_clone(self):
        if not manager.can_spawn(): return
        new_pet = DesktopPet(start_pos=(self.x + 20, self.y - 20), start_state="drop")
        new_pet.vy = -5
        new_pet.vx = 2 if self.look_right else -2
        manager.add_pet(new_pet)

    def update_image(self):
        conf = ACTIONS.get(self.state)
        if not conf:
            conf = ACTIONS["idle"]

        if self.frame_index >= len(conf): self.frame_index = 0
        current_frame = conf[self.frame_index]
        img_name = current_frame["img"]

        key = img_name
        if self.look_right: key += "_r"

        if key in self.img_cache:
            self.pixmap = self.img_cache[key]
            self.resize(self.pixmap.size())
            self.update()

    def paintEvent(self, event):
        if hasattr(self, 'pixmap'):
            painter = QPainter(self)
            painter.drawPixmap(0, 0, self.pixmap)

    # ==========================================
    # 8. 鼠标交互
    # ==========================================
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

            current_x = g_pos.x()
            dx = current_x - self.last_drag_x
            self.last_drag_x = current_x
            threshold = 2

            if dx < -threshold:
                self.set_state("drag_left_fast");
                self.look_right = False
            elif dx < 0:
                self.set_state("drag_left_slow");
                self.look_right = False
            elif dx > threshold:
                self.set_state("drag_right_fast");
                self.look_right = True
            elif dx > 0:
                self.set_state("drag_right_slow");
                self.look_right = True

            self.x = new_pos.x()
            self.y = new_pos.y()
            self.move(int(self.x), int(self.y))

            self.mouse_history.append(g_pos)
            if len(self.mouse_history) > 6: self.mouse_history.pop(0)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False

            abyss_threshold = self.screen_rect.bottom() - 80
            if self.y > abyss_threshold:
                self.respawn_at_top()
                event.accept()
                return

            vx, vy = 0, 0
            if len(self.mouse_history) >= 2:
                p_last = self.mouse_history[-1]
                p_first = self.mouse_history[0]
                vx = (p_last.x() - p_first.x()) * 0.3
                vy = (p_last.y() - p_first.y()) * 0.3
            self.vx = vx;
            self.vy = vy

            if vx < -2:
                self.set_state("fly");
                self.look_right = False
            elif vx > 2:
                self.set_state("drag_throw");
                self.look_right = True
            else:
                self.set_state("drop")
            event.accept()

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
            ("IE Walk", "ie_walk"),

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
            if "wander" in key:
                if key == "stop_wander":
                    act.triggered.connect(lambda: [setattr(self, 'wander_mode', None), self.set_state("idle")])
                elif key == "wall_wander":
                    act.triggered.connect(self.start_wall_wander)
                elif key == "ceiling_wander":
                    act.triggered.connect(
                        lambda: [setattr(self, 'wander_mode', "ceiling"), self.set_state("ceiling_walk")])
                elif key == "wander":
                    act.triggered.connect(lambda: [setattr(self, 'wander_mode', "full"), self.set_state("walk")])
            elif key == "wall_climb":
                act.triggered.connect(self.start_wall_climb_action)
            else:
                act.triggered.connect(lambda chk, k=key: self.set_state(k))
            debug_menu.addAction(act)

        menu.addSeparator()
        act_fix = QAction("固定位置", self)
        act_fix.setCheckable(True)
        act_fix.setChecked(self.is_fixed)
        act_fix.toggled.connect(lambda val: setattr(self, 'is_fixed', val))
        menu.addAction(act_fix)

        act_top = QAction("重置到顶部", self)
        act_top.triggered.connect(self.respawn_at_top)
        menu.addAction(act_top)

        act_spawn = QAction("立即生成分身", self)
        act_spawn.triggered.connect(self.spawn_clone)
        menu.addAction(act_spawn)

        act_close = QAction("移除此宠物", self)
        act_close.triggered.connect(self.close_pet)
        menu.addAction(act_close)

        act_exit = QAction("退出", self)
        act_exit.triggered.connect(QApplication.quit)
        menu.addAction(act_exit)

        menu.exec_(event.globalPos())

    def close_pet(self):
        self.close()
        manager.remove_pet(self)
        if len(manager.pets) == 0:
            QApplication.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    pet = DesktopPet(start_state="drop")
    manager.add_pet(pet)

    z_order_timer = QTimer()
    z_order_timer.timeout.connect(manager.sort_windows)
    z_order_timer.start(500)

    sys.exit(app.exec_())