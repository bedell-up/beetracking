"""
AprilTag Sheet Generator for Bee Tracking
==========================================
Generates a printable PDF sheet of unique AprilTags
sized for attachment to Bombus vosnesenskii thorax.

Usage:
    python generate_tags.py --count 64 --size 5
    (generates 64 tags at 5mm each on a printable A4 sheet)

Output:
    ../tags/apriltags_5mm.pdf   — print at 100% scale, no scaling
    ../tags/tag_manifest.csv    — maps tag ID to bee assignment (fill in field)

Requirements:
    pip install opencv-contrib-python numpy reportlab
"""

import cv2
import numpy as np
import csv
import os
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TAGS_DIR = os.path.join(BASE_DIR, "..", "tags")

# AprilTag 36h11 — same dictionary as the detector
ARUCO_DICT = cv2.aruco.DICT_APRILTAG_36h11


def generate_tag_image(tag_id, size_px):
    """Generate a single AprilTag as a numpy array."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    tag_img = np.zeros((size_px, size_px), dtype=np.uint8)
    tag_img = cv2.aruco.generateImageMarker(aruco_dict, tag_id, size_px, tag_img, 1)
    return tag_img


def generate_pdf(count, size_mm):
    """
    Generate a printable A4 PDF sheet of AprilTags.
    Tags have a white border for gluing and a visible ID number below.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.utils import ImageReader
        from PIL import Image
        import io
    except ImportError:
        print("[ERROR] Missing dependencies. Run:")
        print("  pip install reportlab Pillow")
        return

    os.makedirs(TAGS_DIR, exist_ok=True)

    page_w, page_h = A4  # 595 x 842 pts
    margin = 10 * mm
    label_height = 4 * mm
    cell_size = size_mm * mm + 2 * mm  # tag + small white border
    row_height = cell_size + label_height + 1 * mm

    cols = int((page_w - 2 * margin) / cell_size)
    rows = int((page_h - 2 * margin) / row_height)
    per_page = cols * rows

    pdf_path = os.path.join(TAGS_DIR, f"apriltags_{size_mm}mm.pdf")
    c = rl_canvas.Canvas(pdf_path, pagesize=A4)
    c.setFont("Helvetica", 5)

    tag_id = 0
    page = 0

    while tag_id < count:
        if tag_id > 0 and tag_id % per_page == 0:
            c.showPage()
            page += 1
            c.setFont("Helvetica", 5)

        pos_on_page = tag_id % per_page
        col = pos_on_page % cols
        row = pos_on_page // cols

        x = margin + col * cell_size
        y = page_h - margin - (row + 1) * row_height

        # Generate tag image
        tag_px = 200  # high-res internal generation, scaled down for print
        tag_img = generate_tag_image(tag_id, tag_px)

        # White background cell
        c.setFillColorRGB(1, 1, 1)
        c.rect(x, y, cell_size, cell_size + label_height, fill=1, stroke=0)

        # Draw tag using Pillow as intermediary
        pil_img = Image.fromarray(tag_img)
        pil_img = pil_img.convert("RGB")
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        img_reader = ImageReader(buf)

        c.drawImage(img_reader, x + 1 * mm, y + label_height + 0.5 * mm,
                    width=size_mm * mm, height=size_mm * mm)

        # Tag ID label below the tag
        c.setFillColorRGB(0, 0, 0)
        c.drawCentredString(x + cell_size / 2, y + 1 * mm, f"ID:{tag_id:03d}")

        tag_id += 1

    # Print instructions on final page
    c.showPage()
    c.setFont("Helvetica-Bold", 12)
    c.drawString(30, 800, "Bee AprilTag Application Instructions")
    c.setFont("Helvetica", 10)
    instructions = [
        "1. Print this sheet at EXACTLY 100% scale (no 'fit to page').",
        "   Verify tag size with a ruler before cutting.",
        f"  Each tag should measure {size_mm}mm × {size_mm}mm.",
        "",
        "2. Cut out individual tags. Keep the white border — it helps detection.",
        "",
        "3. Chill bees briefly (ice pack in net, ~2 min) until motionless.",
        "",
        "4. Apply a TINY amount of superglue (Loctite Gel) to the tag.",
        "   Attach to the dorsal thorax. Do not cover wings or eyes.",
        "",
        "5. Hold in place ~10 seconds. Release bee once moving normally.",
        "",
        "6. Record tag ID, capture site, date, and any notes in tag_manifest.csv.",
        "",
        "7. Allow ~5 min observation before considering bee fully released.",
        "",
        "NOTE: Tags ≤5mm have been used successfully on Bombus workers",
        "without significant effect on flight (Couvillon et al. 2012).",
    ]
    y = 775
    for line in instructions:
        c.drawString(30, y, line)
        y -= 16

    c.save()
    print(f"[PDF] Saved → {pdf_path}  ({count} tags, {size_mm}mm, {page+2} pages)")
    return pdf_path


def generate_manifest(count):
    """Create a blank CSV manifest for field bee assignment."""
    os.makedirs(TAGS_DIR, exist_ok=True)
    manifest_path = os.path.join(TAGS_DIR, "tag_manifest.csv")

    with open(manifest_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "tag_id", "capture_site", "capture_date", "capture_time",
            "bee_sex", "bee_size_estimate", "notes", "assigned_to_researcher"
        ])
        for tag_id in range(count):
            writer.writerow([tag_id, "", "", "", "", "", "", ""])

    print(f"[MANIFEST] Saved → {manifest_path}")
    return manifest_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate AprilTag print sheet")
    parser.add_argument("--count", type=int, default=64,
                        help="Number of unique tags to generate (default: 64)")
    parser.add_argument("--size", type=int, default=5,
                        help="Tag size in mm (default: 5mm — good for B. vosnesenskii)")
    args = parser.parse_args()

    print(f"\nGenerating {args.count} tags at {args.size}mm...\n")
    generate_pdf(args.count, args.size)
    generate_manifest(args.count)
    print("\nDone. Print the PDF at 100% scale and verify size with a ruler.")
