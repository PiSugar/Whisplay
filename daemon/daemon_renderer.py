import os
import time

from PIL import Image, ImageDraw, ImageFont

from daemon_shared import SCREEN_HEIGHT, SCREEN_WIDTH, calculate_luminance, image_to_rgb565_bytes


STATUS_ICON_HEIGHT = 15
TITLE_FONT_SIZE = 17
LIST_ITEM_ROW_HEIGHT = 40
LIST_ITEM_META_OFFSET = 20
LIST_VISIBLE_COUNT = 3
WIFI_ICON_SCALE = 1.6
WIFI_LEVEL_ICON_FILES = {
    1: "wifi-weak.png",
    2: "wifi-medium.png",
    3: "wifi-strong.png",
}


class DesktopRenderer:
    def __init__(self, board, script_dir: str):
        self.board = board
        self.script_dir = script_dir
        self.title_font = self._load_font(TITLE_FONT_SIZE)
        self.body_font = self._load_font(16)
        self.small_font = self._load_font(14)
        self.battery_font = self._load_font(13)
        self.icon_dir = os.path.join(script_dir, "img")
        self._icon_cache: dict[tuple[str, int], Image.Image | None] = {}
        self.zoom_sizes = {
            -2: self._load_font(12),
            -1: self._load_font(14),
            0: self._load_font(18),
            1: self._load_font(14),
            2: self._load_font(12),
        }

    def _load_font(self, size: int):
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _load_status_icon(self, icon_name: str, target_height: int, scale: float = 1.0) -> Image.Image | None:
        scale = scale if scale > 0 else 1.0
        cache_key = (icon_name, target_height, round(scale, 4))
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]
        icon_path = os.path.join(self.icon_dir, icon_name)
        if not os.path.exists(icon_path):
            self._icon_cache[cache_key] = None
            return None
        try:
            source = Image.open(icon_path).convert("RGBA")
            src_width, src_height = source.size
            if src_height <= 0:
                self._icon_cache[cache_key] = None
                return None
            scaled_height = max(1, int(round(target_height * scale)))
            scaled_width = max(1, int(round(src_width * scaled_height / src_height)))
            resized = source.resize((scaled_width, scaled_height), Image.LANCZOS)
        except Exception:
            resized = None
        self._icon_cache[cache_key] = resized
        return resized

    def _battery_fill_color(self, battery_level: int) -> tuple[int, int, int]:
        if battery_level >= 70:
            return (85, 255, 0)
        if battery_level >= 35:
            return (255, 196, 79)
        return (255, 107, 107)

    def _draw_battery_icon(self, draw: ImageDraw.ImageDraw, x: int, y: int, battery_level: int) -> int:
        level = max(0, min(100, int(battery_level)))
        body_width = 26
        body_height = STATUS_ICON_HEIGHT
        head_width = 2
        head_height = 5
        line_width = 2
        corner_radius = 3
        outline = (255, 255, 255)
        fill_color = self._battery_fill_color(level)

        draw.arc((x, y, x + 2 * corner_radius, y + 2 * corner_radius), 180, 270, fill=outline, width=line_width)
        draw.arc((x + body_width - 2 * corner_radius, y, x + body_width, y + 2 * corner_radius), 270, 0, fill=outline, width=line_width)
        draw.arc((x, y + body_height - 2 * corner_radius, x + 2 * corner_radius, y + body_height), 90, 180, fill=outline, width=line_width)
        draw.arc(
            (x + body_width - 2 * corner_radius, y + body_height - 2 * corner_radius, x + body_width, y + body_height),
            0,
            90,
            fill=outline,
            width=line_width,
        )
        draw.line([(x + corner_radius, y), (x + body_width - corner_radius, y)], fill=outline, width=line_width)
        draw.line([(x + corner_radius, y + body_height), (x + body_width - corner_radius, y + body_height)], fill=outline, width=line_width)
        draw.line([(x, y + corner_radius), (x, y + body_height - corner_radius)], fill=outline, width=line_width)
        draw.line([(x + body_width, y + corner_radius), (x + body_width, y + body_height - corner_radius)], fill=outline, width=line_width)
        draw.rectangle(
            [x + line_width // 2, y + line_width // 2, x + body_width - line_width // 2, y + body_height - line_width // 2],
            fill=fill_color,
        )

        head_x = x + body_width
        head_y = y + (body_height - head_height) // 2
        draw.rectangle([head_x, head_y, head_x + head_width, head_y + head_height], fill=outline)

        label = str(level)
        bbox = self.battery_font.getbbox(label)
        text_w = bbox[2] - bbox[0]
        text_y = y + (body_height - (self.battery_font.getmetrics()[0] + self.battery_font.getmetrics()[1])) // 2
        text_x = x + (body_width - text_w) // 2
        text_color = (0, 0, 0) if calculate_luminance(fill_color) > 128 else (255, 255, 255)
        draw.text((text_x, text_y), label, font=self.battery_font, fill=text_color)
        return body_width + head_width

    def _draw_status_icons(self, draw: ImageDraw.ImageDraw, wifi_signal_level: int | None, battery_level: int | None):
        items: list[tuple[str, object]] = []
        if isinstance(battery_level, int):
            items.append(("battery", max(0, min(100, battery_level))))
        if isinstance(wifi_signal_level, int) and wifi_signal_level in WIFI_LEVEL_ICON_FILES:
            icon = self._load_status_icon(
                WIFI_LEVEL_ICON_FILES[wifi_signal_level],
                STATUS_ICON_HEIGHT,
                scale=WIFI_ICON_SCALE,
            )
            if icon is not None:
                items.append(("wifi", icon))
        if not items:
            return

        right_margin = 10
        icon_gap = 8
        cursor_x = SCREEN_WIDTH - right_margin
        for kind, payload in items:
            if kind == "battery":
                width = 28
                icon_x = cursor_x - width
                self._draw_battery_icon(draw, icon_x, 10, payload)
            else:
                width = payload.width
                icon_x = cursor_x - width
                draw._image.paste(payload, (icon_x, 7), payload)
            cursor_x = icon_x - icon_gap

    def _draw_legend_pill(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        width: int,
        height: int,
        fill: tuple[int, int, int],
    ) -> int:
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=height // 2,
            fill=fill,
        )
        return width

    def _draw_desktop_legend(self, draw: ImageDraw.ImageDraw, x: int, y: int):
        legend_color = (60, 90, 100)
        label_gap = 4
        group_gap = 12
        dot_size = 8
        pill_w = 18
        pill_h = 8

        cursor_x = x
        cursor_x += self._draw_legend_pill(draw, cursor_x, y + 4, dot_size, dot_size, legend_color)
        cursor_x += label_gap
        draw.text((cursor_x, y), "next", fill=legend_color, font=self.small_font)
        cursor_x += self.small_font.getbbox("next")[2] + group_gap

        cursor_x += self._draw_legend_pill(draw, cursor_x, y + 4, pill_w, pill_h, legend_color)
        cursor_x += label_gap
        draw.text((cursor_x, y), "open", fill=legend_color, font=self.small_font)
        cursor_x += self.small_font.getbbox("open")[2] + group_gap

        for index in range(4):
            cursor_x += self._draw_legend_pill(draw, cursor_x, y + 4, dot_size, dot_size, legend_color)
            if index != 3:
                cursor_x += 3
        cursor_x += label_gap
        draw.text((cursor_x, y), "home", fill=legend_color, font=self.small_font)

    def render(
        self,
        apps,
        selected_index: int,
        pending_app_id: str | None = None,
        running_app_id: str | None = None,
        wifi_signal_level: int | None = None,
        battery_level: int | None = None,
    ):
        image = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (7, 11, 18))
        draw = ImageDraw.Draw(image)
        self._draw_status_icons(draw, wifi_signal_level, battery_level)

        top_margin = 6
        left = 14
        draw.text((left, top_margin), "whisplay", fill=(255, 255, 255), font=self.title_font)
        self._draw_desktop_legend(draw, left, top_margin + 25)

        if not apps:
            draw.text((left, top_margin + 48), "No apps registered", fill=(255, 200, 120), font=self.body_font)
            frame = image_to_rgb565_bytes(image)
            self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, frame)
            return

        selected = apps[selected_index % len(apps)]
        status = "running" if selected.is_running() else "stopped"
        selected_color = (120, 255, 140) if pending_app_id == selected.app_id else (80, 160, 255)
        draw.text((left, top_margin + 46), selected.display_name, fill=selected_color, font=self.body_font)
        draw.text((left, top_margin + 68), f"State: {status}", fill=(170, 220, 170), font=self.small_font)

        y = top_margin + 112
        total = len(apps)
        display_items = []
        for offset in range(-2, 3):
            idx = (selected_index + offset) % total
            display_items.append((apps[idx], idx, offset))
        arrow_x = left
        text_x = left + 18
        for app, idx, offset in display_items:
            font = self.zoom_sizes.get(offset, self.small_font)
            if pending_app_id == app.app_id:
                item_color = (120, 255, 140)
            elif offset == 0:
                item_color = (255, 255, 255)
            elif abs(offset) == 1:
                item_color = (160, 170, 190)
            else:
                item_color = (100, 110, 130)
            if idx == selected_index:
                draw.text((arrow_x, y), ">", fill=item_color, font=font)
            draw.text((text_x, y), app.display_name, fill=item_color, font=font)
            y += 22

        modal_app_id = pending_app_id or running_app_id
        if modal_app_id:
            modal_w, modal_h = 188, 64
            modal_x = (SCREEN_WIDTH - modal_w) // 2
            modal_y = (SCREEN_HEIGHT - modal_h) // 2
            draw.rounded_rectangle(
                (modal_x, modal_y, modal_x + modal_w, modal_y + modal_h),
                radius=10,
                fill=(16, 28, 40),
                outline=(90, 150, 200),
                width=2,
            )
            modal_title = "Opening app..." if pending_app_id else "App running..."
            draw.text((modal_x + 14, modal_y + 12), modal_title, fill=(255, 255, 255), font=self.body_font)
            draw.text((modal_x + 14, modal_y + 36), modal_app_id, fill=(120, 255, 140), font=self.small_font)
            spinner_frames = ["|", "/", "-", "\\"]
            spinner = spinner_frames[int(time.time() * 8) % len(spinner_frames)]
            draw.text((modal_x + modal_w - 22, modal_y + 12), spinner, fill=(120, 220, 255), font=self.body_font)

        frame = image_to_rgb565_bytes(image)
        self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, frame)

    def render_internal_app(self, view_model: dict):
        kind = view_model.get("kind")
        if kind == "keyboard":
            self._render_keyboard(view_model)
            return
        self._render_list_page(view_model)

    def _draw_internal_legend(self, draw: ImageDraw.ImageDraw, x: int, y: int):
        legend_color = (60, 90, 100)
        label_gap = 4
        group_gap = 12
        dot_size = 8
        pill_w = 18
        pill_h = 8

        cursor_x = x
        cursor_x += self._draw_legend_pill(draw, cursor_x, y + 4, dot_size, dot_size, legend_color)
        cursor_x += label_gap
        draw.text((cursor_x, y), "next", fill=legend_color, font=self.small_font)
        cursor_x += self.small_font.getbbox("next")[2] + group_gap

        cursor_x += self._draw_legend_pill(draw, cursor_x, y + 4, pill_w, pill_h, legend_color)
        cursor_x += label_gap
        draw.text((cursor_x, y), "select", fill=legend_color, font=self.small_font)

    def _render_list_page(self, view_model: dict):
        image = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (9, 14, 24))
        draw = ImageDraw.Draw(image)

        title = str(view_model.get("title") or "System")
        subtitle = str(view_model.get("subtitle") or "")
        items = list(view_model.get("items") or [])
        selected_index = int(view_model.get("selected_index") or 0)
        status = str(view_model.get("status") or "")
        busy = bool(view_model.get("busy"))
        detail_lines = list(view_model.get("detail_lines") or [])

        left = 14
        top = 10
        draw.text((left, top), title, fill=(255, 255, 255), font=self.title_font)
        self._draw_internal_legend(draw, left, top + 24)
        if subtitle:
            draw.text((left, top + 42), subtitle[:30], fill=(126, 162, 200), font=self.small_font)

        card_y = 68
        card_h = LIST_VISIBLE_COUNT * LIST_ITEM_ROW_HEIGHT + 16
        draw.rounded_rectangle((10, card_y, SCREEN_WIDTH - 10, card_y + card_h), radius=12, fill=(15, 24, 36))
        if not items:
            draw.text((left, card_y + 20), "No items", fill=(255, 200, 120), font=self.body_font)
        else:
            selected_index = max(0, min(selected_index, len(items) - 1))
            visible_count = min(LIST_VISIBLE_COUNT, len(items))
            start_index = max(0, min(selected_index, len(items) - visible_count))
            y = card_y + 10
            for idx in range(start_index, start_index + visible_count):
                item = items[idx]
                item_title = str(item.get("title") or "")
                item_meta = str(item.get("meta") or "")
                if idx == selected_index:
                    color = (255, 255, 255)
                    meta_color = (130, 224, 170)
                    font = self.body_font
                    draw.text((left, y), ">", fill=color, font=font)
                elif abs(idx - selected_index) == 1:
                    color = (176, 186, 208)
                    meta_color = (100, 124, 148)
                    font = self.small_font
                else:
                    color = (106, 118, 138)
                    meta_color = (82, 92, 108)
                    font = self.small_font
                draw.text((left + 18, y), item_title[:22], fill=color, font=font)
                if item_meta:
                    draw.text(
                        (left + 18, y + LIST_ITEM_META_OFFSET),
                        item_meta[:34],
                        fill=meta_color,
                        font=self.small_font,
                    )
                y += LIST_ITEM_ROW_HEIGHT

        status_y = card_y + card_h + 12
        draw.rounded_rectangle((10, status_y, SCREEN_WIDTH - 10, status_y + 42), radius=10, fill=(18, 30, 44))
        status_fill = (255, 218, 96) if busy else (156, 214, 255)
        draw.text((left, status_y + 6), "Status", fill=(255, 255, 255), font=self.small_font)
        if detail_lines:
            draw.text((left, status_y + 18), detail_lines[0][:30], fill=status_fill, font=self.small_font)
            if len(detail_lines) > 1:
                draw.text((left, status_y + 30), detail_lines[1][:30], fill=(190, 220, 255), font=self.small_font)
        else:
            draw.text((left, status_y + 20), status[:30], fill=status_fill, font=self.small_font)
        frame = image_to_rgb565_bytes(image)
        self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, frame)

    def _render_keyboard(self, view_model: dict):
        image = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (11, 16, 26))
        draw = ImageDraw.Draw(image)

        title = str(view_model.get("title") or "Keyboard")
        subtitle = str(view_model.get("subtitle") or "")
        password = str(view_model.get("password") or "")
        password_length = int(view_model.get("password_length") or 0)
        status = str(view_model.get("status") or "")

        left = 14
        top = 10
        draw.text((left, top), title, fill=(255, 255, 255), font=self.title_font)
        draw.text((left, top + 22), subtitle[:24], fill=(130, 180, 230), font=self.small_font)

        draw.rounded_rectangle((10, 56, SCREEN_WIDTH - 10, 104), radius=12, fill=(18, 28, 42))
        draw.text((left, 64), "Password", fill=(255, 255, 255), font=self.small_font)
        display_password = password[-24:] if password else "<empty>"
        draw.text((left, 82), display_password, fill=(120, 255, 140), font=self.body_font)

        draw.rounded_rectangle((10, 116, SCREEN_WIDTH - 10, 196), radius=12, fill=(17, 28, 39))
        draw.text((left, 126), "External keyboard", fill=(255, 255, 255), font=self.small_font)
        draw.text((left, 148), f"{password_length} chars typed", fill=(255, 214, 94), font=self.title_font)
        draw.text((left, 176), "Enter connect  Esc cancel", fill=(118, 136, 156), font=self.small_font)

        draw.rounded_rectangle((10, 208, SCREEN_WIDTH - 10, 250), radius=10, fill=(18, 30, 44))
        draw.text((left, 216), status[:30], fill=(156, 214, 255), font=self.small_font)
        draw.text((left, 258), "Backspace delete", fill=(90, 106, 124), font=self.small_font)

        frame = image_to_rgb565_bytes(image)
        self.board.draw_image(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, frame)
