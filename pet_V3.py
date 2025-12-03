import sys
import os
import random
import psutil

# Attempt to import pynvml for GPU monitoring
try:
    import pynvml

    HAS_PYNVML = True
except ImportError:
    HAS_PYNVML = False

from PyQt5.QtWidgets import QApplication, QMainWindow, QMenu, QAction, QSystemTrayIcon, QActionGroup, QInputDialog
from PyQt5.QtCore import QTimer, Qt, QPoint
from PyQt5.QtGui import QPixmap, QPainter, QTransform, QIcon

# ==========================================
# 1. Configuration Area
# ==========================================
# Default configurations
DEFAULT_IMG_DIR_QUAN = "./img_quan"
DEFAULT_IMG_DIR_CAT = "./img_cat"
RUNCAT_DIR = "./icons/cat2/processed"

# Global MAX_PETS variable, modifiable at runtime
MAX_PETS = 5

FLOOR_OFFSET = 50
RIGHT_WALL_OFFSET = 55

ACTIONS = {
    # --- Spawn ---
    "born": [{"img": f"born{i:05d}.png", "dur": 300} for i in range(6)],

    # --- Air/Throw ---
    "fly": [{"img": "fly.png", "dur": 100}],
    "drag_throw": [{"img": "drag00004.png", "dur": 100}],
    "drop": [{"img": "drop.png", "dur": 100}],

    # --- Ground ---
    "idle": [{"img": "idle.png", "dur": 3000}],
    "walk": [{"img": f"walk{i:05d}.png", "dur": 150} for i in range(11)],
    "run": [{"img": f"walk{i:05d}.png", "dur": 100} for i in range(11, 20)],
    "standup": [{"img": f"standup{i:05d}.png", "dur": 150} for i in range(3)],

    # --- Sit ---
    "sit": [{"img": f"sit{i:05d}.png", "dur": 150} for i in range(10)],
    "sit_idle": [{"img": "sit00009.png", "dur": 2000}],
    "sitloop": [{"img": f"sitloop{i:05d}.png", "dur": 150} for i in range(19)],

    # --- Wall/Ceiling ---
    "wall_idle": [{"img": "wall00000.png", "dur": 3000}],
    "wall_climb": [{"img": f"wall{i:05d}.png", "dur": 180} for i in range(1, 12)],
    "wall_descend": [{"img": f"wall{i:05d}.png", "dur": 180} for i in range(1, 12)],
    "ceiling_walk": [{"img": f"ceiling{i:05d}.png", "dur": 120} for i in range(8)],

    # --- Drag ---
    "drag_left_slow": [{"img": "drag00001.png", "dur": 100}],
    "drag_left_fast": [{"img": "drag00002.png", "dur": 100}],
    "drag_right_slow": [{"img": "drag00003.png", "dur": 100}],
    "drag_right_fast": [{"img": "drag00002.png", "dur": 100}],

    # --- Other ---
    "ie_walk": [{"img": f"ie{i:05d}.png", "dur": 150} for i in range(11)],
    "struggle": [{"img": f"struggle{i:05d}.png", "dur": 120} for i in range(3)],
}


# ==========================================
# 2. Resource Singleton (SharedAssets)
# ==========================================
class SharedAssets:
    """
    Singleton pattern: Loads image resources only once, shared among all pets.
    Now supports loading assets for multiple character types.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(SharedAssets, cls).__new__(cls, *args, **kwargs)
            cls._instance.initialized = False
            cls._instance.img_cache = {}  # Dictionary to store caches for each pet type
            cls._instance.runcat_icons = []
        return cls._instance

    def load_pet_assets(self, pet_type, img_dir):
        """Loads assets for a specific pet type if not already loaded."""
        if pet_type in self.img_cache:
            return  # Already loaded

        if not os.path.exists(img_dir):
            print(f"Error: Image directory not found: {img_dir}")
            return

        # print(f"Loading assets for {pet_type} from {img_dir}...")
        transform = QTransform().scale(-1, 1)  # For creating mirrored images
        type_cache = {}

        for frames_list in ACTIONS.values():
            for frame_data in frames_list:
                name = frame_data["img"]
                if name in type_cache: continue

                path = os.path.join(img_dir, name)
                pix = QPixmap(path)
                if pix.isNull():
                    # Fallback to a transparent placeholder if image is missing
                    pix = QPixmap(128, 128)
                    pix.fill(Qt.transparent)

                type_cache[name] = pix
                type_cache[name + "_r"] = pix.transformed(transform)

        self.img_cache[pet_type] = type_cache
        print(f"Assets for {pet_type} loaded.")

    def load_runcat_icons(self):
        """Loads RunCat icons (shared)."""
        if self.runcat_icons: return

        if os.path.exists(RUNCAT_DIR):
            for i in range(10):
                p = os.path.join(RUNCAT_DIR, f"{i}.png")
                if os.path.exists(p):
                    self.runcat_icons.append(QIcon(p))
                else:
                    # Fallback is tricky here without a guaranteed loaded pet type,
                    # so we just try to use a placeholder or skip.
                    # For simplicity, we assume at least one pet type loads successfully
                    # and we might use its idle image if needed, but here we just skip or use empty.
                    self.runcat_icons.append(QIcon())

    def get_pixmap(self, pet_type, name, look_right=False):
        if pet_type not in self.img_cache:
            return None

        key = name + "_r" if look_right else name
        return self.img_cache[pet_type].get(key)


# ==========================================
# 3. Pet Manager (PetManager)
# ==========================================
class PetManager(QSystemTrayIcon):
    """
    Inherits from QSystemTrayIcon, acts as the control center.
    Responsible for: Tray icon, hardware monitor thread, management of all pet instances.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pets = []
        self.assets = SharedAssets()
        self.app = QApplication.instance()

        # Load assets for the default pet (quan)
        self.assets.load_pet_assets("quan", DEFAULT_IMG_DIR_QUAN)
        self.assets.load_runcat_icons()

        # --- Hardware Monitor Init ---
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

        # --- Init Tray ---
        self.init_tray_ui()

        # --- Start Timers ---
        # 1. Hardware sampling timer (1 sec)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.update_monitor_data)
        self.monitor_timer.start(1000)

        # 2. Window sorting timer (500ms)
        self.sort_timer = QTimer(self)
        self.sort_timer.timeout.connect(self.sort_windows)
        self.sort_timer.start(500)

        # 3. Start RunCat animation
        self.update_runcat_icon()

    def init_tray_ui(self):
        # Set default icon (using 'quan' idle image)
        default_pix = self.assets.get_pixmap("quan", "idle.png")
        if default_pix:
            self.setIcon(QIcon(default_pix))

        # --- Tray Menu Logic ---
        tray_menu = QMenu()

        monitor_menu = QMenu("监测指标", tray_menu)
        monitor_group = QActionGroup(self)

        act_cpu = QAction("CPU", self, checkable=True)
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

        # --- Dynamic MAX_PETS Setting ---
        act_set_max_pets = QAction("设置宠物数量上限", self)
        act_set_max_pets.triggered.connect(self.set_max_pets)
        tray_menu.addAction(act_set_max_pets)

        tray_menu.addSeparator()

        # --- Spawn Options ---
        spawn_menu = QMenu("生成分身", tray_menu)

        act_spawn_quan = QAction("生成犬夜叉分身", self)
        act_spawn_quan.triggered.connect(lambda: self.spawn_pet(pet_type="quan"))
        spawn_menu.addAction(act_spawn_quan)

        act_spawn_cat = QAction("生成云母分身", self)
        act_spawn_cat.triggered.connect(lambda: self.spawn_pet(pet_type="cat"))
        spawn_menu.addAction(act_spawn_cat)

        tray_menu.addMenu(spawn_menu)

        act_clean = QAction("清除所有分身", self)
        act_clean.triggered.connect(self.remove_all_pets)
        tray_menu.addAction(act_clean)

        tray_menu.addSeparator()
        act_exit = QAction("退出程序", self)
        act_exit.triggered.connect(self.app.quit)
        tray_menu.addAction(act_exit)

        self.setContextMenu(tray_menu)
        self.show()

    # def set_max_pets(self):
    #     """动态设置 MAX_PETS，并自动清理多余的宠物（UI美化版）"""
    #     global MAX_PETS
    #
    #     # 1. 创建对话框实例（而不是使用静态方法）
    #     dialog = QInputDialog()
    #     dialog.setWindowTitle("设置")
    #     dialog.setLabelText("请输入最大宠物数量:")
    #     dialog.setIntRange(1, 50)
    #     dialog.setIntValue(MAX_PETS)
    #     dialog.setIntStep(1)
    #
    #     # 2. 设置按钮文字（中文化）
    #     dialog.setOkButtonText("确定")
    #     dialog.setCancelButtonText("取消")
    #
    #     # 3. 去除标题栏上的“问号”帮助按钮
    #     dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    #
    #     # 4. 【核心美化】设置 QSS 样式表
    #     # 类似网页 CSS，支持圆角、扁平化、颜色定义
    #     dialog.setStyleSheet("""
    #         QDialog {
    #             background-color: #ffffff;
    #             font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    #         }
    #         QLabel {
    #             font-size: 14px;
    #             font-weight: bold;
    #             color: #333333;
    #             margin-bottom: 5px;
    #         }
    #         QSpinBox {
    #             min-width: 200px;
    #             height: 32px;
    #             padding: 0 5px;
    #             border: 1px solid #cccccc;
    #             border-radius: 4px;
    #             font-size: 14px;
    #             background-color: #f9f9f9;
    #         }
    #         QSpinBox:focus {
    #             border: 1px solid #0078d4;
    #             background-color: #ffffff;
    #         }
    #         /* 确定按钮 (默认按钮) */
    #         QPushButton {
    #             height: 30px;
    #             padding: 0 15px;
    #             border-radius: 4px;
    #             font-size: 13px;
    #             background-color: #f0f0f0;
    #             border: 1px solid #dcdcdc;
    #         }
    #         QPushButton:hover {
    #             background-color: #e0e0e0;
    #         }
    #         /* 专门美化“确定”按钮，设为蓝色高亮 */
    #         QPushButton:default {
    #             background-color: #0078d4;
    #             color: white;
    #             border: 1px solid #0078d4;
    #             font-weight: bold;
    #         }
    #         QPushButton:default:hover {
    #             background-color: #006abc;
    #         }
    #         QPushButton:default:pressed {
    #             background-color: #005a9e;
    #         }
    #     """)
    #
    #     # 5. 执行弹窗并获取结果
    #     ok = dialog.exec_()
    #     num = dialog.intValue()
    #
    #     if ok:
    #         MAX_PETS = num
    #         # print(f"MAX_PETS updated to {MAX_PETS}")
    #
    #         # --- 检查并清理多余宠物逻辑 ---
    #         current_count = len(self.pets)
    #         if current_count > MAX_PETS:
    #             # 获取多出来的宠物列表（从列表末尾截取，即移除最新生成的）
    #             pets_to_remove = self.pets[MAX_PETS:]
    #             for pet in pets_to_remove:
    #                 pet.close()
    def set_max_pets(self):
        """动态设置 MAX_PETS（UI美化版 - 简约白圆润风）"""
        global MAX_PETS

        dialog = QInputDialog()
        # 保留了你喜欢的自定义文字
        dialog.setWindowTitle("召唤设置 ✨")
        dialog.setLabelText("想要多少只小可爱同时出现？")
        dialog.setIntRange(1, 50)
        dialog.setIntValue(MAX_PETS)
        dialog.setIntStep(1)

        # 按钮文字
        dialog.setOkButtonText("决定了")
        dialog.setCancelButtonText("算了")

        # 去除问号
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # 【核心美化】简约白圆润风 QSS
        style_sheet = """
            /* 全局背景：纯白 */
            QDialog {
                background-color: #ffffff;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            }

            /* 标签文字：深灰，清晰 */
            QLabel {
                font-size: 15px;
                color: #333333;
                font-weight: bold;
                margin-bottom: 10px;
            }

            /* 输入框：极简灰白底 + 圆角 */
            QSpinBox {
                min-width: 180px;
                height: 30px;
                border: 2px solid #f0f0f0; /* 极淡的边框 */
                border-radius: 20px;       /* 全圆角 */
                padding: 0 15px;
                background-color: #f9f9f9; /* 极淡灰背景 */
                color: #333333;
                font-size: 18px;
                font-weight: bold;
                selection-background-color: #ddd;
            }
            /* 鼠标悬停 */
            QSpinBox:hover {
                border: 2px solid #e0e0e0;
                background-color: #ffffff;
            }
            /* 聚焦状态：深色边框，强调输入 */
            QSpinBox:focus {
                border: 2px solid #333333; 
                background-color: #ffffff;
            }

            /* 隐藏原本丑陋的上下箭头按钮，保持视觉极简 */
            /* 用户依然可以通过键盘上下键或滚轮调节数值 */
            QSpinBox::up-button, QSpinBox::down-button {
                width: 0px; 
                border: none;
            }

            /* 按钮通用设置 */
            QPushButton {
                height: 36px;
                border-radius: 18px; /* 圆角按钮 */
                font-size: 14px;
                min-width: 90px;
                font-weight: bold;
                border: none;
            }

            /* 取消按钮 ("算了") - 浅灰风格 */
            QPushButton[text="算了"] {
                background-color: #f2f2f2;
                color: #666666;
            }
            QPushButton[text="算了"]:hover {
                background-color: #e5e5e5;
                color: #333333;
            }

            /* 确定按钮 ("决定了") - 深黑风格，黑白对比 */
            QPushButton[text="决定了"] {
                background-color: #333333;
                color: #ffffff;
            }
            QPushButton[text="决定了"]:hover {
                background-color: #555555;
            }
            QPushButton[text="决定了"]:pressed {
                background-color: #000000;
            }
        """
        dialog.setStyleSheet(style_sheet)

        ok = dialog.exec_()
        num = dialog.intValue()

        if ok:
            MAX_PETS = num
            # 清理逻辑
            current_count = len(self.pets)
            if current_count > MAX_PETS:
                pets_to_remove = self.pets[MAX_PETS:]
                for pet in pets_to_remove:
                    pet.close()

    def add_pet(self, pet):
        self.pets.append(pet)

    def remove_pet(self, pet):
        if pet in self.pets:
            self.pets.remove(pet)

    def remove_all_pets(self):
        """Clears all pets."""
        for pet in self.pets[:]:
            pet.close()

    def spawn_pet(self, source_x=None, source_y=None, pet_type="quan"):
        """
        Spawns a pet clone.
        :param source_x: Reference X coordinate (if any)
        :param source_y: Reference Y coordinate (if any)
        :param pet_type: 'quan' or 'cat'
        """
        # Global limit check (applies to total pets of all types)
        if len(self.pets) >= MAX_PETS:
            print(f"Cannot spawn: Max pets limit ({MAX_PETS}) reached.")
            return

        # Ensure assets for this type are loaded
        if pet_type == "cat":
            self.assets.load_pet_assets("cat", DEFAULT_IMG_DIR_CAT)
        elif pet_type == "quan":
            self.assets.load_pet_assets("quan", DEFAULT_IMG_DIR_QUAN)

        start_x, start_y = None, None

        # Priority 1: Use specific coordinates (from right-click)
        if source_x is not None and source_y is not None:
            start_x, start_y = source_x + 20, source_y - 20
        # Priority 2: Use first pet's location as reference
        elif self.pets:
            target = self.pets[0]
            start_x, start_y = target.x + 20, target.y - 20

        new_pet = DesktopPet(self, pet_type=pet_type, start_pos=(start_x, start_y) if start_x else None,
                             start_state="drop")
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

        # Delay algorithm
        delay_sec = 0.2 - (self.current_usage * 0.18)
        delay_ms = int(delay_sec * 1000)
        if delay_ms < 20: delay_ms = 20

        QTimer.singleShot(delay_ms, self.update_runcat_icon)


# ==========================================
# 4. Desktop Pet Class
# ==========================================
class DesktopPet(QMainWindow):
    def __init__(self, manager, pet_type="quan", start_pos=None, start_state="drop"):
        super().__init__()
        self.manager = manager
        self.assets = SharedAssets()
        self.pet_type = pet_type  # Store the type (quan/cat)

        # --- Window Settings ---
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # --- Screen Management ---
        self.desktop = QApplication.desktop()
        if start_pos:
            self.x, self.y = start_pos
        else:
            primary_rect = self.desktop.availableGeometry(self.desktop.primaryScreen())
            self.x = primary_rect.center().x() - 64
            self.y = primary_rect.top() - 128

        self.update_screen_info(force_update=True)

        # --- State & Physics ---
        self.state = start_state
        self.look_right = True
        self.vx = 0
        self.vy = 0
        self.gravity = 2

        # Toggles
        self.is_fixed = False
        self.wander_mode = None

        # Animation
        self.frame_index = 0
        self.frame_timer = 0

        # Interaction
        self.is_dragging = False
        self.mouse_history = []
        self.drag_offset = QPoint(0, 0)
        self.last_drag_x = 0
        self.ceiling_dist = 0

        # --- Timer ---
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

        # Physics Logic
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

        # Move Window
        if self.is_fixed and self.state not in ["fly", "drop", "drag_throw"]:
            pass
        else:
            self.move(int(self.x), int(self.y))

    def update_image(self):
        conf = ACTIONS.get(self.state, ACTIONS["idle"])
        if self.frame_index >= len(conf): self.frame_index = 0

        img_name = conf[self.frame_index]["img"]
        # Request image for this specific pet type
        pix = self.assets.get_pixmap(self.pet_type, img_name, self.look_right)

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
            # Spawn a clone of the SAME type from this pet
            self.manager.spawn_pet(self.x, self.y, pet_type=self.pet_type)
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

    # --- Helper methods ---
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
        """Force start wall wander."""
        self.wander_mode = "wall"
        self.snap_to_nearest_wall()
        self.set_state("wall_climb")

    # --- Physics Logic ---
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
            self.x = left;
            self.look_right = False
        else:
            self.x = right;
            self.look_right = True

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
            self.x = l + 5;
            self.look_right = True
        else:
            self.x = r - 5;
            self.look_right = False

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
                self.set_state("drop");
                self.vy = 0
        elif self.x >= right:
            self.x = right
            if self.wander_mode == "ceiling":
                self.look_right = False
            elif self.wander_mode == "full":
                self.set_state("wall_descend")
            else:
                self.set_state("drop");
                self.vy = 0

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

    # --- Mouse Interaction ---
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
                self.set_state("drag_left_fast");
                self.look_right = False
            elif dx > 2:
                self.set_state("drag_right_fast");
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
            vx, vy = 0, 0
            if len(self.mouse_history) >= 2:
                p_last = self.mouse_history[-1]
                p0 = self.mouse_history[0]
                vx = (p_last.x() - p0.x()) * 0.3
                vy = (p_last.y() - p0.y()) * 0.3
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

    # --- Right Click Menu ---
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        debug_menu = menu.addMenu("调试动作")

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

            if key == "wall_wander":
                act.triggered.connect(self.start_wall_wander)

            elif key == "wall_climb":
                act.triggered.connect(lambda: [self.snap_to_nearest_wall(), self.set_state("wall_climb")])

            elif key == "ceiling_wander":
                act.triggered.connect(
                    lambda: [setattr(self, 'wander_mode', "ceiling"), self.set_state("ceiling_walk")])

            elif key == "wander":
                act.triggered.connect(
                    lambda: [setattr(self, 'wander_mode', "full"), self.set_state("walk")])

            elif key == "stop_wander":
                act.triggered.connect(
                    lambda: [setattr(self, 'wander_mode', None), self.set_state("idle")])

            else:
                act.triggered.connect(lambda chk, k=key: self.set_state(k))

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
# 5. Main Entry Point
# ==========================================
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 【核心修复】禁止在最后一个窗口关闭时退出程序
    # 因为我们的宠物是 Tool 窗口，而设置弹窗是普通窗口，
    # 关闭弹窗会导致程序误判为应该退出。
    app.setQuitOnLastWindowClosed(False)

    # 2. Start Manager (Tray, Monitor)
    manager = PetManager()

    # 3. Spawn first pet (Quan by default)
    manager.spawn_pet(pet_type="quan")

    sys.exit(app.exec_())