"""将已审核的龙娘原图转换为窗口图标：python build_icon.py（需要 Pillow）。"""

from pathlib import Path

from PIL import Image, ImageOps

WEB_DIR = Path(__file__).resolve().parent / "ow_desktop" / "web"  # 图标资源目录
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)  # Windows 常用图标尺寸
CANVAS_SIZE = 256  # 最大图标尺寸
CONTENT_SIZE = 232  # 四周预留透明边距，防止龙角贴边


def main() -> None:  # 保留原图比例与透明通道，生成多尺寸窗口图标
    with Image.open(WEB_DIR / "app_icon_source.png") as source:  # 已审核原图
        content = ImageOps.contain(
            source.convert("RGBA"), (CONTENT_SIZE, CONTENT_SIZE),
            method=Image.Resampling.LANCZOS,
        )  # 等比例缩小的图标内容
    image = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE))  # 透明正方形画布
    offset = ((CANVAS_SIZE - content.width) // 2,
              (CANVAS_SIZE - content.height) // 2)  # 居中位置
    image.alpha_composite(content, offset)
    image.save(WEB_DIR / "app_icon.png")
    # WinForms 的 System.Drawing.Icon 对 PNG 编码帧存在兼容问题，使用 BMP 帧避免花屏。
    image.save(WEB_DIR / "app_icon.ico", bitmap_format="bmp",
               sizes=[(size, size) for size in ICON_SIZES])


if __name__ == "__main__":
    main()
