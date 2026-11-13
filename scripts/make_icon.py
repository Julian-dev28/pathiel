#!/usr/bin/env python3
"""Draws .claude-plugin/icon.png (1024 px, the plugin directory listing icon)
with no image library: a bracket, target on top, dashed entry, stop below,
price running to the target. Colors are the Desk page tokens.

    python3 scripts/make_icon.py .claude-plugin/icon.png
"""
import struct
import sys
import zlib

N=1024; S=2; W=N*S
def hexc(h): h=h.lstrip('#'); return tuple(int(h[i:i+2],16) for i in (0,2,4))
BG=hexc('0a1211'); UP=hexc('4fd1b8'); DOWN=hexc('e8756d'); INK=hexc('9aaaa5')
img=[[BG]*W for _ in range(W)]
def rect(x0,y0,x1,y1,c,r=0):
    for y in range(int(y0*S),int(y1*S)):
        row=img[y]
        for x in range(int(x0*S),int(x1*S)):
            if r:
                cx=min(max(x/S,x0+r),x1-r); cy=min(max(y/S,y0+r),y1-r)
                if (x/S-cx)**2+(y/S-cy)**2>r*r: continue
            row[x]=c
def path(pts,w,c):
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]; h=w/2
    for y in range(int((min(ys)-h)*S),int((max(ys)+h)*S)+1):
        py=y/S; row=img[y]
        for x in range(int((min(xs)-h)*S),int((max(xs)+h)*S)+1):
            px=x/S; best=1e9
            for (ax,ay),(bx,by) in zip(pts,pts[1:]):
                dx,dy=bx-ax,by-ay; t=max(0,min(1,((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy)))
                d=(px-ax-t*dx)**2+(py-ay-t*dy)**2
                if d<best: best=d
            if best<=h*h: row[x]=c
# target (top, teal), entry (dashed, grey), stop (bottom, red)
rect(176,236,848,284,UP,24)
for i in range(9):
    x=176+i*(848-44-176)/8; rect(x,580,x+44,604,INK,12)
rect(176,740,848,788,DOWN,24)
# price path from entry up to target
path([(204,592),(392,512),(520,568),(752,262)],56,UP)
# downsample
rows=[]
for y in range(N):
    line=bytearray([0])
    for x in range(N):
        acc=[0,0,0]
        for dy in range(S):
            for dx in range(S):
                p=img[y*S+dy][x*S+dx]; acc[0]+=p[0];acc[1]+=p[1];acc[2]+=p[2]
        line+=bytes(v//(S*S) for v in acc)
    rows.append(bytes(line))
def chunk(t,d): return struct.pack('>I',len(d))+t+d+struct.pack('>I',zlib.crc32(t+d)&0xffffffff)
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',N,N,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(rows),9))+chunk(b'IEND',b'')
open(sys.argv[1],'wb').write(png)
