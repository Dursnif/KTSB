from adapters.display.bridge_picture import BridgePictureProvider


class ChromecastDisplayProvider(BridgePictureProvider):
    """Full-screen pictures over Cast — sent by the voice bridge (P139 phase 3; the API has no pychromecast)."""

    channel = "cast"
