# THIS ONLY WORKS WITH THE BOT ENEMY VISUAL SETTING
# DO NOT USE IN ACTUAL LOBBYS OR YOU WILL BE BANNED!!!
# ONLY FOR EDUCATIONAL PURPOSES

# Triggerbot (single-player only)

Watches a small box around your crosshair and right-clicks when the enemy is
under it. It does not move your mouse.

**Only use this against your own single-player session.** Synthetic input ingame
with any anti-cheat, can get an account flagged. 

## Two detection modes

- **`--mode color`** (recommended for this game): finds the enemy's yellow head
  with the blue torso directly below it. Scale independent, ~1 ms per frame, so
  it keeps working as the enemy walks towards or away from you.

- **`--mode template`** (default): matches `template.png` at several scales.
  More general, but much more sensitive to size and pose.

## Partial hits (colour mode)

By default colour mode also fires when only **part** of the enemy is visible:

- head **and** torso visible - score is the share of blue under the head
  (typically 0.6-0.9), so normal body shots behave as before;
- only the **head** showing (peeking over cover, lower body hidden) - score is
  `--partial-score` (default `0.40`), and the box is just the head;
- torso half hidden but still aligned under the head - also counts.

The default `--threshold` is `0.30`, i.e. below `--partial-score`, so head-only
hits pass. If you want the old behaviour (head + torso required), add `--strict`.
`triggerbot_debug.png` and the log mark these as `head-only` / `head`.

## Eager mode (distant / uncertain targets, precise trigger)

`--eager` (alias `--spray`) makes **detection** more forgiving for distant or
squashed targets, but keeps the **aim check** strict: it only fires when the
crosshair is actually on the target box, within `--aim-tolerance` pixels
(default 3). An enemy standing somewhere else in the capture region does **not**
trigger it.

Detection changes:

- drops the smallest head blob from 150 px to 50 px (6 px wide), so a tiny
  distant head still counts;
- loosens the head shape from 0.6-1.6 to 0.5-2.0, so perspective-squashed heads
  qualify;
- requires the blob to be solid (`--eager-fill`, default 0.40 of its box), which
  rejects thin yellow trim strips and wall edges;
- gives uncertain blobs a low confidence (`--eager-score`, default 0.20) and
  lowers `--threshold` to match;
- still waits `--confirm` frames (default 2) and a 0.12 s cooldown, so it does
  not machine-gun at flickering pixels.

```
py -3.12 triggerbot.py --mode color --eager --window "Roblox" --debug
```

Result on your screenshot: the distant enemy (36x65 px) is detected when the
crosshair is on it, the thin yellow strip on the wall is rejected, and the same
enemy moved 60 px off the crosshair produces no clicks at all.

If it is still too eager, raise `--eager-score` or `--eager-fill`, set
`--aim-tolerance 0`, and/or shrink `--region`. Drop `--eager` to go back to
confident hits only.

`--eager` also works in template mode: `--scales auto` then goes down to 0.2x so
the template can match a distant enemy.

## Quick start (colour mode)

```
py -3.12 triggerbot.py --mode color --window "Roblox" --debug
py -3.12 triggerbot.py --mode color --window "Roblox"
```

`--debug` never clicks; it logs a confidence score once a second and writes
`triggerbot_debug.png` with the detected box drawn. The default `--threshold`
(0.30) accepts both full head + torso and head-only hits (see *Partial hits*
below). Raise it to 0.5-0.6 if it ever fires at scenery; that also stops
head-only hits unless you raise `--partial-score` too.

Colour ranges are configurable if the lighting shifts:

```
--head-hsv 18,130,130,40,255,255 --body-hsv 95,120,80,125,255,255
```

## Requirements

Windows + Python 3.12 with the deps already installed on this machine:

```
py -3.12 -m pip install opencv-python mss numpy keyboard
```

Set the game to **borderless windowed** (or windowed). Exclusive fullscreen often
captures as a black frame, so the bot will never see the enemy.

## Template mode

`template.png` currently holds a 54x54 crop of the enemy's head taken from a
gameplay screenshot. Re-crop it any time with:

```
py -3.12 snip.py                 # 3s, then drag a box
py -3.12 snip.py shot.png
```

You get 3 seconds to switch to the game, then a fullscreen copy of the screen
appears - drag a tight box around the enemy and press Enter.

- Crop **tightly** around something distinctive (head, logo, face) - not the
  whole body. Background inside the crop is what causes false hits.
- If the enemy appears in several poses, save `tpl_a.png`, `tpl_b.png`, ... and
  pass them all: `--template tpl_a.png,tpl_b.png`.

## Template dry run (not active)

```
py -3.12 triggerbot.py --window "Roblox" --debug
```

It logs a match score once a second and writes `triggerbot_debug.png` (a 240x240
crop around the crosshair with the best match boxed). Point at an enemy and watch
the score: set `--threshold` a bit above the best score you see on empty
background, e.g. `--threshold 0.85`. With the current 54x54 head template,
`--scales 0.9,1.0,1.1,1.2,1.3` and `--threshold 0.70` are a sensible start.

`--window` is matched against the foreground window title, so it only fires when
the game is focused. Get the exact title with:

```
py -3.12 -c "import ctypes,ctypes.wintypes as w;u=ctypes.windll.user32;h=u.GetForegroundWindow();b=ctypes.create_unicode_buffer(300);u.GetWindowTextW(h,b,300);print(b.value)"
```

(alt-tab to the game first).

## Live

```
py -3.12 triggerbot.py --window "Roblox"
```

| Key | Action |
|-----|--------|
| F6  | toggle clicking on/off |
| F8  | save `triggerbot_debug.png` right now |
| F10 | quit |

## Tuning

| Flag | Default | What it does |
|------|---------|--------------|
| `--threshold` | 0.80 (template) / 0.30 (color) | template: match score. color: share of blue needed below the head. Higher = fewer false clicks |
| `--partial-score` | 0.40 | color: score given to a head-only hit. Keep it **above** `--threshold` (or raise `--threshold`) if partial hits are too eager |
| `--strict` | off | color: require head **and** torso, i.e. disable partial/head-only hits |
| `--eager` / `--spray` | off | forgiving detection for distant/odd targets; still requires the crosshair on the box |
| `--eager-score` | 0.20 | confidence given to a "might be an enemy" hit |
| `--eager-min-area` | 50 | smallest head blob (px) accepted in eager mode |
| `--eager-min-width` | 6 | smallest head width (px) accepted in eager mode |
| `--eager-aspect` | 0.5,2.0 | width/height range accepted in eager mode |
| `--eager-fill` | 0.40 | how solid a blob must be (area / box). Higher = rejects trim and wall edges |
| `--aim-tolerance` | 3 | px the crosshair may miss the box by before it counts. `0` = must be exactly on it |
| `--region` | 240x240 | capture box around the crosshair (smaller is faster and safer) |
| `--scales` | 0.6..1.6 | template sizes to try. `auto` derives 6 sizes that fit the region; narrow it to your usual engagement distances |
| `--rotations` | 0 | add angles, e.g. `--rotations -15,0,15`, if the enemy turns |
| `--confirm` | 2 (2 with `--eager`) | consecutive frames the match must persist before firing |
| `--cooldown` | 0.18 (0.12 with `--eager`) | seconds between clicks |
| `--button` | right | `left` or `right` |
| `--click-hold` | 25 | ms the button stays down (raise to 40-60 if the game ignores fast clicks) |
| `--cx` / `--cy` | screen centre | crosshair position if it is not exactly centred |

## Troubleshooting

- **Nothing ever matches** - look at `triggerbot_debug.png`; if it is black or
  does not contain the crosshair area, the game is in exclusive fullscreen or
  display scaling is off. Try borderless windowed, or pass `--cx`/`--cy`.
- **Clicks do not register** - run the terminal **as administrator** (Windows
  blocks input to elevated windows), and try `--click-hold 50`.
- **Fires at scenery/teammates** - raise `--threshold`, re-snip a tighter
  template, shrink `--region`, or raise `--confirm` to 3-4. In colour mode with
  partial hits on, yellow scenery that happens to be head-shaped can also fire:
  add `--strict`, or set `--partial-score` above `--threshold` so head-only hits
  are ignored while full head + torso still counts.
- **`--eager` fires too much** - it is deliberately forgiving, but it should only
  fire with the crosshair on the target. Tighten with `--aim-tolerance 0`,
  `--eager-fill 0.55`, a higher `--eager-score` (0.30), `--eager-min-area 80`,
  a smaller `--region`, or `--confirm 3`. Drop `--eager` to go back to confident
  hits only.
- **Colour mode misses the enemy** - the enemy may be in different lighting:
  run `--debug` and check `triggerbot_debug.png`, then widen `--head-hsv` /
  `--body-hsv`, or lower `--threshold`. Very small distant enemies also fall
  below `--min-head-area`.
- **Template drifts at other distances** - that is what `--scales` is for; widen
  the list instead of re-snipping.
- **Big template (fills most of the screenshot)** - it cannot match at full size
  inside a small crosshair region. Use `--scales auto`, which picks sizes that
  fit. A large crop also drags background into the template, which caps the
  match score; if on-target scores stay below ~0.75, crop just the head or
  another distinctive part instead.
- **Performance** - cost is roughly `variants x region area`; a 240x240 region
  with ~7 variants is cheap, thousands of variants is not.
