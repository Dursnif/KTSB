from adapters.display.bridge_picture import BridgePictureProvider


class DlnaDisplayProvider(BridgePictureProvider):
    """Full-screen pictures over DLNA — sent by the voice bridge (P139 phase 3)."""

    channel = "dlna"
