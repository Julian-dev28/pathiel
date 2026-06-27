"""B15 regime_switch_HMM — fit a 2-state diagonal-Gaussian HMM on BTC (ret, |ret|) via EM on the
TRAIN half, decode CAUSALLY (filtering), then test whether each live edge's EV concentrates in a
state on the OOS (test) half -> regime-conditional size multiplier."""
import math, statistics
import alpha_lib as A

d = A.load_dataset()
coins=[c for c in d["coins"] if len(A.candles(d,c,"1d"))==301]
N=301
cl={c:[b[A.C] for b in A.candles(d,c,"1d")] for c in coins}
ret={c:[cl[c][t]/cl[c][t-1]-1 if cl[c][t-1] else 0 for t in range(1,N)] for c in coins}
RL=N-1
br=ret["BTC"]
X=[[br[t], abs(br[t])] for t in range(RL)]

def gpdf(x, mu, var):
    p=1.0
    for k in range(len(x)):
        v=max(var[k],1e-8)
        p*=math.exp(-0.5*(x[k]-mu[k])**2/v)/math.sqrt(2*math.pi*v)
    return max(p,1e-300)

def em(data, S=2, iters=30):
    n=len(data); dim=len(data[0])
    # init: split by sorted abs-ret
    order=sorted(range(n), key=lambda i:data[i][1])
    lab=[0]*n
    for r,i in enumerate(order): lab[i]=0 if r<n//2 else 1
    def msteps(g2):  # not used
        pass
    mu=[[statistics.mean(data[i][k] for i in range(n) if lab[i]==j) for k in range(dim)] for j in range(S)]
    var=[[statistics.pvariance([data[i][k] for i in range(n) if lab[i]==j]) or 1e-4 for k in range(dim)] for j in range(S)]
    pi=[0.5,0.5]; Amat=[[0.9,0.1],[0.1,0.9]]
    for _ in range(iters):
        # forward-backward (scaled)
        alpha=[[0.0]*S for _ in range(n)]; cscale=[0.0]*n
        for j in range(S): alpha[0][j]=pi[j]*gpdf(data[0],mu[j],var[j])
        cscale[0]=sum(alpha[0]) or 1e-300
        for j in range(S): alpha[0][j]/=cscale[0]
        for t in range(1,n):
            for j in range(S):
                alpha[t][j]=gpdf(data[t],mu[j],var[j])*sum(alpha[t-1][i]*Amat[i][j] for i in range(S))
            cscale[t]=sum(alpha[t]) or 1e-300
            for j in range(S): alpha[t][j]/=cscale[t]
        beta=[[0.0]*S for _ in range(n)]
        for j in range(S): beta[n-1][j]=1.0
        for t in range(n-2,-1,-1):
            for i in range(S):
                beta[t][i]=sum(Amat[i][j]*gpdf(data[t+1],mu[j],var[j])*beta[t+1][j] for j in range(S))/cscale[t+1]
        gamma=[[alpha[t][j]*beta[t][j] for j in range(S)] for t in range(n)]
        for t in range(n):
            s=sum(gamma[t]) or 1e-300
            for j in range(S): gamma[t][j]/=s
        # xi sums
        xis=[[0.0]*S for _ in range(S)]
        for t in range(n-1):
            denom=0.0; tmp=[[0.0]*S for _ in range(S)]
            for i in range(S):
                for j in range(S):
                    tmp[i][j]=alpha[t][i]*Amat[i][j]*gpdf(data[t+1],mu[j],var[j])*beta[t+1][j]
                    denom+=tmp[i][j]
            denom=denom or 1e-300
            for i in range(S):
                for j in range(S): xis[i][j]+=tmp[i][j]/denom
        # M-step
        pi=[gamma[0][j] for j in range(S)]
        for i in range(S):
            row=sum(xis[i]) or 1e-300
            for j in range(S): Amat[i][j]=xis[i][j]/row
        for j in range(S):
            gs=sum(gamma[t][j] for t in range(n)) or 1e-300
            mu[j]=[sum(gamma[t][j]*data[t][k] for t in range(n))/gs for k in range(dim)]
            var[j]=[max(sum(gamma[t][j]*(data[t][k]-mu[j][k])**2 for t in range(n))/gs,1e-6) for k in range(dim)]
    return pi,Amat,mu,var

split=int(RL*0.6)
pi,Amat,mu,var=em(X[:split], iters=25)
# identify which state is "calm" (lower abs-ret mean) vs "turbulent"
calm = 0 if mu[0][1]<mu[1][1] else 1
print(f"HMM fit on train[:{split}]  state means (ret,|ret|): s0={[round(m,4) for m in mu[0]]} s1={[round(m,4) for m in mu[1]]}  calm=s{calm}")

# causal filtering decode over FULL series using train params
def decode():
    n=len(X); f=[0.5,0.5]; states=[]
    for t in range(n):
        nf=[gpdf(X[t],mu[j],var[j])*(f[0]*Amat[0][j]+f[1]*Amat[1][j] if t>0 else pi[j]) for j in range(2)]
        s=sum(nf) or 1e-300; nf=[x/s for x in nf]; f=nf
        states.append(0 if nf[0]>nf[1] else 1)
    return states
st=decode()

# live edges on OOS (test half): XS book + long_all + mom7, EV by decoded state
L,NSIDE=14,8
def book_ret(t):
    if t-1-L<0: return None
    sc=sorted((cl[c][t-1]/cl[c][t-1-L]-1 if cl[c][t-1-L] else 0,c) for c in coins)
    sh=[c for _,c in sc[:NSIDE]]; lo=[c for _,c in sc[-NSIDE:]]
    return statistics.mean(ret[c][t] for c in lo)-statistics.mean(ret[c][t] for c in sh)
from collections import defaultdict
acc=defaultdict(lambda: defaultdict(list))
for t in range(L+1, RL):
    half = "train" if t<split else "TEST"
    lbl = "calm" if st[t]==calm else "turb"
    b=book_ret(t)
    if b is not None: acc[("xs_book",half)][lbl].append(b)
    acc[("long_all",half)][lbl].append(statistics.mean(ret[c][t] for c in coins))
print(f"\n{'edge':9s} {'half':5s} {'calm_n':>6s} {'calm_ret%':>9s} {'turb_n':>6s} {'turb_ret%':>9s}")
for edge in ["xs_book","long_all"]:
    for half in ["train","TEST"]:
        c=acc[(edge,half)]["calm"]; tt=acc[(edge,half)]["turb"]
        cm=statistics.mean(c)*100 if c else 0; tm=statistics.mean(tt)*100 if tt else 0
        print(f"{edge:9s} {half:5s} {len(c):6d} {cm:9.4f} {len(tt):6d} {tm:9.4f}")
