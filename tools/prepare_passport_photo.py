from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter


SOURCE = Path(r"C:\Users\MiniPC\Desktop\KakaoTalk_20260908_123117409.jpg")
OUTPUT = Path(r"C:\Users\MiniPC\Desktop\passport_photo_413x531.jpg")
TARGET_SIZE = (413, 531)


def main() -> None:
    with Image.open(SOURCE) as source:
        image = source.convert("RGB")

    target_ratio = TARGET_SIZE[0] / TARGET_SIZE[1]
    crop_width = round(image.height * target_ratio)
    left = (image.width - crop_width) // 2
    image = image.crop((left, 0, left + crop_width, image.height))
    image = image.resize(TARGET_SIZE, Image.Resampling.LANCZOS)
    image = ImageEnhance.Brightness(image).enhance(1.08)
    image = ImageEnhance.Contrast(image).enhance(1.03)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=80, threshold=3))
    image.save(OUTPUT, "JPEG", quality=95, subsampling=0, optimize=True)

    print(f"Created: {OUTPUT}")
    print(f"Size: {image.size[0]} x {image.size[1]} px")


if __name__ == "__main__":
    main()