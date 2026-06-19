from PIL import Image, ImageDraw, ImageFont
import argparse
import sys
import os

def load_font(font_path, font_size):
    if font_size <= 0:
        raise ValueError("font-size must be a positive integer")
    if font_path is not None:
        if not os.path.isfile(font_path):
            print(f"Warning: font file '{font_path}' not found, falling back to default font.", file=sys.stderr)
        else:
            return ImageFont.truetype(font_path, font_size)
    return ImageFont.load_default()


def get_text_bounding_box(draw, text, font, xy):
    return draw.textbbox(xy, text, font=font)


def resolve_position(position, image_size, text_size):
    w, h = image_size
    tw, th = text_size
    px, py = position
    x = int(w * px / 100) - tw // 2
    y = int(h * py / 100) - th // 2
    return (x, y)


def sample_average_color(image, bbox):
    left = max(0, bbox[0])
    top = max(0, bbox[1])
    right = min(image.width, bbox[2])
    bottom = min(image.height, bbox[3])
    if right <= left or bottom <= top:
        return (128, 128, 128)
    region = image.crop((left, top, right, bottom)).convert("RGB")
    avg = region.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    return (avg[0], avg[1], avg[2])


def compute_luminance(color):
    def linearize(c):
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = linearize(color[0]), linearize(color[1]), linearize(color[2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def pick_contrast_color(background):
    return (0, 0, 0) if compute_luminance(background) > 0.179 else (255, 255, 255)


def lerp_color(color_a, color_b, t):
    t = max(0.0, min(1.0, t))
    return (
        int(color_a[0] + (color_b[0] - color_a[0]) * t),
        int(color_a[1] + (color_b[1] - color_a[1]) * t),
        int(color_a[2] + (color_b[2] - color_a[2]) * t),
    )


def parse_position(value):
    try:
        parts = value.split(",")
        if len(parts) == 2:
            x, y = float(parts[0].strip()), float(parts[1].strip())
            if not (0.0 <= x <= 100.0 and 0.0 <= y <= 100.0):
                raise argparse.ArgumentTypeError("Position percentages must be between 0 and 100.")
            return (x, y)
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(
        "Position must be 'x,y' percentages between 0 and 100 (e.g. '50,50' for center)."
    )


def add_text_to_image(image_path, text, position, similarity, font_path, font_size, output_path):
    if not 0.0 <= similarity <= 1.0:
        raise ValueError("similarity must be between 0.0 and 1.0")

    image = Image.open(image_path).convert("RGB")
    font = load_font(font_path, font_size)
    draw = ImageDraw.Draw(image)

    temp_bbox = get_text_bounding_box(draw, text, font, (0, 0))
    text_size = (temp_bbox[2] - temp_bbox[0], temp_bbox[3] - temp_bbox[1])
    xy = resolve_position(position, (image.width, image.height), text_size)
    final_bbox = get_text_bounding_box(draw, text, font, xy)

    if final_bbox[0] < 0 or final_bbox[1] < 0 or final_bbox[2] > image.width or final_bbox[3] > image.height:
        print("Warning: text extends beyond image bounds and will be clipped.", file=sys.stderr)

    avg_bg = sample_average_color(image, final_bbox)
    contrast = pick_contrast_color(avg_bg)
    text_color = lerp_color(contrast, avg_bg, similarity)

    draw.text(xy, text, font=font, fill=text_color)
    image.save(output_path)
    print(f"Saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        prog="image_text_blend",
        description="Add text to an image with a color that blends into the background.",
    )
    parser.add_argument("image", help="Path to the input image file.")
    parser.add_argument("text", help="Text string to render onto the image.")
    parser.add_argument(
        "--position", "-p",
        type=parse_position,
        default=(50.0, 50.0),
        metavar="X,Y",
        help=(
            "Text center position as percentages of image dimensions, e.g. '50,50' "
            "places the text center at the middle of the image. Default: 50,50."
        ),
    )
    parser.add_argument(
        "--similarity", "-s",
        type=float,
        default=0.0,
        metavar="FLOAT",
        help="0.0 = max contrast/readable (default), 1.0 = matches background (invisible).",
    )
    parser.add_argument(
        "--font",
        default=None,
        metavar="PATH",
        help="Path to a .ttf or .otf font file. Falls back to PIL default if not provided.",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=36,
        metavar="INT",
        help="Font size in points (only applies when --font is used). Default: 36.",
    )
    parser.add_argument(
        "--output", "-o",
        default="output.png",
        metavar="PATH",
        help="Output file path. Default: output.png.",
    )

    args = parser.parse_args()
    try:
        add_text_to_image(
            image_path=args.image,
            text=args.text,
            position=args.position,
            similarity=args.similarity,
            font_path=args.font,
            font_size=args.font_size,
            output_path=args.output,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
