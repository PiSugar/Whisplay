from __future__ import annotations


def draw_rounded_rectangle(draw, bounds, radius, fill, outline=None, width=1):
    """Draw a rounded rectangle on Pillow versions before 8.2 as well."""
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(bounds, radius=radius, fill=fill, outline=outline, width=width)
        return

    def draw_solid(box, corner_radius, color):
        x0, y0, x1, y1 = box
        corner_radius = max(
            0,
            min(int(corner_radius), int((x1 - x0) // 2), int((y1 - y0) // 2)),
        )
        if corner_radius == 0:
            draw.rectangle(box, fill=color)
            return
        draw.rectangle((x0 + corner_radius, y0, x1 - corner_radius, y1), fill=color)
        draw.rectangle((x0, y0 + corner_radius, x1, y1 - corner_radius), fill=color)
        diameter = corner_radius * 2
        draw.ellipse((x0, y0, x0 + diameter, y0 + diameter), fill=color)
        draw.ellipse((x1 - diameter, y0, x1, y0 + diameter), fill=color)
        draw.ellipse((x0, y1 - diameter, x0 + diameter, y1), fill=color)
        draw.ellipse((x1 - diameter, y1 - diameter, x1, y1), fill=color)

    x0, y0, x1, y1 = bounds
    if outline is None:
        draw_solid(bounds, radius, fill)
        return

    outline_width = max(1, int(width))
    draw_solid(bounds, radius, outline)
    if fill is not None and x1 - x0 > outline_width * 2 and y1 - y0 > outline_width * 2:
        inner = (
            x0 + outline_width,
            y0 + outline_width,
            x1 - outline_width,
            y1 - outline_width,
        )
        draw_solid(inner, max(0, radius - outline_width), fill)
