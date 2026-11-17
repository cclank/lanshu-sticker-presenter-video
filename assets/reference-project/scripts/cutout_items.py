# Cut the four products out of items_sheet.png with rembg isnet-general-use, trim, save RGBA.
import sys, numpy as np
from PIL import Image
from rembg import new_session, remove
src = Image.open('assets/items_sheet.png').convert('RGB')
W, H = src.size
sess = new_session('isnet-general-use')
names = {(0,0):'lamp',(1,0):'coaster',(0,1):'pothos',(1,1):'card'}
for (cx,cy),n in names.items():
    tile = src.crop((cx*W//2, cy*H//2, (cx+1)*W//2, (cy+1)*H//2))
    out = remove(tile, session=sess, post_process_mask=True)
    a = np.array(out)
    al = a[...,3]
    al[al<12] = 0
    ys, xs = np.where(al>0)
    box = (xs.min(), ys.min(), xs.max()+1, ys.max()+1)
    Image.fromarray(a).crop(box).save(f'build/item_{n}.png')
    print(n, box)
