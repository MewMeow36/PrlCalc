#!/usr/bin/env python3
"""pearlcalc desktop app: frameless native window (WebView2), no browser needed.

The window has no OS title bar; the app's own header acts as one (drag zone +
minimize/maximize/close buttons), active only when running under pywebview.

Run directly:  python app.py
Build exe:     powershell -ExecutionPolicy Bypass -File build-desktop.ps1
"""
import threading

import webview

import pearlcalc


class Api:
    def __init__(self):
        self.maxed = False

    def min(self):
        webview.windows[0].minimize()

    def max(self):
        w = webview.windows[0]
        if self.maxed:
            w.restore()
        else:
            w.maximize()
        self.maxed = not self.maxed

    def close(self):
        webview.windows[0].destroy()


def main():
    cfg = pearlcalc.load_config()
    server = pearlcalc.make_server(0, cfg)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webview.create_window(
        "pearlcalc",
        f"http://127.0.0.1:{port}/",
        width=1400,
        height=920,
        min_size=(960, 640),
        frameless=True,
        easy_drag=False,  # only .pywebview-drag-region elements move the window
        js_api=Api(),
    )
    webview.start()


if __name__ == "__main__":
    main()
