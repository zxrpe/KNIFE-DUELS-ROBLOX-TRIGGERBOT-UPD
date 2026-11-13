"""Crop a template image for triggerbot.py - zoomable, draw-your-own-box.

    py -3.12 snip.py                          # screenshot after 3s
    py -3.12 snip.py screenshot.png
    py -3.12 snip.py screenshot.png --box x0,y0,x1,y1   # no GUI

The image is scaled up to fill the screen, so you can see what you are doing.

    left-drag    draw the box around the enemy
    wheel        zoom in/out around the cursor
    right-drag   pan
    R            reset zoom/position
    Enter        save template.png
    Esc          cancel

A zoomed preview of the crop is shown bottom-right, so you can verify it is
really the model and not background before saving.
"""

import argparse
import ctypes
import os
import sys
import tempfile
import time

import cv2
import mss
import numpy as np
import tkinter as tk

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(BASE, "template.png")
GRAB_DELAY = 3.0
MIN_SIDE = 12
PREVIEW = 200
MIN_ZOOM, MAX_ZOOM = 0.25, 8.0


def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def grab_screen():
    with mss.mss() as sct:
        mon = sct.monitors[1]
        return np.array(sct.grab(mon))[:, :, :3]


def write_ppm(path, bgr):
    """tkinter reads PPM natively, so no imaging library is needed."""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    with open(path, "wb") as f:
        f.write(f"P6\n{w} {h}\n255\n".encode("ascii"))
        f.write(rgb.tobytes())


def parse_box(text):
    try:
        return tuple(int(v) for v in text.split(","))
    except ValueError:
        raise SystemExit("--box must look like x0,y0,x1,y1")


def save_crop(img, box, out):
    x0, y0, x1, y1 = box
    crop = img[y0:y1, x0:x1].copy()
    cv2.imwrite(out, crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    print(f"saved {out}: {crop.shape[1]}x{crop.shape[0]} px, "
          f"gray std {gray.std():.1f}, box x {x0}-{x1} y {y0}-{y1}")
    return crop


class Cropper:
    def __init__(self, root, img, out_path):
        self.root = root
        self.img = img
        self.out_path = out_path
        self.ih, self.iw = img.shape[:2]
        self.sw = root.winfo_screenwidth()
        self.sh = root.winfo_screenheight()

        self.base = min(self.sw / self.iw, self.sh / self.ih)
        self.zoom = 1.0
        self.scale = self.base
        self.ox = (self.sw - self.iw * self.scale) / 2.0
        self.oy = (self.sh - self.ih * self.scale) / 2.0

        self.selection = None      # (x0, y0, x1, y1) in image pixels
        self.drag_from = None
        self.pan_from = None
        self.preview_img = None
        self.preview_path = os.path.join(tempfile.gettempdir(),
                                         "triggerbot_preview.ppm")
        self.image_item = None
        self.hint = ("drag = select   wheel = zoom   right-drag = pan   "
                     "R = reset   Enter = save   Esc = cancel")

        self.canvas = tk.Canvas(root, highlightthickness=0, bg="#141414")
        self.canvas.pack(fill="both", expand=True)
        self.status = self.canvas.create_text(
            self.sw // 2, 20, fill="#00ff88", font=("Consolas", 13),
            text=self.hint)

        c = self.canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-3>", self.on_pan_start)
        c.bind("<B3-Motion>", self.on_pan)
        c.bind("<MouseWheel>", self.on_wheel)
        root.bind("<Return>", self.on_enter)
        root.bind("<KP_Enter>", self.on_enter)
        root.bind("<Escape>", lambda _e: (print("cancelled"),
                                          self.root.destroy()))
        root.bind("<r>", lambda _e: self.reset_view())
        root.bind("<R>", lambda _e: self.reset_view())

        self.render()

    # ---- coordinate helpers -----------------------------------------
    def to_image(self, x, y):
        ix = int(round((x - self.ox) / self.scale))
        iy = int(round((y - self.oy) / self.scale))
        return max(0, min(ix, self.iw - 1)), max(0, min(iy, self.ih - 1))

    def to_screen(self, x, y):
        return self.ox + x * self.scale, self.oy + y * self.scale

    # ---- rendering ---------------------------------------------------
    def render(self):
        c = self.canvas
        self.scale = max(0.05, self.base * self.zoom)
        dw = max(1, int(self.iw * self.scale))
        dh = max(1, int(self.ih * self.scale))
        interp = cv2.INTER_NEAREST if self.scale >= 1.0 else cv2.INTER_AREA
        disp = cv2.resize(self.img, (dw, dh), interpolation=interp)
        self.tmp = os.path.join(tempfile.gettempdir(), "triggerbot_snip.ppm")
        write_ppm(self.tmp, disp)
        self.tkimg = tk.PhotoImage(file=self.tmp)

        if self.image_item is not None:
            c.delete(self.image_item)
        self.image_item = c.create_image(self.ox, self.oy, anchor="nw",
                                         image=self.tkimg)
        self.redraw_overlay()
        self.update_status()

    def redraw_overlay(self):
        c = self.canvas
        c.delete("overlay")
        if self.selection is None:
            return
        x0, y0, x1, y1 = self.selection
        sx0, sy0 = self.to_screen(x0, y0)
        sx1, sy1 = self.to_screen(x1, y1)
        c.create_rectangle(sx0, sy0, sx1, sy1, outline="#00ff88", width=2,
                           tags="overlay")
        for hx, hy in ((sx0, sy0), (sx1, sy0), (sx0, sy1), (sx1, sy1)):
            c.create_rectangle(hx - 3, hy - 3, hx + 3, hy + 3,
                               outline="#00ff88", tags="overlay")
        self.draw_preview(x0, y0, x1, y1)

    def draw_preview(self, x0, y0, x1, y1):
        c = self.canvas
        if self.preview_img is not None:
            c.delete("preview")
        crop = self.img[y0:y1, x0:x1]
        if crop.size == 0:
            return
        ch, cw = crop.shape[:2]
        k = min(PREVIEW / cw, PREVIEW / ch)
        pw, ph = max(1, int(cw * k)), max(1, int(ch * k))
        prev = cv2.resize(crop, (pw, ph), interpolation=cv2.INTER_NEAREST)
        write_ppm(self.preview_path, prev)
        self.preview_img = tk.PhotoImage(file=self.preview_path)
        px, py = self.sw - PREVIEW - 24, self.sh - PREVIEW - 24
        c.create_rectangle(px - 4, py - 4, px + PREVIEW + 4, py + PREVIEW + 4,
                           outline="#00ff88", tags="preview")
        c.create_image(px, py, anchor="nw", image=self.preview_img,
                       tags="preview")
        c.create_text(px, py - 16, anchor="nw", fill="#00ff88",
                      font=("Consolas", 10), tags="preview",
                      text=f"saving this: {cw}x{ch} px")

    def update_status(self, extra=""):
        text = self.hint
        if self.selection is not None:
            x0, y0, x1, y1 = self.selection
            pct = 100 * (x1 - x0) * (y1 - y0) / (self.iw * self.ih)
            text = (f"selection {x1-x0}x{y1-y0} px at ({x0},{y0}) "
                    f"= {pct:.0f}% of image | {text}")
        self.canvas.itemconfig(self.status, text=text + extra)

    # ---- events ------------------------------------------------------
    def on_press(self, event):
        self.drag_from = self.to_image(event.x, event.y)
        self.selection = None
        self.canvas.delete("overlay")
        self.canvas.delete("preview")

    def on_drag(self, event):
        if self.drag_from is None:
            return
        x, y = self.to_image(event.x, event.y)
        x0, x1 = sorted((self.drag_from[0], x))
        y0, y1 = sorted((self.drag_from[1], y))
        self.selection = (x0, y0, x1, y1)
        self.canvas.delete("overlay")
        sx0, sy0 = self.to_screen(x0, y0)
        sx1, sy1 = self.to_screen(x1, y1)
        self.canvas.create_rectangle(sx0, sy0, sx1, sy1,
                                     outline="#00ff88", width=2, tags="overlay")
        self.update_status()

    def on_release(self, event):
        if self.drag_from is None:
            return
        self.drag_from = None
        if self.selection is None:
            self.update_status("  -  drag a box first")
            return
        x0, y0, x1, y1 = self.selection
        if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
            self.selection = None
            self.canvas.delete("overlay")
            self.canvas.delete("preview")
            self.update_status(f"  -  {x1-x0}x{y1-y0} px is too small, drag again")
            return
        self.redraw_overlay()
        self.update_status("  -  press Enter to save, drag again to redo")

    def on_pan_start(self, event):
        self.pan_from = (event.x, event.y, self.ox, self.oy)

    def on_pan(self, event):
        if self.pan_from is None:
            return
        px, py, ox, oy = self.pan_from
        self.ox = ox + (event.x - px)
        self.oy = oy + (event.y - py)
        self.render()

    def on_wheel(self, event):
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        if event.delta > 0 and self.zoom >= MAX_ZOOM:
            return
        if event.delta < 0 and self.zoom <= MIN_ZOOM:
            return
        ix, iy = (event.x - self.ox) / self.scale, (event.y - self.oy) / self.scale
        self.zoom = max(MIN_ZOOM, min(self.zoom * factor, MAX_ZOOM))
        self.scale = self.base * self.zoom
        self.ox = event.x - ix * self.scale
        self.oy = event.y - iy * self.scale
        self.render()

    def reset_view(self):
        self.zoom = 1.0
        self.scale = self.base
        self.ox = (self.sw - self.iw * self.scale) / 2.0
        self.oy = (self.sh - self.ih * self.scale) / 2.0
        self.render()

    def on_enter(self, _event=None):
        if self.selection is None:
            self.update_status("  -  drag a box first")
            return
        save_crop(self.img, self.selection, self.out_path)
        self.root.destroy()


def main():
    p = argparse.ArgumentParser(description="Crop a triggerbot template.")
    p.add_argument("source", nargs="?",
                   help="image to crop from (omit to grab the screen)")
    p.add_argument("--box", help="x0,y0,x1,y1 - crop directly, no GUI")
    p.add_argument("--out", default=DEFAULT_OUT)
    args = p.parse_args()

    set_dpi_awareness()

    if args.source:
        source = os.path.abspath(args.source)
        if not os.path.isfile(source):
            raise SystemExit(f"No such image: {source}")
        img = cv2.imread(source, cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"Could not read image: {source}")
        print(f"source {source} ({img.shape[1]}x{img.shape[0]})")
    else:
        print(f"switch to the game - capturing the screen in {GRAB_DELAY:.0f}s...")
        time.sleep(GRAB_DELAY)
        img = grab_screen()

    ih, iw = img.shape[:2]

    if args.box:
        x0, y0, x1, y1 = parse_box(args.box)
        x0, x1 = sorted((max(0, min(x0, iw)), max(0, min(x1, iw))))
        y0, y1 = sorted((max(0, min(y0, ih)), max(0, min(y1, ih))))
        if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
            raise SystemExit(f"box is too small ({x1-x0}x{y1-y0})")
        save_crop(img, (x0, y0, x1, y1), args.out)
        return

    root = tk.Tk()
    root.title("select the enemy - Enter saves, Esc cancels")
    root.attributes("-topmost", True)
    root.attributes("-fullscreen", True)
    root.config(cursor="crosshair")
    Cropper(root, img, args.out)
    root.mainloop()


if __name__ == "__main__":
    main()
