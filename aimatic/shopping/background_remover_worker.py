"""Standalone image processor executed outside the Frappe Python environment."""

import argparse
from pathlib import Path

from PIL import Image, ImageOps
from rembg import new_session, remove

Image.MAX_IMAGE_PIXELS = 40_000_000


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("input")
	parser.add_argument("output")
	parser.add_argument("--model", default="u2netp")
	args = parser.parse_args()

	input_path = Path(args.input).resolve(strict=True)
	output_path = Path(args.output).resolve()
	output_path.parent.mkdir(parents=True, exist_ok=True)

	with Image.open(input_path) as source:
		if source.width * source.height > Image.MAX_IMAGE_PIXELS:
			raise ValueError("Image dimensions exceed the 40 megapixel safety limit")
		image = ImageOps.exif_transpose(source).convert("RGBA")
		# Very large phone photos waste CPU and memory without improving storefront cards.
		image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
		result = remove(image, session=new_session(args.model), alpha_matting=False)
		result.save(output_path, "PNG", optimize=True)


if __name__ == "__main__":
	main()
