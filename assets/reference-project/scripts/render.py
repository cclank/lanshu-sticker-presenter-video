# Desk-sticker short: every frame is a pure function of the frame index.
# usage: python3 scripts/render.py START END out.mp4   (frame range, end exclusive)
#        python3 scripts/render.py --still T out.png    (one frame at time T)
import sys, os, math, functools
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
from keyer import key_frame, clean_alpha

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H, FPS = 1080, 1920, 30
SRC_FPS = 24
NFRAMES = 811  # 27.033 s, source picture ends at 27.04

# ---------------------------------------------------------------- timeline
T = dict(
    title=2.46, coffee=4.68, coffee_write=5.30,
    poster=7.04, coaster=8.72, pothos=9.76, three=11.0,
    editor=13.46, zhuse=14.30, simple=14.76, card=15.72, write=17.70, write_end=18.88,
    spread=19.26, breathe=21.54,
    ask=556 / 24.0, ask_line=23.28, zhuomian=24.26, which=24.70,
)
IRIS = 0.20         # scene iris duration, ends on the anchor
PUSH = 0.24         # in-screen push
MOVE = 0.34         # presenter re-placement

# ---------------------------------------------------------------- colours
def hexc(s):
    s = s.lstrip('#'); return np.array([int(s[i:i+2], 16) for i in (0, 2, 4)], np.float32)
MAT, INK, CREAM, RED, LIME, YELLOW = map(hexc, ['467452', '0e0e0e', 'f7f1e2', 'de342c', 'd6f55a', 'f6e696'])
WHITE = np.array([255, 255, 255], np.float32)
OAT, TERRA, SAGE, SKY, PINK, BUTTER = map(hexc, ['e6d5b8', 'c8693f', '9db59a', 'a9cbe3', 'f2b5c4', 'f6e696'])
KRAFT = hexc('c9a47a')

def T8(c):
    return tuple(int(v) for v in c)

# ---------------------------------------------------------------- fonts
NOTO_BLACK = ('/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc', 2)
NOTO_BOLD = ('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc', 2)
NOTO_MED = ('/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc', 2)
MASHAN = (os.path.join(ROOT, 'fonts/MaShanZheng-Regular.ttf'), 0)
KUAILE = (os.path.join(ROOT, 'fonts/ZCOOLKuaiLe-Regular.ttf'), 0)
WENKAI = ('/usr/local/share/fonts/media-sandbox/LXGWWenKai-Regular.ttf', 0)

@functools.lru_cache(None)
def font(spec, size):
    return ImageFont.truetype(spec[0], size, index=spec[1])

# ---------------------------------------------------------------- easing
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def prog(t, t0, d): return clamp((t - t0) / d)
def e_out3(p): return 1 - (1 - p) ** 3
def e_in3(p): return p ** 3
def e_io(p): return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2
def e_back(p, s=1.9):
    p -= 1; return 1 + (s + 1) * p ** 3 + s * p ** 2
def lerp(a, b, p): return a + (b - a) * p

# ---------------------------------------------------------------- compositing
def over(dst, rgba, x, y, opacity=1.0):
    """Alpha-composite uint8/float RGBA image onto float RGB dst at integer x,y."""
    h, w = rgba.shape[:2]
    x, y = int(round(x)), int(round(y))
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, dst.shape[1]), min(y + h, dst.shape[0])
    if x1 <= x0 or y1 <= y0 or opacity <= 0:
        return
    s = rgba[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    a = s[..., 3:4] / 255.0 * opacity
    if dst.dtype == np.uint8:  # RGBA canvas: straight-alpha over
        d = dst[y0:y1, x0:x1].astype(np.float32)
        da = d[..., 3:4] / 255.0
        oa = a + da * (1 - a)
        rgb = (s[..., :3] * a + d[..., :3] * da * (1 - a)) / np.maximum(oa, 1e-4)
        dst[y0:y1, x0:x1, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        dst[y0:y1, x0:x1, 3:4] = np.clip(oa * 255, 0, 255).astype(np.uint8)
        return
    d = dst[y0:y1, x0:x1]
    d *= (1 - a); d += s[..., :3] * a

def over_full(dst, rgb, alpha):
    a = alpha[..., None]
    dst *= (1 - a); dst += rgb * a

def solid_rgba(mask, color):
    """mask float 0..1 -> RGBA uint8 of one colour."""
    out = np.zeros(mask.shape + (4,), np.uint8)
    out[..., :3] = T8(color); out[..., 3] = np.clip(mask * 255, 0, 255).astype(np.uint8)
    return out

def scaled(rgba, s):
    if abs(s - 1) < 1e-3: return rgba
    h, w = rgba.shape[:2]
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    return cv2.resize(rgba, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)

def over_c(dst, rgba, cx, cy, s=1.0, opacity=1.0, sx=None):
    """Place centred at cx,cy with uniform scale s (or separate x scale sx)."""
    if sx is not None:
        h, w = rgba.shape[:2]
        nw, nh = max(1, int(round(w * s * sx))), max(1, int(round(h * s)))
        im = cv2.resize(rgba, (nw, nh), interpolation=cv2.INTER_AREA)
    else:
        im = scaled(rgba, s)
    over(dst, im, cx - im.shape[1] / 2, cy - im.shape[0] / 2, opacity)

def drop_shadow(rgba, blur=10, alpha=0.35, pad=30):
    a = np.pad(rgba[..., 3], pad).astype(np.float32) / 255
    a = cv2.GaussianBlur(a, (0, 0), blur) * alpha
    return solid_rgba(a, (20, 24, 18))

# ---------------------------------------------------------------- text
@functools.lru_cache(4096)
def text_img(text, spec, size, color, tracking=0, stroke=0, stroke_color=(14, 14, 14)):
    f = font(spec, size)
    pad = stroke + 8
    adv = [f.getlength(ch) + tracking for ch in text]
    wid = int(sum(adv) - (tracking if text else 0)) + pad * 2 + 4
    asc, desc = f.getmetrics()
    hei = asc + desc + pad * 2
    im = Image.new('RGBA', (max(wid, 1), hei), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = pad
    for ch, a in zip(text, adv):
        d.text((x, pad), ch, font=f, fill=tuple(color) + (255,), stroke_width=stroke,
               stroke_fill=tuple(stroke_color) + (255,))
        x += a
    return np.array(im)

def text_w(text, spec, size, tracking=0):
    f = font(spec, size)
    return sum(f.getlength(ch) + tracking for ch in text) - (tracking if text else 0)

def skew(rgba, k):
    """Italic shear: x += k * (h - y)."""
    h, w = rgba.shape[:2]
    extra = int(abs(k) * h) + 2
    M = np.float32([[1, k, 0 if k < 0 else 0], [0, 1, 0]])
    M[0, 2] = -k * h if k < 0 else 0
    M = np.float32([[1, -k, k * h], [0, 1, 0]])
    return cv2.warpAffine(rgba, M, (w + extra, h), flags=cv2.INTER_CUBIC, borderValue=(0, 0, 0, 0))

def rotate_img(rgba, deg):
    h, w = rgba.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    c, s = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * s + w * c) + 2, int(h * c + w * s) + 2
    M[0, 2] += nw / 2 - w / 2; M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(rgba, M, (nw, nh), flags=cv2.INTER_CUBIC, borderValue=(0, 0, 0, 0))

def write_on(rgba, p, nchars):
    """Handwriting reveal: characters appear in order, each wiped left->right."""
    if p >= 1: return rgba
    if p <= 0: return None
    h, w = rgba.shape[:2]
    out = rgba.copy()
    pos = p * nchars
    xs = np.arange(w, dtype=np.float32)
    cw = w / nchars
    ci = np.floor(xs / cw)
    within = (xs - ci * cw) / cw
    fr = pos - math.floor(pos)
    vis = np.where(ci < math.floor(pos), 1.0, np.where(ci == math.floor(pos), np.clip((fr * 1.1 - within) / 0.08, 0, 1), 0.0))
    out[..., 3] = (out[..., 3] * vis[None, :]).astype(np.uint8)
    return out

# ---------------------------------------------------------------- hand-drawn strokes
def wobble(pts, seed, amp=2.5, step=6.0, closed=False):
    pts = np.asarray(pts, np.float32)
    if closed: pts = np.vstack([pts, pts[:1]])
    seg = np.diff(pts, axis=0); L = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate([[0], np.cumsum(L)])
    n = max(2, int(cum[-1] / step))
    s = np.linspace(0, cum[-1], n)
    x = np.interp(s, cum, pts[:, 0]); y = np.interp(s, cum, pts[:, 1])
    rng = np.random.RandomState(seed)
    ph = rng.rand(4) * 6.28
    u = s / 90.0
    nx = amp * (np.sin(u * 1.7 + ph[0]) * 0.6 + np.sin(u * 4.3 + ph[1]) * 0.4)
    ny = amp * (np.sin(u * 2.1 + ph[2]) * 0.6 + np.sin(u * 3.7 + ph[3]) * 0.4)
    return np.stack([x + nx, y + ny], 1)

def rrect_pts(x0, y0, x1, y1, r, n=8):
    pts = []
    for cx, cy, a0 in [(x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)]:
        for k in range(n + 1):
            a = math.radians(a0 + 90 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts

def ellipse_pts(cx, cy, rx, ry, a0=0, a1=360, n=60):
    return [(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a)))
            for a in np.linspace(a0, a1, n)]

def stroke(canvas, pts, color, th, partial=1.0):
    """Antialiased polyline onto RGBA or RGB uint8 canvas; partial draws the first fraction."""
    pts = np.asarray(pts, np.float32)
    if partial < 1:
        k = max(2, int(len(pts) * partial))
        if partial <= 0: return
        pts = pts[:k]
    col = tuple(int(c) for c in color) + ((255,) if canvas.shape[2] == 4 else ())
    cv2.polylines(canvas, [np.round(pts * 4).astype(np.int32)], False, col, int(th), cv2.LINE_AA, shift=2)

def fill(canvas, pts, color):
    col = tuple(int(c) for c in color) + ((255,) if canvas.shape[2] == 4 else ())
    cv2.fillPoly(canvas, [np.round(np.asarray(pts) * 4).astype(np.int32)], col, cv2.LINE_AA, shift=2)

def sparkle_pts(cx, cy, r):
    pts = []
    for k in range(9):
        a = math.radians(-90 + k * 45)
        rr = r if k % 2 == 0 else r * 0.28
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts

def draw_sparkle(canvas, cx, cy, r, color, ink=True):
    if r < 2: return
    p = sparkle_pts(cx, cy, r)
    fill(canvas, p, color)
    if ink: stroke(canvas, p + [p[0]], INK, max(2, int(r * 0.12)))

def cursor_img():
    s = 2
    pts = np.array([(0, 0), (0, 46), (12, 35), (20, 54), (28, 50), (20, 32), (36, 32)], np.float32) * s + 8
    im = np.zeros((140, 100, 4), np.uint8)
    fill(im, pts, (255, 255, 255)); stroke(im, np.vstack([pts, pts[:1]]), INK, 5)
    return cv2.resize(im, (50, 70), interpolation=cv2.INTER_AREA)

def click_ring(dst, cx, cy, p, color=RED):
    if p <= 0 or p >= 1: return
    r = 14 + 46 * e_out3(p)
    layer = np.zeros((140, 140, 4), np.uint8)
    cv2.circle(layer, (70 * 4, 70 * 4), int(r * 4), T8(color) + (255,), 5, cv2.LINE_AA, shift=2)
    over(dst, layer, cx - 70, cy - 70, 1 - p)

# ---------------------------------------------------------------- assets
def load_rgba(path):
    return np.array(Image.open(path).convert('RGBA'))

ITEMS = {n: load_rgba(os.path.join(ROOT, f'build/item_{n}.png')) for n in ['lamp', 'coaster', 'pothos', 'card']}

def item_h(name, h):
    im = ITEMS[name]
    return fit_h(name, h)

@functools.lru_cache(64)
def fit_h(name, h):
    im = ITEMS[name]
    s = h / im.shape[0]
    return cv2.resize(im, (int(im.shape[1] * s), int(h)), interpolation=cv2.INTER_AREA)

@functools.lru_cache(64)
def fit_w(name, w):
    im = ITEMS[name]
    s = w / im.shape[1]
    return cv2.resize(im, (int(w), int(im.shape[0] * s)), interpolation=cv2.INTER_AREA)

@functools.lru_cache(16)
def note_card(w, text=True, rot=0.0):
    """Kraft card with 今天也慢慢来 in brush handwriting."""
    im = fit_w('card', w).copy()
    if text:
        t = text_img('今天也慢慢来', MASHAN, int(w * 0.15), (40, 30, 22))
        over_np = im.astype(np.float32)
        h = im.shape[0]
        tmp = over_np[..., :3].copy()
        over(tmp, t, (w - t.shape[1]) / 2, (h - t.shape[0]) / 2)
        im[..., :3] = tmp.astype(np.uint8)
    return im

def item_with_shadow(rgba):
    sh = drop_shadow(rgba, blur=9, alpha=0.30, pad=30)
    return sh

@functools.lru_cache(64)
def shadow_of(name, h):
    return drop_shadow(fit_h(name, h), 10, 0.28, 30)

# ---------------------------------------------------------------- mat
def build_mat():
    img = np.zeros((H, W, 3), np.float32); img[:] = MAT
    rng = np.random.RandomState(3)
    noise = cv2.GaussianBlur(rng.randn(H, W).astype(np.float32), (0, 0), 1.2) * 4
    img += noise[..., None]
    lay = np.zeros((H, W, 3), np.uint8)
    for i, x in enumerate(range(20, W, 40)):
        heavy = (i % 5 == 0)
        cv2.line(lay, (x, 0), (x, H), (255, 255, 255), 3 if heavy else 1, cv2.LINE_AA)
    for i, y in enumerate(range(20, H, 40)):
        heavy = (i % 5 == 0)
        cv2.line(lay, (0, y), (W, y), (255, 255, 255), 3 if heavy else 1, cv2.LINE_AA)
    a = lay[..., 0].astype(np.float32) / 255 * 0.16
    img = img * (1 - a[..., None]) + 255 * a[..., None]
    # ruler ticks: top and right edges, every 20 px (mm-ish), long every 100
    tick = np.zeros((H, W, 3), np.uint8)
    f = font(NOTO_MED, 18)
    for i, x in enumerate(range(20, W, 20)):
        L = 30 if i % 5 == 0 else 14
        cv2.line(tick, (x, 0), (x, L), (255, 255, 255), 2, cv2.LINE_AA)
    for i, y in enumerate(range(20, H, 20)):
        L = 30 if i % 5 == 0 else 14
        cv2.line(tick, (W - L, y), (W, y), (255, 255, 255), 2, cv2.LINE_AA)
    a = tick[..., 0].astype(np.float32) / 255 * 0.55
    img = img * (1 - a[..., None]) + 255 * a[..., None]
    pim = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)); d = ImageDraw.Draw(pim)
    for i, x in enumerate(range(20, W, 100)):
        d.text((x + 4, 32), str(i), font=f, fill=(225, 235, 225))
    for i, y in enumerate(range(20, H, 100)):
        if i: d.text((W - 54, y - 10), str(i), font=f, fill=(225, 235, 225))
    return np.array(pim).astype(np.float32)

MATIMG = build_mat()

# ---------------------------------------------------------------- laptop (static, drawn at 2x)
SCR = (108, 196, 1102, 786)   # display area x0,y0,x1,y1 (right edge past the frame)

def build_laptop():
    S = 2
    cw, ch = W + 200, 1300
    im = np.zeros((ch * S, cw * S, 4), np.uint8)
    P = lambda pts: [(x * S, y * S) for x, y in pts]
    # base deck (trapezoid)
    base = [(70, 828), (1130, 828), (1188, 1218), (12, 1218)]
    bpts = wobble(P(rrect_like(base)), 11, amp=5, step=10, closed=True)
    fill(im, bpts, CREAM)
    # lid
    lid = rrect_pts(62 * S, 150 * S, 1148 * S, 832 * S, 34 * S)
    lpts = wobble(lid, 5, amp=5, step=10, closed=True)
    fill(im, lpts, CREAM)
    # keyboard
    rng = np.random.RandomState(7)
    ky0 = 872
    for row in range(5):
        y0 = ky0 + row * 40
        inset = 120 + row * 6
        n = 13
        kw = (W + 80 - 2 * inset) / n
        for k in range(n):
            x0 = inset + k * kw
            if row == 4 and 3 <= k <= 9:
                if k == 3:
                    x0b, x1b = inset + 3 * kw + 4, inset + 10 * kw - 4
                    pts = wobble(P(rrect_pts(x0b, y0 + 4, x1b, y0 + 34, 6, 3)), 100 + row, 1.5 * S, 8, True)
                    stroke(im, pts, INK, 3 * S)
                continue
            pts = wobble(P(rrect_pts(x0 + 4, y0 + 4, x0 + kw - 4, y0 + 34, 6, 3)), 100 + row * 20 + k, 1.2 * S, 8, True)
            stroke(im, pts, INK, 3 * S)
    # trackpad
    tp = wobble(P(rrect_pts(440, 1090, 760, 1196, 14, 4)), 31, 2 * S, 10, True)
    stroke(im, tp, INK, 4 * S)
    # display
    sx0, sy0, sx1, sy1 = SCR
    dp = wobble(P(rrect_pts(sx0 - 6, sy0 - 6, sx1 + 6, sy1 + 6, 14, 4)), 21, 2 * S, 10, True)
    fill(im, dp, (34, 34, 32))
    # crease texture: faint folds on paper
    tex = np.zeros(im.shape[:2], np.float32)
    for k in range(9):
        x0 = rng.randint(0, cw * S); y0 = rng.randint(0, ch * S)
        ang = rng.uniform(-0.6, 0.6) + (0 if k % 2 else 1.4)
        L = rng.randint(400, 1400)
        cv2.line(tex, (x0, y0), (int(x0 + L * math.cos(ang)), int(y0 + L * math.sin(ang))), 1.0, 3 * S, cv2.LINE_AA)
    tex = cv2.GaussianBlur(tex, (0, 0), 2.5)
    paper = (im[..., 3] > 0) & (im[..., 0] > 200)
    col = im[..., :3].astype(np.float32)
    col[paper] -= (tex[paper] * 14)[:, None]
    col += (rng.randn(*im.shape[:2]) * 3)[..., None] * paper[..., None]
    im[..., :3] = np.clip(col, 0, 255).astype(np.uint8)
    # outlines last (thick wobbly ink)
    stroke(im, bpts, INK, 9 * S)
    stroke(im, lpts, INK, 9 * S)
    stroke(im, wobble(P([(78, 840), (1122, 840)]), 41, 2 * S, 10), INK, 5 * S)  # hinge
    stroke(im, dp, INK, 5 * S)
    small = cv2.resize(im, (cw, ch), interpolation=cv2.INTER_AREA)
    return small

def rrect_like(poly):
    # densify polygon corners slightly rounded
    out = []
    n = len(poly)
    for i in range(n):
        p0 = np.array(poly[i - 1], float); p1 = np.array(poly[i], float); p2 = np.array(poly[(i + 1) % n], float)
        a = p1 + (p0 - p1) * 0.03; b = p1 + (p2 - p1) * 0.03
        for t in np.linspace(0, 1, 5):
            out.append(tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * p1 + t ** 2 * b))
    return out

LAPTOP = build_laptop()
LAPTOP_SH = drop_shadow(LAPTOP, 14, 0.40, 40)

# ---------------------------------------------------------------- presenter sticker
class Presenter:
    def __init__(self):
        self.cap = cv2.VideoCapture(os.path.join(ROOT, 'assets/take.mp4'))
        self.pos = 0
        self.cache = {}

    def get(self, si):
        if si in self.cache: return self.cache[si]
        if si != self.pos:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, si); self.pos = si
        ok, fr = self.cap.read()
        if not ok:
            return self.get(si - 1)
        self.pos += 1
        bgr, a = key_frame(fr)
        a = clean_alpha(a)
        rgb = bgr[..., ::-1].copy()
        self.cache = {si: (rgb, a)}
        return rgb, a

PRES = None

def sticker(si, s, border=24, closeup=False):
    """Return (rgba uint8, ox, oy): sticker image and where source (0,0) lands relative to it."""
    rgb, a = PRES.get(si)
    if closeup:
        y0, y1, x0, x1 = 0, 854, 0, 480
    else:
        y0, y1, x0, x1 = 270, 854, 0, 480
    rgb = rgb[y0:y1, x0:x1]; a = a[y0:y1, x0:x1]
    nw, nh = int(round((x1 - x0) * s)), int(round((y1 - y0) * s))
    rs = np.clip(cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LANCZOS4), 0, 255)
    ra = np.clip(cv2.resize(a, (nw, nh), interpolation=cv2.INTER_LANCZOS4), 0, 1)
    pad = border + 40
    rs = np.pad(rs, ((pad, pad), (pad, pad), (0, 0)))
    ra = np.pad(ra, pad)
    hh, ww = ra.shape
    out = np.zeros((hh, ww, 3), np.float32)
    if closeup:
        yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
        cx, cy = ww / 2, pad + nh * 0.36
        rx, ry = nw * 0.62, nh * 0.60
        d = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        fade = np.clip((1.0 - d) / 0.35, 0, 1)
        fade = fade * fade * (3 - 2 * fade)
        A = ra * fade
        sh = cv2.GaussianBlur(A, (0, 0), 14) * 0.35
        sh = np.roll(sh, (12, 8), (0, 1))
        out[:] = (20, 24, 18); alpha = sh.copy()
        comp = out * alpha[..., None]
        comp = comp * (1 - A[..., None]) + rs * A[..., None]
        alpha = alpha * (1 - A) + A
    else:
        m = (ra > 0.5).astype(np.uint8)
        dist = cv2.distanceTransform(1 - m, cv2.DIST_L2, 5)
        B = np.clip(border + 0.5 - dist, 0, 1).astype(np.float32)
        B = cv2.GaussianBlur(B, (0, 0), 0.8)
        sh = cv2.GaussianBlur(B, (0, 0), 12) * 0.42
        sh = np.roll(sh, (14, 9), (0, 1))
        alpha = sh.copy()
        comp = np.zeros((hh, ww, 3), np.float32); comp[:] = (16, 20, 14)
        comp *= alpha[..., None]
        comp = comp * (1 - B[..., None]) + YELLOW * B[..., None]
        alpha = alpha * (1 - B) + B
        comp = comp * (1 - ra[..., None]) + rs * ra[..., None]
        alpha = np.maximum(alpha, ra)
        # unpremultiply for 'over'
    rgba = np.zeros((hh, ww, 4), np.uint8)
    safe = np.maximum(alpha, 1e-4)[..., None]
    rgba[..., :3] = np.clip(comp / safe, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    ox = -x0 * s + pad; oy = -y0 * s + pad
    return rgba, ox, oy

# Placements: source anchor point (230, 791) lands at (ax, ay) with scale s.
SRC_ANCHOR = (230.0, 791.0)
SRC_H = 475.0
PL_BIG = (540.0, 1892.0, 860.0 / SRC_H)
PL_POSTER = (700.0, 1580.0, 760.0 / SRC_H)

def place_presenter(dst, t, fi):
    si = min(int(fi * SRC_FPS / FPS + 1e-6), 648)
    if t >= T['ask']:
        s = 0.98
        rgba, ox, oy = sticker(si, s, closeup=True)
        p = e_back(prog(t, T['ask'], 0.28), 1.4)
        sc = 0.9 + 0.1 * p
        cx, cy = 540, 420 + (0.42 * 854 - 69) * s   # head top (source y 69) at frame y 420
        # source centre (240, 0.42*854) at (cx, cy)
        im = scaled(rgba, sc)
        X = cx - (240 * s + ox) * sc
        Y = cy - (0.42 * 854 * s + oy) * sc
        over(dst, im, X, Y)
        return
    pin = prog(t, T['poster'] - IRIS - 0.05, MOVE)
    pout = prog(t, T['editor'] - IRIS - 0.05, MOVE)
    k = e_io(pin) * (1 - e_io(pout))
    ax = lerp(PL_BIG[0], PL_POSTER[0], k); ay = lerp(PL_BIG[1], PL_POSTER[1], k); s = lerp(PL_BIG[2], PL_POSTER[2], k)
    rgba, ox, oy = sticker(si, s)
    over(dst, rgba, ax - SRC_ANCHOR[0] * s - ox, ay - (SRC_ANCHOR[1]) * s - oy)

# ---------------------------------------------------------------- laptop screens
SW, SH = SCR[2] - SCR[0], SCR[3] - SCR[1]

def kraft_bg(w, h, seed=2):
    rng = np.random.RandomState(seed)
    img = np.zeros((h, w, 3), np.float32); img[:] = KRAFT
    n = cv2.GaussianBlur(rng.randn(h, w).astype(np.float32), (0, 0), 1.0) * 7
    n2 = cv2.GaussianBlur(rng.randn(h // 8 + 1, w // 8 + 1).astype(np.float32), (0, 0), 2)
    n2 = cv2.resize(n2, (w, h)) * 10
    return img + (n + n2)[..., None]

KRAFT_SCR = kraft_bg(SW, SH)

def screen_result(t):
    img = KRAFT_SCR.copy()
    def put(name, h, cx, by, rot=0):
        im = fit_h(name, h)
        if rot: im = rotate_img(im, rot)
        sh = drop_shadow(im, 8, 0.35, 24)
        over(img, sh, cx - im.shape[1] / 2 - 24 + 6, by - im.shape[0] - 24 + 8)
        over(img, im, cx - im.shape[1] / 2, by - im.shape[0])
    put('lamp', 330, 190, 520)
    put('pothos', 300, 800, 530)
    put('coaster', 200, 480, 545)
    card = rotate_img(note_card(300), -5)
    over(img, drop_shadow(card, 8, 0.35, 24), 520 - card.shape[1] / 2 - 18, 350 - card.shape[0] / 2 - 16)
    over(img, card, 520 - card.shape[1] / 2, 350 - card.shape[0] / 2)
    # handwritten labels with arrows
    lab = img.copy().astype(np.uint8)
    l1 = text_img('暖光', MASHAN, 58, T8(INK))
    l2 = text_img('一点绿', MASHAN, 58, T8(INK))
    p1 = prog(t, 0.35, 0.5); p2 = prog(t, 0.95, 0.6)
    w1 = write_on(l1, p1, 2); w2 = write_on(l2, p2, 3)
    if w1 is not None: over(img, w1, 300, 56)
    if w2 is not None: over(img, w2, 690, 30)
    canv = np.zeros((SH, SW, 4), np.uint8)
    a1 = wobble([(330, 130), (300, 170), (260, 200)], 3, 2, 5)
    a2 = wobble([(760, 110), (790, 160), (800, 210)], 4, 2, 5)
    stroke(canv, a1, INK, 5, prog(t, 0.75, 0.25))
    stroke(canv, a2, INK, 5, prog(t, 1.45, 0.25))
    if t > 1.0: stroke(canv, [(260, 200), (282, 196)], INK, 5); stroke(canv, [(260, 200), (270, 180)], INK, 5)
    if t > 1.7: stroke(canv, [(800, 210), (786, 194)], INK, 5); stroke(canv, [(800, 210), (812, 192)], INK, 5)
    over(img, canv, 0, 0)
    # cursor drifting to the card
    p = e_io(prog(t, 0.2, 1.6))
    cx, cy = lerp(900, 600, p), lerp(560, 380, p)
    click_ring(img, cx, cy, prog(t, 1.85, 0.4))
    over(img, CURSOR, cx - 8, cy - 6)
    return img

SPARKS = [(120, 120, 38, 0.0), (880, 110, 30, 0.3), (130, 470, 26, 0.6), (900, 470, 42, 0.15)]

def screen_title(t):
    lt = t - T['title']
    img = np.zeros((SH, SW, 3), np.float32); img[:] = LIME
    title = title_card_img()
    p = e_back(prog(lt, 0.0, 0.38), 1.6)
    over_c(img, title, SW / 2 - 20, SH / 2 + 40, 0.6 + 0.4 * p, opacity=clamp(prog(lt, 0, 0.1)))
    hw = rotate_img(text_img('一个小想法', MASHAN, 74, T8(RED)), 6)
    w = write_on(hw, prog(lt, 0.35, 0.8), 5)
    if w is not None: over(img, w, 150, 66)
    canv = np.zeros((SH, SW, 4), np.uint8)
    for i, (x, y, r, ph) in enumerate(SPARKS):
        pp = e_back(prog(lt, 0.15 + ph * 0.6, 0.35), 2.0)
        tw = 1 + 0.12 * math.sin((lt - ph) * 7)
        draw_sparkle(canv, x, y, r * pp * tw, (255, 255, 255) if i % 2 else (255, 245, 170))
    over(img, canv, 0, 0)
    # underline scribble under title (red)
    return img

@functools.lru_cache(1)
def title_card_img():
    t = text_img('搭一张桌子', NOTO_BLACK, 150, (255, 255, 255), tracking=-4, stroke=9, stroke_color=T8(INK))
    t = skew(t, 0.2)
    # hard offset shadow for a sticker look
    sh = np.zeros_like(t); sh[..., 3] = t[..., 3]; sh[..., :3] = T8(INK)
    out = np.zeros((t.shape[0] + 10, t.shape[1] + 10, 4), np.uint8)
    o = out.astype(np.float32)
    tmp = np.zeros(out.shape[:2] + (3,), np.float32); alpha = np.zeros(out.shape[:2], np.float32)
    def put(src, x, y):
        nonlocal tmp, alpha
        a = src[..., 3].astype(np.float32) / 255
        h, w = a.shape
        tmp[y:y + h, x:x + w] = tmp[y:y + h, x:x + w] * (1 - a[..., None]) + src[..., :3] * a[..., None]
        alpha[y:y + h, x:x + w] = alpha[y:y + h, x:x + w] * (1 - a) + a
    put(sh, 9, 10); put(t, 0, 0)
    out[..., :3] = np.clip(tmp / np.maximum(alpha, 1e-4)[..., None], 0, 255).astype(np.uint8)
    out[..., 3] = (alpha * 255).astype(np.uint8)
    return out

def mug_canvas():
    c = np.zeros((SH, SW, 4), np.uint8)
    # saucer
    fill(c, ellipse_pts(300, 470, 210, 46), (255, 255, 255))
    stroke(c, wobble(ellipse_pts(300, 470, 210, 46), 3, 2, 6, False), INK, 7)
    # cup body
    body = [(170, 250)] + [(170 + 20 * math.sin(k / 10 * math.pi / 2) * 0, 250 + 18 * k) for k in range(1, 10)] + \
        [(230, 450), (370, 450)] + [(430, 250 + 18 * k) for k in range(9, 0, -1)] + [(430, 250)]
    body = [(170, 250), (180, 360), (215, 440), (300, 456), (385, 440), (420, 360), (430, 250)]
    smooth = []
    for i in range(len(body) - 1):
        for tt in np.linspace(0, 1, 8, endpoint=False):
            smooth.append(tuple(np.array(body[i]) * (1 - tt) + np.array(body[i + 1]) * tt))
    smooth.append(body[-1])
    fill(c, smooth + ellipse_pts(300, 250, 130, 30, 0, 180, 30)[::-1], (255, 255, 255))
    # handle
    hd = ellipse_pts(450, 330, 58, 62, -80, 100, 30)
    stroke(c, wobble(hd, 9, 2, 5), INK, 9)
    stroke(c, wobble(smooth, 4, 2, 5), INK, 8)
    # rim + coffee
    fill(c, ellipse_pts(300, 252, 120, 24), (122, 78, 48))
    stroke(c, wobble(ellipse_pts(300, 250, 130, 30), 5, 2, 5), INK, 8)
    # heart on cup
    hp = [(300, 395)] + [(300 + 16 * math.sin(a) ** 3 * 1.6, 360 - (13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a)) * 1.6) for a in np.linspace(0, 2 * math.pi, 40)]
    fill(c, hp[1:], T8(RED))
    return c

MUG = None

def screen_coffee(t):
    global MUG
    if MUG is None: MUG = mug_canvas()
    lt = t - T['coffee']
    img = np.zeros((SH, SW, 3), np.float32); img[:] = CREAM
    # dotted paper
    over(img, DOTS, 0, 0)
    over(img, MUG, -60, 30)
    # steam curls rising
    canv = np.zeros((SH, SW, 4), np.uint8)
    for k, x0 in enumerate([225, 300, 375]):
        ph = lt * 2.2 + k * 0.33
        rise = (ph % 1.0)
        pts = []
        for j in range(40):
            u = j / 39
            y = 210 - u * 150 - rise * 40
            x = x0 - 30 + 16 * math.sin(u * 7 + lt * 4 + k)
            pts.append((x, y + 30))
        alpha_fade = 1.0
        stroke(canv, wobble(pts, k, 1, 4), (110, 100, 90), 7)
    over(img, canv, 0, 0, opacity=clamp(prog(lt, 0.1, 0.4)) * 0.85)
    # two lines beside the cup, inside the visible screen (x < 950), fully written by 6.3 s
    hw = text_img('每天', MASHAN, 104, T8(INK))
    hw2 = text_img('一杯咖啡', MASHAN, 104, T8(INK))
    w1 = write_on(hw, prog(t, 5.30, 0.40), 2)
    w2 = write_on(hw2, prog(t, 5.74, 0.56), 4)
    if w1 is not None: over(img, w1, 560, 140)
    if w2 is not None: over(img, w2, 500, 290)
    # red underline
    canv2 = np.zeros((SH, SW, 4), np.uint8)
    stroke(canv2, wobble([(505, 445), (620, 438), (760, 442), (910, 434)], 8, 3, 6), RED, 9, prog(t, 6.32, 0.25))
    over(img, canv2, 0, 0)
    return img

def build_dots():
    c = np.zeros((SH, SW, 4), np.uint8)
    for y in range(20, SH, 36):
        for x in range(20, SW, 36):
            cv2.circle(c, (x, y), 2, (200, 190, 170, 255), -1, cv2.LINE_AA)
    return c

DOTS = build_dots()
CURSOR = cursor_img()

# ---------------------------------------------------------------- editor (13.46 – 23.167)
TRAY = (150, 90, 800, 500)      # in screen coords
SWATCHES = [OAT, SAGE, TERRA, SKY]
SW_POS = [(880, 140 + i * 92) for i in range(4)]
TABS = ['物品', '颜色', '卡片', '布局']

def editor_step(t):
    if t < T['simple']: return 0
    if t < T['card']: return 1
    if t < T['spread']: return 2
    return 3

PILLS = ['选个主色', '简单就好', '加张卡片', '摆开留白']

@functools.lru_cache(8)
def pill_img(text):
    tw = text_img(text, NOTO_BLACK, 40, T8(INK), tracking=2)
    w, h = tw.shape[1] + 50, 72
    c = np.zeros((h * 2, w * 2, 4), np.uint8)
    pts = rrect_pts(4, 4, w * 2 - 4, h * 2 - 4, h - 4, 8)
    fill(c, pts, LIME); stroke(c, pts + [pts[0]], INK, 7)
    c = cv2.resize(c, (w, h), interpolation=cv2.INTER_AREA)
    o = c.astype(np.float32)[..., :3]
    over(o, tw, (w - tw.shape[1]) / 2, (h - tw.shape[0]) / 2 - 2)
    c[..., :3] = o.astype(np.uint8)
    return c

def tray_layout(t):
    """Item placements inside the tray: list of (img, cx, by, scale, alpha, in_front)."""
    x0, y0, x1, y1 = TRAY
    sp = e_io(prog(t, T['spread'], 0.75))
    # cluster (before spreading) and row (after) positions: centre x, bottom y, height
    cl = {'lamp': (300, 456, 260), 'coaster': (420, 472, 150), 'pothos': (575, 462, 210), 'card': (470, 490, 170)}
    row = {'lamp': (218, 458, 210), 'coaster': (380, 458, 118), 'pothos': (565, 458, 160), 'card': (730, 458, 140)}
    out = []
    for i, n in enumerate(['lamp', 'coaster', 'pothos']):
        pi = prog(t, T['editor'] + 0.12 + i * 0.16, 0.36)
        if pi <= 0: continue
        sp_i = e_io(prog(t, T['spread'] + i * 0.08, 0.75))
        a, b = cl[n], row[n]
        out.append((n, lerp(a[0], b[0], sp_i), lerp(a[1], b[1], sp_i), lerp(a[2], b[2], sp_i), e_back(pi, 2.2)))
    pc = prog(t, T['card'] + 1.42, 0.36)
    if pc > 0:
        sp_i = e_io(prog(t, T['spread'] + 0.24, 0.75))
        a, b = cl['card'], row['card']
        out.append(('card', lerp(a[0], b[0], sp_i), lerp(a[1], b[1], sp_i), lerp(a[2], b[2], sp_i), e_back(pc, 2.2)))
    return out

def screen_editor(t):
    img = np.zeros((SH, SW, 3), np.float32); img[:] = (250, 248, 242)
    canv = np.zeros((SH, SW, 4), np.uint8)
    step = editor_step(t)
    # top bar
    canv[:70, :] = (238, 233, 220, 255)
    cv2.line(canv, (0, 70), (SW, 70), T8(INK) + (255,), 3)
    over(img, canv, 0, 0)
    over(img, text_img('我的桌面', NOTO_BLACK, 34, T8(INK)), 18, 2)
    active_tab = [1, 1, 2, 3][step]
    for i, tb in enumerate(TABS):
        x = 270 + i * 120
        lay = np.zeros((52, 104, 4), np.uint8)
        pts = rrect_pts(2, 2, 102, 50, 22, 5)
        fill(lay, pts, LIME if i == active_tab else (255, 255, 255)); stroke(lay, pts + [pts[0]], INK, 3)
        over(img, lay, x, 9)
        over(img, text_img(tb, NOTO_BOLD, 26, T8(INK)), x + 52 - text_w(tb, NOTO_BOLD, 26) / 2 - 8, 9)
    # left thumbnails
    for i, n in enumerate(['lamp', 'coaster', 'pothos', 'card']):
        y = 92 + i * 108
        lay = np.zeros((96, 112, 4), np.uint8)
        pts = rrect_pts(2, 2, 110, 94, 14, 4)
        hl = (n != 'card' and prog(t, T['editor'] + 0.12 + i * 0.16, 0.1) > 0 and t < T['editor'] + 0.5 + i * 0.16) or \
             (n == 'card' and T['card'] + 1.3 < t < T['card'] + 1.8)
        fill(lay, pts, LIME if hl else (255, 255, 255)); stroke(lay, pts + [pts[0]], INK, 3)
        im = fit_h(n, 72) if n != 'card' else fit_w('card', 84)
        over(lay, im, 56 - im.shape[1] / 2, 48 - im.shape[0] / 2)
        over(img, lay, 18, y)
    # tray
    x0, y0, x1, y1 = TRAY
    pc = e_io(prog(t, T['zhuse'] + 0.22, 0.3))
    tray_col = lerp(np.array([233, 230, 223], np.float32), OAT, pc)
    lay = np.zeros((y1 - y0 + 8, x1 - x0 + 8, 4), np.uint8)
    pts = rrect_pts(4, 4, x1 - x0 + 4, y1 - y0 + 4, 22, 6)
    fill(lay, pts, tray_col); stroke(lay, pts + [pts[0]], INK, 4)
    over(img, lay, x0 - 4, y0 - 4)
    # items in tray
    for n, cx, by, h, sc in tray_layout(t):
        h = int(h)
        im = fit_h(n, h) if n != 'card' else note_card(int(h * 1.4))
        if n == 'card':
            # handwriting writes itself on
            base = fit_w('card', im.shape[1])
            pw = prog(t, T['write'], T['write_end'] - T['write'])
            txt = text_img('今天也慢慢来', MASHAN, int(im.shape[1] * 0.15), (40, 30, 22))
            wr = write_on(txt, pw, 6)
            im = base.copy()
            if wr is not None:
                o = im[..., :3].astype(np.float32)
                over(o, wr, (im.shape[1] - wr.shape[1]) / 2, (im.shape[0] - wr.shape[0]) / 2)
                im[..., :3] = o.astype(np.uint8)
            im = rotate_img(im, -4 * (1 - e_io(prog(t, T['spread'] + 0.24, 0.75))))
        imsc = scaled(im, max(sc, 0.02))
        sh = drop_shadow(imsc, 6, 0.28, 20)
        over(img, sh, cx - imsc.shape[1] / 2 - 20 + 5, by - imsc.shape[0] - 20 + 7)
        over(img, imsc, cx - imsc.shape[1] / 2, by - imsc.shape[0])
    # breathing ticks in the gaps at 桌面才透气
    pb = prog(t, T['breathe'], 0.5)
    if pb > 0:
        canv = np.zeros((SH, SW, 4), np.uint8)
        for k, gx in enumerate([300, 470, 650]):
            pk = e_back(prog(t, T['breathe'] + k * 0.12, 0.4), 2)
            for j in range(3):
                yy = 300 + j * 34
                L = 26 * pk
                stroke(canv, [(gx - L / 2, yy - j * 4), (gx + L / 2, yy - j * 4 - 6)], (120, 150, 110), 5)
        over(img, canv, 0, 0)
    # swatches
    canv = np.zeros((SH, SW, 4), np.uint8)
    cyc = int((t - T['editor']) / 0.14) % 4 if t < T['zhuse'] + 0.22 else 0
    for i, (sx, sy) in enumerate(SW_POS):
        pop = 1.0
        r = 36
        if t < T['zhuse'] + 0.22 and i == cyc:
            r = 42
        if t >= T['zhuse'] + 0.22 and i == 0:
            r = 36 + 6 * (1 - prog(t, T['zhuse'] + 0.22, 0.3))
        cv2.circle(canv, (sx * 4, sy * 4), int(r * 4), T8(SWATCHES[i]) + (255,), -1, cv2.LINE_AA, shift=2)
        cv2.circle(canv, (sx * 4, sy * 4), int(r * 4), T8(INK) + (255,), 3, cv2.LINE_AA, shift=2)
    # ink ring on the chosen swatch (draws itself)
    pr = prog(t, T['zhuse'] + 0.22, 0.3)
    if pr > 0:
        ring = ellipse_pts(SW_POS[0][0], SW_POS[0][1], 50, 50, -90, -90 + 360 * e_out3(pr), 50)
        stroke(canv, wobble(ring, 2, 1.5, 4), INK, 6)
    over(img, canv, 0, 0)
    over(img, text_img('主色', NOTO_BOLD, 24, T8(INK)), 852, 74)
    # pill button (swap: old slides up and fades, new pops)
    changes = [T['editor'], T['simple'], T['card'], T['spread']]
    k = step
    pk = prog(t, changes[k], 0.22)
    cxp, cyp = (x0 + x1) / 2, 546
    if k > 0 and pk < 1:
        over_c(img, pill_img(PILLS[k - 1]), cxp, cyp - 30 * e_out3(pk), 1.0, opacity=1 - pk)
    if k == 0:
        pk = prog(t, T['editor'] + 0.05, 0.3)
    over_c(img, pill_img(PILLS[k]), cxp, cyp, 0.7 + 0.3 * e_back(pk, 2.0), opacity=clamp(pk * 3))
    # prompt bar: types the card request, send, then leaves
    t0 = T['card'] + 0.05
    if t0 <= t < T['card'] + 1.6:
        pin = e_out3(prog(t, t0, 0.2)); pout = e_in3(prog(t, T['card'] + 1.35, 0.25))
        bar_y = 396 + 30 * (1 - pin) + 40 * pout
        lay = np.zeros((84, 620, 4), np.uint8)
        pts = rrect_pts(2, 2, 618, 82, 40, 6)
        fill(lay, pts, (28, 28, 26))
        o = lay.astype(np.float32)
        msg = '加一张卡片：今天也慢慢来'
        nchar = int(clamp((t - t0 - 0.12) / 0.075, 0, len(msg)))
        txt = text_img(msg[:nchar], NOTO_MED, 30, (255, 255, 255)) if nchar else None
        lay3 = lay[..., :3].astype(np.float32)
        if txt is not None: over(lay3, txt, 26, 42 - txt.shape[0] / 2)
        caret_x = 26 + 8 + (text_w(msg[:nchar], NOTO_MED, 30) if nchar else 0)
        if int(t * 4) % 2 == 0 and nchar < len(msg):
            cv2.rectangle(lay3, (int(caret_x), 24), (int(caret_x) + 3, 60), (214, 245, 90), -1)
        lay[..., :3] = lay3.astype(np.uint8)
        # send button
        press = 1 - 0.18 * math.sin(math.pi * prog(t, T['card'] + 1.12, 0.2))
        cv2.circle(lay, (575 * 4, 42 * 4), int(30 * press * 4), T8(LIME) + (255,), -1, cv2.LINE_AA, shift=2)
        arr = [(562, 42), (588, 42)]
        stroke(lay, arr, INK, 5); stroke(lay, [(576, 30), (588, 42), (576, 54)], INK, 5)
        over(img, lay, (x0 + x1) / 2 - 310, bar_y, opacity=1 - pout)
    # cursor: hovers swatches, then send button, then out
    keys = [(T['editor'], (700, 520)), (T['zhuse'] + 0.1, (SW_POS[0][0] + 8, SW_POS[0][1] + 10)),
            (T['card'] + 0.3, (SW_POS[0][0] + 8, SW_POS[0][1] + 10)), (T['card'] + 1.05, (775, 470)),
            (T['spread'] - 0.2, (775, 470)), (T['spread'] + 0.8, (730, 520)), (24, (730, 520))]
    for (ta, pa), (tb, pb_) in zip(keys, keys[1:]):
        if ta <= t < tb:
            q = e_io(prog(t, ta, min(0.6, tb - ta)))
            cx, cy = lerp(pa[0], pb_[0], q), lerp(pa[1], pb_[1], q)
            break
    else:
        cx, cy = keys[-1][1]
    click_ring(img, SW_POS[0][0], SW_POS[0][1], prog(t, T['zhuse'] + 0.16, 0.4))
    click_ring(img, 775 + 0, 470, prog(t, T['card'] + 1.1, 0.4), INK)
    over(img, CURSOR, cx - 8, cy - 6)
    return img

# ---------------------------------------------------------------- laptop scene
def screen_at(t):
    if t < T['title'] - 0.06:
        return screen_result(t)
    if t < T['coffee'] - 0.06:
        a = screen_result(t) if t < T['title'] - 0.06 + PUSH else None
        return push(a, screen_title(t), prog(t, T['title'] - 0.06, PUSH)) if a is not None else screen_title(t)
    a = screen_title(t) if t < T['coffee'] - 0.06 + PUSH else None
    return push(a, screen_coffee(t), prog(t, T['coffee'] - 0.06, PUSH)) if a is not None else screen_coffee(t)

def push(a, b, p):
    p = e_io(p)
    off = int(round(SW * p))
    out = np.empty_like(b)
    out[:, :SW - off] = a[:, off:]
    out[:, SW - off:] = b[:, :off]
    # black seam line
    if 0 < off < SW: out[:, max(SW - off - 3, 0):SW - off + 3] = INK
    return out

def scene_laptop(t, editor=False):
    img = MATIMG.copy()
    over(img, LAPTOP_SH, -40 + 10, -40 + 14)
    over(img, LAPTOP, 0, 0)
    scr = screen_editor(t) if editor else screen_at(t)
    x0, y0 = SCR[0], SCR[1]
    w = min(SW, W - x0)
    # rounded-corner mask for the display
    img[y0:y0 + SH, x0:x0 + w] = img[y0:y0 + SH, x0:x0 + w] * (1 - SCRMASK[:, :w, None]) + scr[:, :w] * SCRMASK[:, :w, None]
    return img

def build_scrmask():
    m = np.zeros((SH * 4, SW * 4), np.uint8)
    fill(m[..., None].repeat(1, 2), rrect_pts(0, 0, SW * 4, SH * 4, 40, 6), (255,)) if False else None
    m3 = np.zeros((SH, SW, 3), np.uint8)
    fill(m3, rrect_pts(0, 0, SW - 1, SH - 1, 12, 5), (255, 255, 255))
    return m3[..., 0].astype(np.float32) / 255

SCRMASK = build_scrmask()

# ---------------------------------------------------------------- poster (7.04 – 13.46)
PY = 80   # whole poster group sits this far below its first layout
CARD = (170, 430 + PY, 930, 1470 + PY)
POSTERS = [  # anchor, title lines, block colour, band colour, item
    (T['poster'], ('留一盏', '暖光'), TERRA, OAT, 'lamp'),
    (T['coaster'], ('给咖啡', '一个家'), SAGE, SKY, 'coaster'),
    (T['pothos'], ('桌上', '有点绿'), PINK, BUTTER, 'pothos'),
    (T['three'], ('三样', '刚刚好'), RED, hexc('efe2c4'), None),
]
ITEM_POSE = {'lamp': (330, 1320 + PY, 700), 'coaster': (380, 1230 + PY, 600), 'pothos': (390, 1250 + PY, 600)}
THREE_POSE = [('lamp', 225, 1130 + PY, 430, 0.0, False), ('pothos', 290, 1450 + PY, 360, 0.07, False), ('coaster', 735, 1478 + PY, 290, 0.14, True)]
TITLE_SIZE, TITLE_TRACK = 172, -10
TITLE_X, TITLE_Y = 64, 262 + PY
LINE_H = 186
CARD_SHADOW = None

def poster_index(t):
    i = 0
    for k, P in enumerate(POSTERS):
        if t >= P[0] - 0.18 and k > 0: i = k
    return i

def card_layer(t):
    """Card (cream) with colour block and diagonal band, flat 760x1040 RGBA."""
    cw, ch = CARD[2] - CARD[0], CARD[3] - CARD[1]
    img = np.zeros((ch, cw, 3), np.float32); img[:] = CREAM
    rng = np.random.RandomState(9)
    img += CARD_NOISE[..., None]
    # current and previous colours; new colour wipes in from the top-right corner
    k = 0
    for j, P in enumerate(POSTERS):
        if t >= P[0] - 0.18: k = j
    def paint(block, band, frac):
        lay = np.zeros((ch, cw, 4), np.uint8)
        band_pts = [(-40, 900), (cw + 40, 330), (cw + 40, 470), (-40, 1040)]
        fill(lay, band_pts, band)
        fill(lay, [(cw * 0.46, 0), (cw, 0), (cw, ch * 0.36), (cw * 0.46, ch * 0.36)], block)
        if frac < 1:
            yy, xx = np.mgrid[0:ch, 0:cw]
            dd = np.hypot(xx - cw, yy) / math.hypot(cw, ch)
            m = np.clip((frac * 1.05 - dd) / 0.05, 0, 1)
            lay[..., 3] = (lay[..., 3] * m).astype(np.uint8)
        over(img, lay, 0, 0)
    if k > 0:
        paint(POSTERS[k - 1][2], POSTERS[k - 1][3], 1)
    paint(POSTERS[k][2], POSTERS[k][3], e_io(prog(t, POSTERS[k][0] - 0.18, 0.42)) if k > 0 else 1)
    out = np.zeros((ch, cw, 4), np.uint8)
    out[..., :3] = np.clip(img, 0, 255).astype(np.uint8); out[..., 3] = 255
    return out

CARD_NOISE = cv2.GaussianBlur(np.random.RandomState(4).randn(1040, 760).astype(np.float32), (0, 0), 1.0) * 4

def title_state(t):
    """Return (lines, nchars_visible, caret_on, caret_pos_line)."""
    k = 0
    for j, P in enumerate(POSTERS):
        if t >= P[0] - 0.18: k = j
    a = POSTERS[k][0]
    lines = POSTERS[k][1]
    total = sum(len(l) for l in lines)
    if k > 0 and t < a:
        old = POSTERS[k - 1][1]
        ot = sum(len(l) for l in old)
        n = int(round(ot * (1 - prog(t, a - 0.18, 0.18))))
        return old, n, True
    if k == 0 and t < a:
        return lines, 0, True
    n = int(math.ceil(total * prog(t, a, 0.45) - 1e-6))
    caret = (t < a + 0.45) or (t < a + 0.45 + 0.9 and int((t - a - 0.45) / 0.15) % 2 == 0)
    return lines, n, caret

def draw_title(img, t):
    lines, n, caret = title_state(t)
    x, y = TITLE_X, TITLE_Y
    cx, cy = x, y
    left = n
    for li, line in enumerate(lines):
        vis = line[:max(0, min(len(line), left))]
        ly = y + li * LINE_H
        if vis:
            im = text_img(vis, NOTO_BLACK, TITLE_SIZE, T8(INK), tracking=TITLE_TRACK)
            over(img, im, x - 8, ly - 8 - 30)
        if left <= len(line) and (left < len(line) or li == len(lines) - 1):
            cx = x + (text_w(vis, NOTO_BLACK, TITLE_SIZE, TITLE_TRACK) if vis else 0) + 10
            cy = ly
            left = -1
            break
        left -= len(line)
    if caret:
        cv2.rectangle(img, (int(cx), int(cy + 20)), (int(cx) + 14, int(cy + 20 + 170)), T8(RED), -1)

def scene_poster(t):
    img = np.full((H, W, 3), 255.0, np.float32)
    # brand line + tagline
    over(img, text_img('DESK NOTES', NOTO_BLACK, 34, T8(INK), tracking=6), 64, 150 + PY)
    over(img, text_img('No.01  小物件搭桌面', NOTO_MED, 26, (110, 104, 96)), 352, 157 + PY)
    tag = rotate_img(text_img('一张桌子 · 慢慢来 · 2026', NOTO_BOLD, 30, T8(INK), tracking=4), -90)
    over(img, tag, 1012 - tag.shape[1] / 2, 470 + PY)
    # card with breathing perspective tilt
    card = card_layer(t)
    lt = t - T['poster']
    ch, cw = card.shape[:2]
    d = 16 * math.sin(lt * 2 * math.pi / 3.2)
    r = 6 * math.sin(lt * 2 * math.pi / 4.1 + 1)
    x0, y0, x1, y1 = CARD
    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    dst = np.float32([[x0 - r, y0 + d], [x1 - r, y0 - d], [x1 + r, y1 + d], [x0 + r, y1 - d]])
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(card, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
    sh = cv2.GaussianBlur(warped[..., 3].astype(np.float32) / 255, (0, 0), 16) * 0.25
    sh = np.roll(sh, (18, 12), (0, 1))
    img *= (1 - sh[..., None]); img += (20 * sh)[..., None]
    over(img, warped, 0, 0)
    # title (under items)
    draw_title(img, t)
    k = poster_index(t)
    # items: current one drops in; previous one shrinks away during the delete
    behind, front = [], []
    for j, P in enumerate(POSTERS):
        a = P[0]
        nxt = POSTERS[j + 1][0] if j + 1 < len(POSTERS) else 99
        if t < a or t >= nxt:
            if not (j == k - 1 and t < POSTERS[k][0]): continue
        out_p = prog(t, nxt - 0.18, 0.18)
        if P[4]:
            cx, by, h = ITEM_POSE[P[4]]
            pd = prog(t, a, 0.42)
            dy = -380 * (1 - e_back(pd, 1.7))
            sc = 1 - 0.15 * out_p
            behind.append((P[4], cx, by + dy, h * sc, clamp(pd * 6) * (1 - out_p), 1.0))
        else:
            for n, cx, by, h, delay, fr in THREE_POSE:
                pf = prog(t, a + delay, 0.42)
                (front if fr else behind).append((n, cx, by, h, clamp(pf * 4), max(0.02, e_back(pf, 1.6))))
    def put(lst):
        for n, cx, by, h, op, sx in lst:
            if op <= 0: continue
            im = fit_h(n, int(h))
            if n == 'lamp':
                glow_c = (cx, by - h * 0.62)
                g = LAMP_GLOW
                over(img, g, glow_c[0] - g.shape[1] / 2, glow_c[1] - g.shape[0] / 2, op * 0.9)
            if sx < 0.999:
                im = cv2.resize(im, (max(1, int(im.shape[1] * sx)), im.shape[0]), interpolation=cv2.INTER_AREA)
            sh = drop_shadow(im, 10, 0.30, 30)
            over(img, sh, cx - im.shape[1] / 2 - 30 + 10, by - im.shape[0] - 30 + 12, op)
            over(img, im, cx - im.shape[1] / 2, by - im.shape[0], op)
    put(behind)
    return img, front

def build_glow():
    s = 700
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    d = np.hypot(xx - s / 2, yy - s / 2) / (s / 2)
    a = np.clip(1 - d, 0, 1) ** 2 * 0.55
    return solid_rgba(a, (255, 214, 140))

LAMP_GLOW = build_glow()

def poster_front(img, t, front, fade=1.0):
    for n, cx, by, h, op, sx in front:
        op *= fade
        if op <= 0: continue
        im = fit_h(n, int(h))
        if sx < 0.999:
            im = cv2.resize(im, (max(1, int(im.shape[1] * sx)), im.shape[0]), interpolation=cv2.INTER_AREA)
        sh = drop_shadow(im, 10, 0.30, 30)
        over(img, sh, cx - im.shape[1] / 2 - 30 + 10, by - im.shape[0] - 30 + 12, op)
        over(img, im, cx - im.shape[1] / 2, by - im.shape[0], op)
    # cursor drifting to each product, click ring on arrival
    keys = [(T['poster'] + 0.1, (980, 1640 + PY)), (T['poster'] + 0.95, (300, 900 + PY)),
            (T['coaster'] + 0.15, (300, 900 + PY)), (T['coaster'] + 0.75, (250, 820 + PY)),
            (T['pothos'] + 0.15, (250, 820 + PY)), (T['pothos'] + 0.75, (260, 1010 + PY)),
            (T['three'] + 0.3, (260, 1010 + PY)), (T['three'] + 1.0, (760, 1330 + PY)), (T['editor'], (760, 1330 + PY))]
    cx, cy = keys[0][1]
    for (ta, pa), (tb, pb) in zip(keys, keys[1:]):
        if ta <= t < tb:
            q = e_io(prog(t, ta, tb - ta)); cx, cy = lerp(pa[0], pb[0], q), lerp(pa[1], pb[1], q); break
        if t >= tb: cx, cy = pb
    for tc, (px, py) in [(T['poster'] + 0.95, (300, 900 + PY)), (T['coaster'] + 0.75, (250, 820 + PY)),
                         (T['pothos'] + 0.75, (260, 1010 + PY)), (T['three'] + 1.0, (760, 1330 + PY))]:
        click_ring(img, px, py, prog(t, tc, 0.4))
    if fade > 0: over(img, CURSOR_BIG, cx - 12, cy - 8, fade)

CURSOR_BIG = cv2.resize(cursor_img(), (70, 98), interpolation=cv2.INTER_CUBIC)

# ---------------------------------------------------------------- ask (23.167 – end)
def ask_back(t):
    img = MATIMG.copy()
    return img

def ask_front(img, t):
    # 评论区打 in white script above her head, chars written as spoken
    line = text_img('评论区打', MASHAN, 120, (255, 255, 255))
    sh = solid_rgba(cv2.GaussianBlur(line[..., 3].astype(np.float32) / 255, (0, 0), 3) * 0.5, (14, 30, 18))
    p = prog(t, T['ask_line'], 24.26 - T['ask_line'])
    w = write_on(line, p, 4)
    if w is not None:
        x = 540 - line.shape[1] / 2
        over(img, write_on(sh, p, 4), x + 4, 228 + 6)
        over(img, w, x, 228)
    # 桌面: biggest word, marker letters, pops on the word
    big = ZHUOMIAN
    pz = prog(t, T['zhuomian'], 0.36)
    if pz > 0:
        over_c(img, big, 540, 1318, 0.55 + 0.45 * e_back(pz, 2.2), opacity=clamp(pz * 5))
    # red scribble underlines drawing in
    canv = np.zeros((H, W, 4), np.uint8)
    s1 = wobble([(250, 1522), (420, 1510), (600, 1518), (830, 1504)], 21, 5, 6)
    s2 = wobble([(300, 1558), (480, 1550), (680, 1556), (800, 1546)], 22, 5, 6)
    stroke(canv, s1, RED, 16, e_out3(prog(t, T['zhuomian'] + 0.18, 0.30)))
    stroke(canv, s2, RED, 12, e_out3(prog(t, T['zhuomian'] + 0.40, 0.28)))
    # sparkles beside 桌面
    for (x, y, r, d) in [(150, 1190, 40, 0.25), (940, 1440, 32, 0.4), (930, 1170, 22, 0.55)]:
        pp = e_back(prog(t, T['zhuomian'] + d, 0.3), 2)
        draw_sparkle(canv, x, y, r * pp * (1 + 0.1 * math.sin(t * 7 + x)), (255, 245, 170))
    over(img, canv, 0, 0)
    q = text_img('你想先换哪样？', WENKAI, 76, (255, 255, 255))
    qs = solid_rgba(cv2.GaussianBlur(q[..., 3].astype(np.float32) / 255, (0, 0), 3) * 0.5, (14, 30, 18))
    pq = prog(t, T['which'], 26.16 - T['which'])
    wq = write_on(q, pq, 7)
    if wq is not None:
        x = 540 - q.shape[1] / 2
        over(img, write_on(qs, pq, 7), x + 3, 1600 + 5)
        over(img, wq, x, 1600)

def build_zhuomian():
    t = text_img('桌面', KUAILE, 330, (255, 255, 255), tracking=10, stroke=14, stroke_color=T8(INK))
    sh = np.zeros_like(t); sh[..., :3] = T8(INK); sh[..., 3] = t[..., 3]
    out = np.zeros((t.shape[0] + 16, t.shape[1] + 16, 4), np.uint8)
    o3 = np.zeros(out.shape[:2] + (3,), np.float32); al = np.zeros(out.shape[:2], np.float32)
    for src, x, y in [(sh, 14, 14), (t, 0, 0)]:
        a = src[..., 3].astype(np.float32) / 255
        hh, ww = a.shape
        o3[y:y + hh, x:x + ww] = o3[y:y + hh, x:x + ww] * (1 - a[..., None]) + src[..., :3] * a[..., None]
        al[y:y + hh, x:x + ww] = al[y:y + hh, x:x + ww] * (1 - a) + a
    out[..., :3] = np.clip(o3 / np.maximum(al, 1e-4)[..., None], 0, 255).astype(np.uint8)
    out[..., 3] = (al * 255).astype(np.uint8)
    return rotate_img(out, 3)

ZHUOMIAN = build_zhuomian()

# ---------------------------------------------------------------- frame
def iris(a, b, p, cx, cy):
    """b revealed through a growing circle over a."""
    if p <= 0: return a
    if p >= 1: return b
    R = math.hypot(max(cx, W - cx), max(cy, H - cy)) * e_in3(p) if False else math.hypot(max(cx, W - cx), max(cy, H - cy)) * (p ** 1.6)
    m = np.zeros((H, W), np.uint8)
    cv2.circle(m, (int(cx * 4), int(cy * 4)), int(R * 4), 255, -1, cv2.LINE_AA, shift=2)
    m = m.astype(np.float32)[..., None] / 255
    # ink ring on the iris edge
    out = a * (1 - m) + b * m
    cv2.circle(out, (int(cx * 4), int(cy * 4)), int(R * 4), T8(INK), 10, cv2.LINE_AA, shift=2)
    return out

def render(fi):
    t = fi / FPS
    front = []
    if t >= T['ask']:
        img = ask_back(t)
        place_presenter(img, t, fi)
        ask_front(img, t)
        return img
    scr_c = ((SCR[0] + SCR[2]) / 2, (SCR[1] + SCR[3]) / 2)
    a0, a1 = T['poster'] - IRIS, T['editor'] - IRIS
    if t < a0:
        img = scene_laptop(t)
    elif t < T['poster']:
        lap = scene_laptop(t)
        pos, front = scene_poster(t)
        img = iris(lap, pos, prog(t, a0, IRIS), *scr_c)
    elif t < a1:
        img, front = scene_poster(t)
        z = 1 + 0.05 * e_io(prog(t, T['poster'], T['editor'] - T['poster']))
        if z > 1.0005:
            M = np.float32([[z, 0, (1 - z) * 540], [0, z, (1 - z) * 980]])
            img = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    elif t < T['editor']:
        pos, front = scene_poster(t)
        z = 1.05
        M = np.float32([[z, 0, (1 - z) * 540], [0, z, (1 - z) * 980]])
        pos = cv2.warpAffine(pos, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        lap = scene_laptop(t, editor=True)
        img = iris(pos, lap, prog(t, a1, IRIS), *scr_c)
    else:
        img = scene_laptop(t, editor=True)
    place_presenter(img, t, fi)
    if T['poster'] - 0.02 <= t < T['editor']:
        # front-layer items and cursor leave with the outgoing iris
        poster_front(img, t, front, 1 - prog(t, T['editor'] - IRIS, IRIS * 0.4))
    return img

def main():
    global PRES
    PRES = Presenter()
    if sys.argv[1] == '--still':
        ts = [float(x) for x in sys.argv[2].split(',')]
        for t in ts:
            fi = int(round(t * FPS))
            img = render(fi)
            out = sys.argv[3] if len(ts) == 1 else sys.argv[3].replace('.png', f'_{t:05.2f}.png')
            Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(out)
        return
    a, b, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    import subprocess
    p = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                          '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'medium',
                          '-crf', '12', '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709',
                          '-color_trc', 'bt709', out], stdin=subprocess.PIPE)
    for fi in range(a, b):
        img = render(fi)
        p.stdin.write(np.clip(img + 0.5, 0, 255).astype(np.uint8).tobytes())
    p.stdin.close()
    code = p.wait()
    if code:
        raise SystemExit(code)

if __name__ == '__main__':
    main()
