"""DEV-only neighbour preview of the candidate (mn=0.05, X=30, Y=0, BTC hedge, cap=0.33)."""
from lib import *
base=dict(mn=0.05,X=30,Y=0,cap=0.33)
def run(mn,X,Y,cap,h=True):
    tr=candidates(mn,X=X,Y=Y); r,_=book(tr,cap=cap,hedge=h); return stats(monthly(r,DEV_START,DEV_END))
print('base',run(**base))
for k,vals in dict(mn=(0.025,0.0375,0.0625,0.075),X=(15,22,38,45),Y=(1,3,7),cap=(0.165,0.25,0.41,0.5)).items():
    for v in vals:
        p=dict(base); p[k]=v; s=run(**p); print(k,v,s['geo'],s['median'],s['up'],s['maxdd'],s['t'])
for X in (20,24,26,28,32,35,40): s=run(0.05,X,0,0.33); print('X',X,s['geo'],s['maxdd'])
