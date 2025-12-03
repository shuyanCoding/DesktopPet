import os
import cv2
import numpy as np
from PIL import Image, ImageEnhance

# ================= 配置区域 =================
INPUT_DIR = "../icons/cat2"
OUTPUT_DIR = "../icons/cat2/processed_AI"
TARGET_SIZE = (128, 128)  # 最终输出尺寸
PROCESS_SCALE = 4  # 【核心优化】处理倍率 (在 4倍 大图上处理线条，最后缩小以获得光滑效果)
LINE_THICKNESS = 11  # 线条检测块大小 (奇数，越大线条越粗)
NOISE_REMOVAL = 10  # 降噪强度

# ===========================================

def strict_crop(img_pil):
    """
    严格裁剪：去除所有透明边距，只保留主体
    """
    img_data = np.array(img_pil)
    # 提取 Alpha 通道
    if img_data.shape[2] < 4:
        # 如果没有 alpha 通道，尝试添加
        img_pil = img_pil.convert("RGBA")
        img_data = np.array(img_pil)

    alpha = img_data[:, :, 3]
    coords = np.argwhere(alpha > 10)

    if coords.size == 0:
        return img_pil

    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1
    return img_pil.crop((x0, y0, x1, y1))


def process_single_image(file_path, save_path):
    try:
        img_pil = Image.open(file_path).convert("RGBA")

        # 1. 裁剪
        img_pil = strict_crop(img_pil)

        # 2. 【核心优化】放大处理 (Super-sampling)
        # 我们先计算一个很大的“工作尺寸”，比如 512x512
        # 在大图上提取线条，锯齿会变小，断裂会减少
        work_w = TARGET_SIZE[0] * PROCESS_SCALE
        work_h = TARGET_SIZE[1] * PROCESS_SCALE

        # 计算缩放比例 (保持长宽比，留出 padding)
        padding = 10 * PROCESS_SCALE
        draw_w, draw_h = work_w - padding, work_h - padding

        ratio = min(draw_w / img_pil.width, draw_h / img_pil.height)
        new_w = int(img_pil.width * ratio)
        new_h = int(img_pil.height * ratio)

        # 使用高质量重采样放大
        img_large = img_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 3. 转为 OpenCV 格式处理
        # 创建白底背景
        background = Image.new("RGB", img_large.size, (255, 255, 255))
        background.paste(img_large, mask=img_large.split()[3])
        img_cv = cv2.cvtColor(np.array(background), cv2.COLOR_RGB2GRAY)

        # 4. 【核心优化】图像预处理
        # 4.1 双边滤波：这是让线条光滑的关键，它能模糊噪点但保留边缘锐度
        # d=9, sigmaColor=75, sigmaSpace=75
        img_cv = cv2.bilateralFilter(img_cv, 9, 75, 75)

        # 4.2 增强对比度 (CLAHE - 限制对比度自适应直方图均衡化)
        # 比简单的 scaleAbs 更自然
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        img_cv = clahe.apply(img_cv)

        # 5. 线条提取
        binary = cv2.adaptiveThreshold(
            img_cv, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,  # 高斯法比平均法更平滑
            cv2.THRESH_BINARY,
            blockSize=LINE_THICKNESS,
            C=2  # 常数越小，保留的线条细节越多
        )

        # 6. 【核心优化】形态学操作与平滑
        # 6.1 闭运算：连接细微的断裂
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        # 6.2 中值滤波：去除孤立的噪点，磨圆锯齿
        binary = cv2.medianBlur(binary, 3)

        # 7. 准备 Alpha 通道
        # 同样需要对原始 Alpha 进行放大和二值化，保证边缘锋利
        mask = np.array(img_large.split()[3])
        # 对 Mask 做一点点腐蚀，消除原图可能的毛边
        mask = cv2.erode(mask, kernel, iterations=1)
        _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

        # 8. 合成大图 (此时是黑线、白底)
        # 将 opencv 的单通道转回 RGB
        img_lines_large = cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)

        # 逻辑：如果是黑色线条(值<100)，保留黑色；否则变成白色
        # 这里做一个加强：将非黑色的部分强制变白
        threshold_indices = img_lines_large > 127
        img_lines_large[threshold_indices] = 255  # 背景纯白
        img_lines_large[~threshold_indices] = 0  # 线条纯黑

        # 9. 【核心优化】下采样 (Downsampling)
        # 将处理完美的 512x512 大图缩小回 128x128
        # INTER_AREA 是抗锯齿效果最好的缩小算法，它会让线条看起来无比顺滑
        final_rgb = cv2.resize(img_lines_large, (new_w // PROCESS_SCALE, new_h // PROCESS_SCALE),
                               interpolation=cv2.INTER_AREA)
        final_mask = cv2.resize(mask, (new_w // PROCESS_SCALE, new_h // PROCESS_SCALE), interpolation=cv2.INTER_AREA)

        # 10. 组装最终 PIL 图片
        img_final = Image.fromarray(final_rgb)
        mask_final = Image.fromarray(final_mask)

        # 将半透明边缘转为全透明/不透明 (可选，如果喜欢抗锯齿边缘则注释掉下面一行)
        # mask_final = mask_final.point(lambda p: 255 if p > 100 else 0)

        img_final.putalpha(mask_final)

        # 11. 居中输出
        final_canvas = Image.new("RGBA", TARGET_SIZE, (0, 0, 0, 0))
        final_x = (TARGET_SIZE[0] - img_final.width) // 2
        final_y = (TARGET_SIZE[1] - img_final.height) // 2
        final_canvas.paste(img_final, (final_x, final_y), mask=img_final)

        # 最后的锐化 (可选，提升清晰度)
        enhancer = ImageEnhance.Sharpness(final_canvas)
        final_canvas = enhancer.enhance(1.5)

        final_canvas.save(save_path)
        print(f"处理成功 (Super-sampling x{PROCESS_SCALE}): {os.path.basename(file_path)}")

    except Exception as e:
        print(f"处理出错 {file_path}: {e}")
        import traceback
        traceback.print_exc()


def main():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    print(f"开始处理... (工作分辨率: {TARGET_SIZE[0] * PROCESS_SCALE}x{TARGET_SIZE[1] * PROCESS_SCALE})")
    for root, dirs, files in os.walk(INPUT_DIR):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                process_single_image(
                    os.path.join(root, file),
                    os.path.join(OUTPUT_DIR, file)
                )
    print("完成。")


if __name__ == "__main__":
    main()