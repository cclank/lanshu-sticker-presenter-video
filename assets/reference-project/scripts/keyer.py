# Green-difference key for take.mp4 -> despilled RGB + alpha at source size.
import numpy as np, cv2

def key_frame(bgr, lo=28.0, hi=62.0):
    f = bgr.astype(np.float32)
    b, g, r = f[...,0], f[...,1], f[...,2]
    mx = np.maximum(r, b)
    diff = g - mx
    a = 1.0 - np.clip((diff - lo) / (hi - lo), 0, 1)
    a = cv2.GaussianBlur(a, (0,0), 0.7)
    # despill: green never above max(r,b)
    g2 = np.minimum(g, mx)
    out = np.stack([b, g2, r], -1)
    return out, a

def clean_alpha(a):
    # keep the largest connected body; drop specks
    m = (a > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        keep = np.zeros(n, bool)
        areas = stats[1:, cv2.CC_STAT_AREA]
        big = areas.max()
        keep[1:] = areas > max(400, big*0.02)
        body = keep[lab].astype(np.uint8)
        body = cv2.dilate(body, np.ones((5,5),np.uint8))
        a = a * body
    return a
