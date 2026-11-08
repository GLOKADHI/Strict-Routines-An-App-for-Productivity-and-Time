"""Measured pixel attributes. Heuristics are never physical material claims."""
import colorsys
from PIL import Image, ImageFilter, ImageStat


def family(rgb: list[int]) -> str:
    h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in rgb))
    if v < 0.18:
        return "black"
    if s < 0.12:
        return "white" if v > 0.88 else "gray"
    deg = (h * 360) % 360
    if deg < 15 or deg >= 345:
        return "red"
    if deg < 45:
        return "orange"
    if deg < 70:
        return "yellow"
    if deg < 165:
        return "green"
    if deg < 195:
        return "cyan"
    if deg < 255:
        return "blue"
    if deg < 290:
        return "purple"
    return "pink"


def colors(image: Image.Image, region: list[int] | None = None) -> list[dict]:
    sample = image.copy() if region is None else image.crop((region[0], region[1], region[0]+region[2], region[1]+region[3]))
    try:
        sample.thumbnail((128, 128))
        if sample.mode == "RGBA":
            # Transparent pixels do not become an invented black background.
            pixels = [p[:3] for p in sample.getdata() if p[3] >= 128]
            if not pixels:
                return []
            rgb_image = Image.new("RGB", (len(pixels), 1))
            rgb_image.putdata(pixels)
        else:
            rgb_image = sample.convert("RGB")
        try:
            quantized = rgb_image.quantize(colors=4, method=Image.Quantize.MEDIANCUT)
            palette = quantized.getpalette()
            counts = sorted(quantized.getcolors() or [], reverse=True)
            total = sum(n for n, _ in counts)
            values = []
            for count, index in counts:
                rgb = palette[index*3:index*3+3]
                values.append({"rgb": rgb, "hex": "#" + "".join(f"{c:02x}" for c in rgb),
                               "family": family(rgb), "fraction": round(count/total, 4),
                               "region": region, "source": "measured_pixels"})
            quantized.close()
            return values
        finally:
            rgb_image.close()
    finally:
        sample.close()


def attributes(image: Image.Image) -> dict:
    gray = image.convert("L")
    gray.thumbnail((256, 256))
    try:
        stat = ImageStat.Stat(gray)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        interior = edges.crop((1, 1, max(2, gray.width-1), max(2, gray.height-1)))
        edge_mean = ImageStat.Stat(interior).mean[0]
        quality = {"mean_luminance": round(stat.mean[0], 2), "luminance_stddev": round(stat.stddev[0], 2),
                   "edge_response": round(edge_mean, 2), "source": "measured_pixels"}
        warnings = []
        if stat.mean[0] < 45:
            warnings.append("Low-light image; colors and recognition may be unreliable.")
        if edge_mean < 2:
            warnings.append("Low edge detail: image may be plain or blurry; these cannot be distinguished reliably.")
        surface = [{"label": "low visible edge detail" if edge_mean < 4 else "visible edge/texture detail",
                    "source": "inferred_from_pixel_statistic", "confidence": None,
                    "uncertainty": "Not a fabric, smoothness, gloss or physical material classification."}]
        # Repeated horizontal/vertical intensity bands support only a tentative visual pattern.
        rows = [ImageStat.Stat(gray.crop((0, y, gray.width, y+1))).mean[0] for y in range(gray.height)]
        cols = [ImageStat.Stat(gray.crop((x, 0, x+1, gray.height))).mean[0] for x in range(gray.width)]
        def bands(values):
            changes = [values[i+1]-values[i] for i in range(len(values)-1)]
            strong = [v for v in changes if abs(v) > 18]
            return len(strong) >= 6 and sum(a*b < 0 for a, b in zip(strong, strong[1:])) >= len(strong)-2
        patterns = []
        if bands(rows) or bands(cols):
            patterns = [{"label": "possible repeating bands", "source": "inferred_from_pixels", "confidence": None,
                         "uncertainty": "May be stripes, scene edges or lighting; does not establish a garment pattern."}]
        interior.close()
        edges.close()
        return {"colors": colors(image), "patterns": patterns, "surface_appearance": surface,
                "possible_materials": [], "quality": quality, "warnings": warnings}
    finally:
        gray.close()


def analyze_clothing(image: Image.Image, objects: list[dict] | None = None) -> dict:
    """Analyze garment/clothing attributes where visually supported.

    Never claims physical material composition (e.g. cotton, silk, wool)
    solely from image pixels.
    """
    image_colors = colors(image)
    main_color = image_colors[0] if image_colors else None
    secondary_color = image_colors[1] if len(image_colors) > 1 and image_colors[1].get("fraction", 0) >= 0.15 else None

    # Identify clothing-related categories if detected in objects
    garment_categories = []
    clothing_coco = {"tie", "handbag", "backpack", "suitcase", "umbrella"}
    if objects:
        for obj in objects:
            lbl = obj.get("label", "").lower()
            if lbl in clothing_coco:
                garment_categories.append(lbl)
            elif lbl == "person":
                garment_categories.append("garment / wearer")

    # Pattern and surface estimation
    gray = image.convert("L")
    gray.thumbnail((256, 256))
    try:
        rows = [ImageStat.Stat(gray.crop((0, y, gray.width, y + 1))).mean[0] for y in range(gray.height)]
        cols = [ImageStat.Stat(gray.crop((x, 0, x + 1, gray.height))).mean[0] for x in range(gray.width)]

        def has_bands(values):
            changes = [values[i + 1] - values[i] for i in range(len(values) - 1)]
            strong = [v for v in changes if abs(v) > 18]
            return len(strong) >= 6 and sum(a * b < 0 for a, b in zip(strong, strong[1:])) >= len(strong) - 2

        row_bands = has_bands(rows)
        col_bands = has_bands(cols)

        edges = gray.filter(ImageFilter.FIND_EDGES)
        interior = edges.crop((1, 1, max(2, gray.width - 1), max(2, gray.height - 1)))
        edge_mean = ImageStat.Stat(interior).mean[0]
        interior.close()
        edges.close()

        if row_bands and col_bands:
            pattern = "checked / grid pattern"
        elif row_bands:
            pattern = "horizontal striped pattern"
        elif col_bands:
            pattern = "vertical striped pattern"
        elif edge_mean >= 15:
            pattern = "textured / patterned"
        else:
            pattern = "plain / uniform"

        if edge_mean < 4:
            surface = "smooth / matte appearance"
        elif edge_mean >= 18:
            surface = "textured / ribbed or knitted appearance"
        else:
            surface = "standard woven / fabric-like surface"

        return {
            "garment_category": garment_categories[0] if garment_categories else ("clothing / garment" if objects else None),
            "detected_categories": garment_categories,
            "main_color": main_color,
            "secondary_color": secondary_color,
            "color_family": main_color["family"] if main_color else None,
            "visible_pattern": {
                "pattern": pattern,
                "source": "measured_pixel_bands",
                "uncertainty": "Visual estimate only; lighting, folds, or background may mimic stripes or checks."
            },
            "surface_appearance": {
                "appearance": surface,
                "source": "measured_edge_response",
                "uncertainty": "Visual surface appearance only; not a physical material test."
            },
            "apparent_fabric_characteristics": {
                "observed_texture": "smooth" if edge_mean < 4 else ("coarse/textured" if edge_mean >= 18 else "fine weave"),
                "physical_material_claim": False,
                "disclaimer": "Physical material composition (such as cotton, silk, wool, or synthetic) cannot be verified from 2D pixel data alone."
            },
            "visible_details": {
                "closures_detected": [],
                "notes": "Fine garment details (buttons, zippers, seams) require high-magnification verification."
            }
        }
    finally:
        gray.close()

