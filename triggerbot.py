"""Triggerbot for single-player / offline games.

Watches a small box around the crosshair, matches a template image of the
enemy model (multi-scale / optionally multi-angle), and clicks the mouse
button when the template sits under the crosshair.

Only works against your own single-player session. Do not point this at a
multiplayer game.

Usage:
    python triggerbot.py --window "Roblox" --debug     # dry run, no clicks
    python triggerbot.py --window "Roblox"             # live
    python triggerbot.py --template a.png,b.png            # several poses
    python triggerbot.py --mode color --eager              # guess-and-fire mode

Hotkeys:
    F6   toggle clicking on/off
    F8   save an annotated debug frame right now
    F10  quit
"""

import argparse
import ctypes
import os
import sys
import time

import cv2
import keyboard
import mss
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, "triggerbot_log.txt")
DEBUG_PNG = os.path.join(BASE, "triggerbot_debug.png")

# ---- defaults (all overridable from the command line) -----------------------
THRESHOLD = 0.80       # match score needed to accept a hit (0..1)
SCALES = "0.6,0.75,0.9,1.05,1.2,1.4,1.6"
ROTATIONS = "0"
REGION = "240x240"     # capture box around the crosshair
CONFIRM = 2            # consecutive frames a hit must appear before firing
COOLDOWN = 0.18        # seconds between clicks
CLICK_HOLD = 25        # milliseconds the button stays down
LOOP_SLEEP = 0.0       # extra sleep per frame, seconds
DEBUG_INTERVAL = 2.0   # seconds between saved debug frames
LOG_INTERVAL = 1.0

# ---- colour mode defaults (no template needed) ------------------------------
HEAD_HSV = "18,130,130,40,255,255"     # the enemy's head colour
BODY_HSV = "95,120,80,125,255,255"     # torso colour, looked for below the head
MIN_HEAD_AREA = 150    # px; ignore smaller yellow specks
HEAD_ASPECT = "0.6,1.6"  # width/height range for a head-like blob
MIN_HEAD_WIDTH = 12
BODY_FRAC = 0.30       # share of the band below the head that must be torso colour
PARTIAL_SCORE = 0.40   # confidence when only the head is visible
COLOR_THRESHOLD = 0.30  # below partial score, so partial hits count by default

# ---- eager mode: guess-and-fire at things that might be an enemy -------------
# Detection is relaxed so distant / tiny / half-hidden targets still register,
# but the trigger stays precise: the crosshair must be on the box (within
# --aim-tolerance pixels), and solid, blobby shapes are required. Yellow trim,
# walls and thin strips are rejected by the fill test.
EAGER_SCORE = 0.20     # confidence given to a "might be one" hit
EAGER_MIN_AREA = 50    # px; small enough for a distant head
EAGER_MIN_WIDTH = 6
EAGER_ASPECT = "0.5,2.0"  # loose: distant heads are often squashed
EAGER_FILL = 0.40      # blob must fill this much of its box (0..1)
EAGER_CONFIRM = 2      # consecutive suspicious frames before firing
EAGER_COOLDOWN = 0.12  # seconds between clicks
AIM_TOLERANCE = 3      # px the crosshair may miss the box by, all modes

# ---- win32 input / window plumbing -----------------------------------------
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

MOUSE_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
}

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _ULONG_PTR = ctypes.c_ulonglong
else:
    _ULONG_PTR = ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]


user32 = ctypes.windll.user32
user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = ctypes.c_uint
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)


def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def foreground_title():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _send_mouse(flags):
    inp = INPUT(type=INPUT_MOUSE)
    inp.union.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def mouse_click(hold_ms, button="right"):
    down, up = MOUSE_FLAGS[button]
    _send_mouse(down)
    time.sleep(max(hold_ms, 1) / 1000.0)
    _send_mouse(up)


# ---- logging ---------------------------------------------------------------
def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---- template handling -----------------------------------------------------
def load_templates(paths):
    templates = []
    for path in paths:
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"Could not read template: {path}")
        templates.append((os.path.basename(path), img))
        log(f"template {path} loaded: {img.shape[1]}x{img.shape[0]}")
    return templates


def auto_scales(templates, region_w, region_h, bottom=0.55):
    """A spread of scales from `bottom`x to 1.8x, shrunk to what fits."""
    tw = max(t.shape[1] for _n, t in templates)
    th = max(t.shape[0] for _n, t in templates)
    limit = 0.95 * min(region_w / tw, region_h / th)
    top = min(1.8, limit)
    if top < bottom:
        bottom = max(0.03, limit / 3.0)
        top = limit
    n = 7
    ratio = (top / bottom) ** (1.0 / (n - 1))
    return [round(bottom * ratio ** i, 3) for i in range(n)]


def build_variants(templates, scales, rotations):
    """Pre-render every template at every scale/angle, as blurred grayscale."""
    variants = []
    for name, tpl in templates:
        h0, w0 = tpl.shape[:2]
        for rot in rotations:
            for s in scales:
                w, h = int(round(w0 * s)), int(round(h0 * s))
                if w < 8 or h < 8:
                    continue
                img = cv2.resize(tpl, (w, h), interpolation=cv2.INTER_AREA)
                if rot:
                    center = (w / 2.0 - 0.5, h / 2.0 - 0.5)
                    m = cv2.getRotationMatrix2D(center, rot, 1.0)
                    cos, sin = abs(m[0, 0]), abs(m[0, 1])
                    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
                    m[0, 2] += nw / 2.0 - center[0]
                    m[1, 2] += nh / 2.0 - center[1]
                    img = cv2.warpAffine(img, m, (nw, nh),
                                         flags=cv2.INTER_LINEAR,
                                         borderMode=cv2.BORDER_REPLICATE)
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (3, 3), 0)
                variants.append((name, rot, s, gray))
    if not variants:
        raise SystemExit("No usable template variants - template too small?")
    log(f"{len(variants)} template variants ready")
    return variants


def to_gray(frame):
    return cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (3, 3), 0)


def best_match(frame_gray, variants):
    """Return (score, x, y, w, h, name) for the strongest match, or None."""
    best = None
    fh, fw = frame_gray.shape[:2]
    for name, _rot, _scale, tpl in variants:
        th, tw = tpl.shape[:2]
        if th > fh or tw > fw:
            continue
        res = cv2.matchTemplate(frame_gray, tpl, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, score, _, loc = cv2.minMaxLoc(res)
        if best is None or score > best[0]:
            best = (float(score), loc[0], loc[1], tw, th, name)
    return best


def contains_point(match, px, py, pad=0):
    _, x, y, w, h, _ = match
    return (x - pad <= px < x + w + pad
            and y - pad <= py < y + h + pad)


def _clean(mask):
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))


def color_candidates(frame, cfg):
    """Every head/torso candidate in the frame.

    Returns (score, x, y, w, h, kind) where kind is "full" (head + torso),
    "head" (head only, partial mode) or "guess" (eager mode: small/odd shape
    that might be an enemy).
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    head_lo, head_hi = cfg["head"]
    body_lo, body_hi = cfg["body"]
    head = _clean(cv2.inRange(hsv, head_lo, head_hi))
    body = _clean(cv2.inRange(hsv, body_lo, body_hi))
    fh, fw = frame.shape[:2]
    frame_area = fh * fw
    partial = cfg["partial"]

    # eager mode relaxes size and shape so distant / squashed heads count
    eager = cfg.get("eager", False)
    min_area = cfg.get("eager_min_area", EAGER_MIN_AREA) if eager else cfg["min_area"]
    min_width = cfg.get("eager_min_width", EAGER_MIN_WIDTH) if eager else cfg["min_width"]
    aspect = cfg.get("eager_aspect", (0.4, 2.2)) if eager else cfg["aspect"]
    guess_score = cfg.get("eager_score", EAGER_SCORE)
    min_fill = cfg.get("eager_fill", EAGER_FILL) if eager else 0.0

    n, _lab, stats, _cents = cv2.connectedComponentsWithStats(head, 8)
    out = []
    for i in range(1, n):
        hx, hy, hw, hh, area = stats[i]
        if area < min_area or hw < min_width:
            continue
        if hh < max(4, min_width // 2):
            continue
        if area > 0.20 * frame_area:          # a wall, not a head
            continue
        aspect_ok = aspect[0] <= hw / hh <= aspect[1]
        if eager and area / float(hw * hh) < min_fill:
            continue                      # thin strip / ragged shape, not a head

        x0 = max(0, hx - hw // 4)
        x1 = min(fw, hx + hw + hw // 4)
        y0 = max(0, int(hy + hh * 0.8))
        y1 = min(fh, int(hy + hh * 3.0))
        frac = 0.0
        torso = None
        if y1 - y0 >= 4 and x1 - x0 >= 4:
            band = body[y0:y1, x0:x1]
            frac = float(np.count_nonzero(band)) / band.size
            bn, _bl, bstats, _bc = cv2.connectedComponentsWithStats(band, 8)
            for j in range(1, bn):
                bx, by, bw, bh, barea = bstats[j]
                if barea < 0.25 * hw * hh or bw > 2.5 * hw or bw < 0.5 * hw:
                    continue
                if abs((bx + bw / 2) - (hx - x0 + hw / 2)) > 0.75 * hw:
                    continue
                if torso is None or barea > torso[4]:
                    torso = (bx, by, bw, bh, barea)

        if torso is not None and frac >= max(0.10, cfg["body_frac"] * 0.4):
            bx0, by0 = x0 + torso[0], y0 + torso[1]
            ux0, uy0 = min(hx, bx0), min(hy, by0)
            ux1 = max(hx + hw, bx0 + torso[2])
            uy1 = max(hy + hh, by0 + torso[3])
            score = max(frac, cfg["partial_score"] if partial else frac)
            kind = "full"
        elif partial and aspect_ok:
            ux0, uy0, ux1, uy1 = hx, hy, hx + hw, hy + hh
            score = cfg["partial_score"]
            kind = "head"
        elif eager:
            ux0, uy0, ux1, uy1 = hx, hy, hx + hw, hy + hh
            score = guess_score
            kind = "guess"
        else:
            continue
        out.append((float(score), int(ux0), int(uy0),
                    int(ux1 - ux0), int(uy1 - uy0), kind))
    return out


def color_match(frame, cfg, prefer_x, prefer_y):
    """Closest head/torso candidate to the crosshair, or None.

    A full head+torso scores the torso fraction (usually 0.6-0.9). When partial
    hits are allowed, a visible head whose torso is hidden (or only partly
    visible) also counts, and scores cfg["partial_score"]. Requires the head to
    look head-shaped and any torso to be aligned underneath, so plain yellow
    scenery does not fire on its own.
    """
    cands = color_candidates(frame, cfg)
    if not cands:
        return None
    def rank(c):
        _, x, y, w, h, _kind = c
        dx = max(x - prefer_x, 0, prefer_x - (x + w))
        dy = max(y - prefer_y, 0, prefer_y - (y + h))
        return ((dx * dx + dy * dy) ** 0.5, -c[0])
    best = min(cands, key=rank)
    name = {"full": "color", "head": "color-head",
            "guess": "color-guess"}[best[5]]
    return (best[0], best[1], best[2], best[3], best[4], name)


# ---- main ------------------------------------------------------------------
class State:
    def __init__(self):
        self.active = True
        self.quit = False
        self.dump = False
        self.clicks = 0
        self.streak = 0
        self.last_click = 0.0
        self.best_score = 0.0

    def toggle(self):
        self.active = not self.active
        log("Clicking: " + ("ON" if self.active else "OFF"))


def parse_pair(value, default):
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise SystemExit(f"Expected WxH, got {value!r}")


def parse_list(value, cast):
    try:
        items = [cast(v) for v in str(value).split(",") if str(v).strip()]
    except ValueError:
        raise SystemExit(f"Bad number list: {value!r}")
    if not items:
        raise SystemExit(f"Empty list: {value!r}")
    return items


def parse_hsv(value):
    vals = parse_list(value, int)
    if len(vals) != 6:
        raise SystemExit("HSV range must be h1,s1,v1,h2,s2,v2")
    return tuple(vals[:3]), tuple(vals[3:])


def parse_args():
    p = argparse.ArgumentParser(
        description="Click when the enemy template is under the crosshair "
                    "(single-player use only).")
    p.add_argument("--mode", choices=("template", "color"), default="template",
                   help="template = image matching, color = head/torso colour rule")
    p.add_argument("--template", default="template.png",
                   help="template png, or comma-separated list of poses")
    p.add_argument("--window", default=None,
                   help="only act when the foreground window title contains "
                        "this text (strongly recommended)")
    p.add_argument("--threshold", type=float, default=None,
                   help="template: match score (default 0.80). "
                        "color: required torso fraction (default 0.30)")
    p.add_argument("--head-hsv", default=HEAD_HSV,
                   help="head colour range: h1,s1,v1,h2,s2,v2")
    p.add_argument("--body-hsv", default=BODY_HSV,
                   help="torso colour range: h1,s1,v1,h2,s2,v2")
    p.add_argument("--min-head-area", type=int, default=MIN_HEAD_AREA)
    p.add_argument("--head-aspect", default=HEAD_ASPECT,
                   help="min,max width/height for a head blob")
    p.add_argument("--body-frac", type=float, default=BODY_FRAC,
                   help="share of the band below the head that must be torso colour")
    p.add_argument("--strict", action="store_true",
                   help="colour mode: require head + torso, ignore partial hits "
                        "(a visible head alone no longer counts)")
    p.add_argument("--partial-score", type=float, default=PARTIAL_SCORE,
                   help="colour mode: confidence given to a head-only hit")
    p.add_argument("--eager", "--spray", action="store_true", dest="eager",
                   help="fire at anything that MIGHT be an enemy: distant/small "
                        "shapes, loose head shape, crosshair near the box. "
                        "Expect clicks on scenery too")
    p.add_argument("--eager-score", type=float, default=EAGER_SCORE,
                   help="confidence given to a 'might be an enemy' hit")
    p.add_argument("--eager-min-area", type=int, default=EAGER_MIN_AREA,
                   help="smallest head blob accepted in eager mode (px)")
    p.add_argument("--eager-min-width", type=int, default=EAGER_MIN_WIDTH,
                   help="smallest head width accepted in eager mode (px)")
    p.add_argument("--eager-aspect", default=EAGER_ASPECT,
                   help="min,max width/height accepted in eager mode")
    p.add_argument("--eager-fill", type=float, default=EAGER_FILL,
                   help="how solid the blob must be, area/box (0..1). "
                        "Higher = fewer clicks on yellow trim/walls")
    p.add_argument("--aim-tolerance", type=int, default=AIM_TOLERANCE,
                   help="pixels the crosshair may miss the box by before it "
                        "counts (default 3; 0 = must be exactly on it)")
    p.add_argument("--scales", default=SCALES,
                   help="comma-separated template scales, or 'auto'")
    p.add_argument("--rotations", default=ROTATIONS,
                   help="comma-separated template angles in degrees")
    p.add_argument("--region", default=REGION,
                   help="capture box around the crosshair, e.g. 240x240")
    p.add_argument("--cx", type=int, default=None,
                   help="crosshair x on screen (default: monitor centre)")
    p.add_argument("--cy", type=int, default=None,
                   help="crosshair y on screen (default: monitor centre)")
    p.add_argument("--monitor", type=int, default=1,
                   help="mss monitor index (1 = primary)")
    p.add_argument("--confirm", type=int, default=None,
                   help="consecutive frames a hit must persist before firing "
                        "(default 2, or 1 with --eager)")
    p.add_argument("--cooldown", type=float, default=None,
                   help="seconds between clicks (default 0.18, or 0.08 with "
                        "--eager)")
    p.add_argument("--button", choices=("left", "right"), default="right",
                   help="mouse button to click (default right)")
    p.add_argument("--click-hold", type=int, default=CLICK_HOLD,
                   help="milliseconds to hold the button down")
    p.add_argument("--loop-sleep", type=float, default=LOOP_SLEEP)
    p.add_argument("--debug", action="store_true",
                   help="never click; print scores and save debug frames")
    p.add_argument("--save-debug", action="store_true", default=True,
                   help="save an annotated crop every couple of seconds")
    return p.parse_args()


def draw_debug(frame, match, threshold, cx, cy):
    img = frame.copy()
    if match is not None:
        score, x, y, w, h, name = match
        color = (0, 255, 0) if score >= threshold else (0, 165, 255)
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 1)
        label = f"{score:.3f}"
        if name == "color-head":
            label += " head"
        elif name == "color-guess":
            label += " guess"
        cv2.putText(img, label, (x, max(y - 4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
    cv2.drawMarker(img, (cx, cy), (255, 0, 255), cv2.MARKER_CROSS, 12, 1)
    return img


def main():
    args = parse_args()
    set_dpi_awareness()

    state = State()

    reg_w, reg_h = parse_pair(args.region, None)

    confirm = (args.confirm if args.confirm is not None
               else (EAGER_CONFIRM if args.eager else CONFIRM))
    cooldown = (args.cooldown if args.cooldown is not None
                else (EAGER_COOLDOWN if args.eager else COOLDOWN))

    if args.mode == "color":
        cfg = {
            "head": parse_hsv(args.head_hsv),
            "body": parse_hsv(args.body_hsv),
            "min_area": args.min_head_area,
            "min_width": MIN_HEAD_WIDTH,
            "aspect": tuple(parse_list(args.head_aspect, float)),
            "body_frac": args.body_frac,
            "partial": not args.strict,
            "partial_score": args.partial_score,
        }
        threshold = args.threshold if args.threshold is not None else COLOR_THRESHOLD
        if args.eager:
            cfg.update({
                "eager": True,
                "eager_score": args.eager_score,
                "eager_min_area": args.eager_min_area,
                "eager_min_width": args.eager_min_width,
                "eager_aspect": tuple(parse_list(args.eager_aspect, float)),
                "eager_fill": args.eager_fill,
            })
            threshold = min(threshold, args.eager_score)
        log(f"colour mode: head {cfg['head']}, body {cfg['body']}, "
            f"threshold {threshold}, "
            f"{'strict head+torso' if args.strict else 'partial hits allowed'}"
            + (f", EAGER: guess >= {args.eager_score}, "
               f"min area {args.eager_min_area}px, fill >= {args.eager_fill:.0%}"
               if args.eager else ""))
    else:
        cfg = None
        threshold = args.threshold if args.threshold is not None else THRESHOLD
        if args.eager:
            threshold = min(threshold, args.eager_score)
        tpl_paths = []
        for raw in args.template.split(","):
            raw = raw.strip()
            if not raw:
                continue
            if not os.path.isabs(raw):
                raw = os.path.join(BASE, raw)
            tpl_paths.append(raw)
        templates = load_templates(tpl_paths)

        if args.scales.strip().lower() == "auto":
            scales = auto_scales(templates, reg_w, reg_h,
                                 bottom=0.20 if args.eager else 0.55)
            log("auto scales: " + ", ".join(f"{s:g}" for s in scales))
        else:
            scales = parse_list(args.scales, float)
        variants = build_variants(templates, scales,
                                  parse_list(args.rotations, float))

        kept = [v for v in variants
                if v[3].shape[1] <= reg_w and v[3].shape[0] <= reg_h]
        if len(kept) < len(variants):
            log(f"skipped {len(variants) - len(kept)} variants that do not fit "
                f"the {reg_w}x{reg_h} region (template too big for that scale)")
        if not kept:
            raise SystemExit(
                f"No template variant fits a {reg_w}x{reg_h} region. Enlarge "
                f"--region, add smaller --scales, or snip a smaller template.")
        variants = kept
    keyboard.add_hotkey("f6", state.toggle)
    keyboard.add_hotkey("f8", lambda: setattr(state, "dump", True))
    keyboard.add_hotkey("f10", lambda: setattr(state, "quit", True))

    with mss.MSS() as sct:
        mon = sct.monitors[args.monitor]
        cx = args.cx if args.cx is not None else mon["left"] + mon["width"] // 2
        cy = args.cy if args.cy is not None else mon["top"] + mon["height"] // 2
        region = {
            "left": cx - reg_w // 2,
            "top": cy - reg_h // 2,
            "width": reg_w,
            "height": reg_h,
        }
        rel_cx, rel_cy = reg_w // 2, reg_h // 2

        log(f"watching {reg_w}x{reg_h} around ({cx}, {cy}), "
            f"threshold={threshold}, confirm={confirm}, "
            f"cooldown={cooldown}s, aim tolerance={args.aim_tolerance}px, "
            f"window={args.window!r}")
        log(f"mode: {'DEBUG (no clicks)' if args.debug else 'LIVE'}. "
            f"F6 toggle, F8 snapshot, F10 quit.")

        if not args.window:
            log("WARNING: --window not set, so it will fire on any window, "
                "including your desktop. Pass --window \"Roblox".")

        last_debug = last_log = 0.0
        focus_warned = False

        while not state.quit:
            now = time.time()

            if args.window and args.window.lower() not in foreground_title().lower():
                if not focus_warned:
                    log(f"waiting for a window containing {args.window!r}")
                    focus_warned = True
                time.sleep(0.2)
                continue
            focus_warned = False

            frame = np.array(sct.grab(region))[:, :, :3]
            if cfg is not None:
                match = color_match(frame, cfg, rel_cx, rel_cy)
            else:
                match = best_match(to_gray(frame), variants)
            score = match[0] if match else -1.0
            state.best_score = max(state.best_score, score)
            tag = ""
            if match is not None:
                if match[5] == "color-head":
                    tag = " head-only"
                elif match[5] == "color-guess":
                    tag = " guess"

            pad = args.aim_tolerance
            on_target = (match is not None
                         and score >= threshold
                         and contains_point(match, rel_cx, rel_cy, pad))
            state.streak = state.streak + 1 if on_target else 0

            fired = False
            if (state.active and not args.debug
                    and state.streak >= max(confirm, 1)
                    and now - state.last_click >= cooldown):
                mouse_click(args.click_hold, args.button)
                state.last_click = time.time()
                state.clicks += 1
                fired = True
                log(f"click #{state.clicks} score={score:.3f} "
                    f"streak={state.streak}{tag}")
                state.streak = 0

            if now - last_debug >= DEBUG_INTERVAL or state.dump:
                last_debug = now
                state.dump = False
                cv2.imwrite(DEBUG_PNG, draw_debug(frame, match,
                                                  threshold,
                                                  rel_cx, rel_cy))

            if now - last_log >= LOG_INTERVAL:
                last_log = now
                log(f"{'ACTIVE' if state.active else 'PAUSED'} "
                    f"score={score:.3f} best={state.best_score:.3f} "
                    f"streak={state.streak} clicks={state.clicks}"
                    + tag + (" FIRED" if fired else ""))
                state.best_score = score

            if args.loop_sleep:
                time.sleep(args.loop_sleep)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
