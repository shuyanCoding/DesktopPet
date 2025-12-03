"""
    由于使用彩色图片生成动态图标效果不佳，主要是尺寸过小，放大会导致图片只显示部分
    因此这里将彩色图片转换为黑白图片

"""

import os
import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

# ================= 配置区域 =================
INPUT_DIR = "../icons/cat2"  # 你的原始图片文件夹
OUTPUT_DIR = "../icons/cat2/processed"  # 处理后保存的文件夹
TARGET_SIZE = (128, 128)        # 目标尺寸
LINE_THICKNESS = 7              # 线条粗细 (奇数)
BINARY_THRESHOLD = 200          # 二值化阈值 (0-255)，用于最后一步强制变清晰
# ===========================================

def strict_crop(img_pil):
    """
    更严格的裁剪：
    不相信 getbbox()，而是自己扫描像素。
    忽略 alpha < 10 的几乎透明的噪点，强制切掉空白。
    """
    img_data = np.array(img_pil)
    # 提取 Alpha 通道
    alpha = img_data[:, :, 3]

    # 找到所有 alpha > 10 (非透明) 的像素坐标
    coords = np.argwhere(alpha > 10)

    if coords.size == 0:
        return img_pil # 全空，没法切

    # coords 格式是 [y, x]
    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1 # 切片需要 +1

    # 执行裁剪
    return img_pil.crop((x0, y0, x1, y1))

def process_single_image(file_path, save_path):
    try:
        img_pil = Image.open(file_path).convert("RGBA")

        # 1. 【暴力裁剪】去除所有干扰噪点，确保猫咪顶天立地
        img_pil = strict_crop(img_pil)

        # 2. 【缩放】
        # 计算比例，让猫咪的长边完全撑满 TARGET_SIZE
        # 留一点点边距 (padding) 防止贴边太紧不好看
        padding = 4
        draw_size = (TARGET_SIZE[0] - padding*2, TARGET_SIZE[1] - padding*2)

        ratio = min(draw_size[0] / img_pil.width, draw_size[1] / img_pil.height)
        new_w = int(img_pil.width * ratio)
        new_h = int(img_pil.height * ratio)
        # 使用 LANCZOS 保证缩放本身的质量
        img_resized = img_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 3. 【OpenCV 处理线条】
        # 转为白底
        background = Image.new("RGB", img_resized.size, (255, 255, 255))
        background.paste(img_resized, mask=img_resized.split()[3])
        img_cv = cv2.cvtColor(np.array(background), cv2.COLOR_RGB2GRAY)

        # 增强对比度，让线条更明显
        img_cv = cv2.convertScaleAbs(img_cv, alpha=1.2, beta=-10)

        # 降噪
        img_cv = cv2.fastNlMeansDenoising(img_cv, None, 10, 7, 21)

        # 提取线条 (黑线白底)
        binary = cv2.adaptiveThreshold(
            img_cv, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=LINE_THICKNESS,
            C=3
        )

        # 4. 【核心清晰化步骤】
        # 将 OpenCV 结果转回 PIL
        img_lines = Image.fromarray(binary).convert("RGBA")

        # 获取猫咪身体的形状 (Mask)
        mask = img_resized.split()[3]

        # 对 Mask 进行二值化处理！
        # 这一步非常关键：它把边缘半透明的像素强制变成“全透明”或“全不透明”
        # 去除边缘的毛刺感，模仿像素画风格
        mask = mask.point(lambda p: 255 if p > 100 else 0)

        # 应用 Mask：背景透明
        img_lines.putalpha(mask)

        # 5. 【最终二值化清理】
        # 上面的 adaptiveThreshold 生成的线条可能带有噪点
        # 我们把整张图变成只有 纯黑(0,0,0) 和 纯白(255,255,255) 两种颜色
        # 这样图标在缩小后看起来会非常锐利
        data = np.array(img_lines)
        r, g, b, a = data.T

        # 定义：如果不是背景透明，且颜色比较深，就强制变成纯黑线条
        # 否则变成纯白填充
        # 这里使用 broad cast 逻辑
        dark_pixel = (r < 150) & (g < 150) & (b < 150) & (a > 50)

        data[..., :-1][dark_pixel.T] = (0, 0, 0)       # 线条 -> 纯黑
        data[..., :-1][~dark_pixel.T] = (255, 255, 255) # 其他 -> 纯白 (配合 Alpha 通道)

        img_final = Image.fromarray(data)

        # 6. 【居中粘贴】
        final_canvas = Image.new("RGBA", TARGET_SIZE, (0, 0, 0, 0))
        x = (TARGET_SIZE[0] - new_w) // 2
        y = (TARGET_SIZE[1] - new_h) // 2
        final_canvas.paste(img_final, (x, y), mask=img_final)

        final_canvas.save(save_path)
        print(f"处理成功: {os.path.basename(file_path)}")

    except Exception as e:
        print(f"处理出错 {file_path}: {e}")
        import traceback
        traceback.print_exc()

def main():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    print("开始处理...")
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