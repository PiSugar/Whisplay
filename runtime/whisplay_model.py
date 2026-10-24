from __future__ import annotations

AUTO_MODEL = "auto"
DEFAULT_MODEL = "whisplay"
WHISPLAY_PLUS_MODEL = "whisplay-plus"
WHISPLAY_JOY_MODEL = "whisplay-joy"
VALID_MODELS = {
    AUTO_MODEL,
    DEFAULT_MODEL,
    WHISPLAY_PLUS_MODEL,
    WHISPLAY_JOY_MODEL,
}

HAT_VENDOR_PATH = "/proc/device-tree/hat/vendor"
HAT_PRODUCT_ID_PATH = "/proc/device-tree/hat/product_id"

PRODUCT_ID_MODELS = {
    0x0001: DEFAULT_MODEL,
    0x0002: WHISPLAY_PLUS_MODEL,
    0x0003: WHISPLAY_JOY_MODEL,
}


def _read_device_tree_string(path: str) -> str:
    try:
        with open(path, "rb") as fp:
            return fp.read().rstrip(b"\0").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def parse_product_id(value: str) -> int | None:
    try:
        return int(str(value).strip(), 0)
    except (TypeError, ValueError):
        return None


def detect_hat_model(
    vendor_path: str = HAT_VENDOR_PATH,
    product_id_path: str = HAT_PRODUCT_ID_PATH,
) -> str:
    """Return the model encoded by a PiSugar HAT EEPROM.

    Unknown, missing, and non-PiSugar EEPROMs intentionally fall back to the
    original Whisplay model so installations without a programmed EEPROM keep
    working as before.
    """
    vendor = _read_device_tree_string(vendor_path)
    if vendor.casefold() != "pisugar":
        return DEFAULT_MODEL
    product_id = parse_product_id(_read_device_tree_string(product_id_path))
    return PRODUCT_ID_MODELS.get(product_id, DEFAULT_MODEL)


def resolve_model(configured_model: object = AUTO_MODEL) -> tuple[str, str]:
    """Resolve a configured model to ``(effective_model, source)``.

    ``auto`` (and an omitted/empty setting) reads the HAT EEPROM. Any valid
    concrete model is a user override.
    """
    text = str(configured_model or AUTO_MODEL).strip().lower()
    if text not in VALID_MODELS:
        text = AUTO_MODEL
    if text == AUTO_MODEL:
        return detect_hat_model(), "eeprom"
    return text, "config"
