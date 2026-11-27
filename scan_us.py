# 미국 전 종목(나스닥·뉴욕·아메리칸 거래소 상장 주식+ETF) 병하양봉 신호 검색기
# 사용: python scan_us.py            → 가장 최근 '끝난' 일봉 기준으로 돌파★ / 되돌림 1~5위 (전체 목록은 scan_out\ 에 CSV)
# 규칙은 지표(병하양봉 v8.3)와 같음:
#   돌파★  = 120일선(SMA) 5봉 전보다 위 + 종가 > 직전 60봉 최고가 + 전날 종가 <= 그 전날 기준 60봉 최고가 + 거래량 >= 직전 20일 평균 x2 (x3 = ★★)
#   되돌림 = 돌파★ 뒤 3~20봉 안, 저가 <= 돌파선 + 0.3ATR, 종가 > 돌파선 (돌파★마다 처음 1번)
#   손절   = min(기준선 - 0.2ATR, 종가 - 0.3ATR) · 절반 익절 = 종가 + (종가 - 손절)
# 거래가 너무 적은 종목은 뺌: 종가 >= $5, 20일 평균 거래대금 >= $10M
import os, sys, json, time, csv, urllib.request, urllib.parse, datetime as dt, concurrent.futures as cf
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, 'scan_out'); os.makedirs(OUT, exist_ok=True)
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
def get(url, tries=3):
    for k in range(tries):
        try: return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30).read()
        except Exception as e: err = e; time.sleep(1.5 * (k + 1))
    raise err
BAD = ('warrant', 'right', 'unit', 'preferred', 'notes due', 'debenture', 'depositary shares representing',
       # 레버리지·인버스 ETF (개잡주·도박성 제외)
       ' 2x', ' 3x', '-2x', '-3x', '1.5x', '1.25x', '2x ', '3x ', 'ultra', 'leveraged', 'inverse', 'bear ', ' bull ', 'daily target', 'short ')
MINP, MINDV = 10, 20e6   # 주가 $10 이상, 20일 평균 거래대금 $2천만 이상 (동전주·거래 적은 종목 제외)
# 2026-11-28 '움직이는 종목만': 하루 평균 변동폭(ATR) 3.5% 이상 + 6개월(126봉) 상승률 +20% 이상
# bt_tight.py 검증 (미국 2천여 종목, DEV/VAL × 2016 전/후 4칸 모두 기본보다 좋음):
#   돌파★  평균 +1.2/+2.4/+0.6/+0.9% → +3.2/+5.5/+2.3/+3.1%, 20일 안 최고 상승 +7~8% → +13~17%
#   되돌림 평균 +0.6/+1.0/+0.1/+0.7% → +1.4/+2.8/+0.9/+2.3%
#   대형주만(거래대금 $100M↑, S&P500·나스닥100급)은 효과 없음 (VAL 앞 -0.5%) · 저변동(ATR 2%↓)이 '안 움직이는 종목'의 원인
MINATR, MINR126 = 3.5, 0.20
def symbols():
    S = {}
    for url, sc, nc, ec, tc in (('https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt', 0, 1, 6, 3),
                                ('https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt', 0, 1, 4, 6)):
        for line in get(url).decode('utf-8', 'replace').splitlines()[1:]:
            p = line.split('|')
            if len(p) < 7 or line.startswith('File Creation'): continue
            s, name, test = p[sc], p[nc], p[tc]
            if test == 'Y' or any(b in name.lower() for b in BAD) or not s or '$' in s: continue
            S[s.replace('.', '-')] = (name[:60], p[ec] == 'Y')
    return S
DATES = []   # 거름망을 통과한 종목들의 마지막(끝난) 봉 날짜 → 가장 최근 날짜 = 기준일
def mid(h, l, L, i): return (max(h[i-L+1:i+1]) + min(l[i-L+1:i+1])) / 2
def analyze(t):
    try:
        j = json.loads(get(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(t)}?range=2y&interval=1d'))
        r = j['chart']['result'][0]; q = r['indicators']['quote'][0]
    except Exception: return None
    B = [(ts, h, l, c, v) for ts, h, l, c, v in zip(r.get('timestamp') or [], q['high'], q['low'], q['close'], q['volume']) if None not in (h, l, c) and c > 0]
    # 오늘 장중(안 끝난) 봉은 뺌
    meta = r.get('meta', {}); per = (meta.get('currentTradingPeriod') or {}).get('regular') or {}
    now = time.time()
    if B and per and per.get('start', 0) <= B[-1][0] and now < per.get('end', 0): B = B[:-1]
    if len(B) < 200: return None
    T = [x[0] for x in B]; h = [x[1] for x in B]; l = [x[2] for x in B]; c = [x[3] for x in B]; v = [x[4] or 0 for x in B]; n = len(c); i = n - 1
    va = lambda k: sum(v[k-20:k]) / 20
    if c[i] < MINP or va(i) * c[i] < MINDV: return None
    tr = [h[0]-l[0]] + [max(h[k]-l[k], abs(h[k]-c[k-1]), abs(l[k]-c[k-1])) for k in range(1, n)]
    A = [0.0]*n; p = 0
    for k in range(n): p = (p*k + tr[k])/(k+1) if k < 14 else (p*13 + tr[k])/14; A[k] = p
    sma120 = lambda k: sum(c[k-119:k+1]) / 120
    def star(k):
        if k < 130: return None
        hi = max(h[k-60:k]); hp = max(h[k-61:k-1])
        if c[k] > hi and c[k-1] <= hp and va(k) > 0 and v[k] >= 2*va(k) and sma120(k) > sma120(k-5): return hi, v[k]/va(k)
        return None
    kij = mid(h, l, 26, i); stp = min(kij - 0.2*A[i], c[i] - 0.3*A[i]); R = c[i] - stp
    cloud = min((mid(h, l, 9, i-25) + mid(h, l, 26, i-25)) / 2, mid(h, l, 52, i-25))   # 완익(나머지 정리) 기준선 = 지금 구름 아래쪽
    day = dt.datetime.fromtimestamp(T[i], dt.timezone.utc).strftime('%Y-%m-%d'); DATES.append(day)   # 시세 받은 종목 수·기준일은 거름망 전에 셈
    if A[i] / c[i] * 100 < MINATR or c[i] / c[i-126] - 1 < MINR126: return None   # 안 움직이는 종목·힘 없는 종목 제외 (v2026-11-28)
    base = dict(t=t, date=day, close=round(c[i], 2), stop=round(stp, 2), stop_pct=round(-R/c[i]*100, 1), half=round(c[i]+R, 2), half_pct=round(R/c[i]*100, 1), cloud=round(cloud, 2), cloud_pct=round((cloud/c[i]-1)*100, 1),
                dvol_m=round(va(i)*c[i]/1e6, 1), atr_pct=round(A[i]/c[i]*100, 1), r126=round((c[i]/c[i-126]-1)*100))
    out = []
    s = star(i)
    if s: out.append(dict(base, kind='돌파★★' if s[1] >= 3 else '돌파★', vol_x=round(s[1], 1), line=round(s[0], 2)))
    # 되돌림: 가장 최근 돌파★(3~20봉 전) 뒤 첫 되돌림이 오늘
    for k in range(i-3, i-21, -1):
        s2 = star(k)
        if not s2: continue
        lvl = s2[0]; first = True
        for m in range(k+3, i):
            if l[m] <= lvl + 0.3*A[m] and c[m] > lvl: first = False; break
        if any(star(m) for m in range(k+1, i)): first = False   # 더 새 돌파★가 있으면 그쪽 기준
        if first and l[i] <= lvl + 0.3*A[i] and c[i] > lvl:
            out.append(dict(base, kind='되돌림', vol_x=round(s2[1], 1), line=round(lvl, 2), star_ago=i-k, star_kind='★★' if s2[1] >= 3 else '★'))
        break
    return out
if __name__ == '__main__':
    t0 = time.time()
    if len(sys.argv) > 1:   # python scan_us.py NVDA SOXX … → 그 종목들만 (신호 없으면 안 나옴)
        tick = [a.upper() for a in sys.argv[1:]]; S = {a: (a, False) for a in tick}
    else: S = symbols(); tick = sorted(S)
    print('종목 수', len(tick), flush=True)
    res = []; done = 0
    with cf.ThreadPoolExecutor(int(os.environ.get('SCAN_THREADS', '10'))) as ex:
        for o in ex.map(analyze, tick):
            done += 1
            if o: res += o
            if done % 500 == 0: print(f'{done}/{len(tick)} · 신호 {len(res)} · {time.time()-t0:.0f}초', flush=True)
    for x in res: x['name'], x['etf'] = S[x['t']][0], 'ETF' if S[x['t']][1] else ''
    if len(DATES) < (5 if len(sys.argv) > 1 else 500):   # 시세를 거의 못 받음 = 야후 차단·인터넷 문제 → 옛 결과를 덮어쓰지 않고 실패로 끝냄
        if len(sys.argv) == 1 or not DATES: print(f'시세를 받은 종목이 {len(DATES)}개뿐이라 중단'); sys.exit(1)
    from collections import Counter
    day = Counter(DATES).most_common(1)[0][0] if len(sys.argv) == 1 else max(DATES)   # 대부분 종목의 마지막 봉 날짜
    brk = sorted([x for x in res if x['kind'].startswith('돌파') and x['date'] == day], key=lambda x: (x['kind'] != '돌파★★', -x['vol_x']))
    ret = sorted([x for x in res if x['kind'] == '되돌림' and x['date'] == day], key=lambda x: (x['star_kind'] != '★★', -x['stop_pct'] if False else abs(x['stop_pct'])))
    f = os.path.join(OUT, f'scan_{day}.csv' if len(sys.argv) == 1 else 'scan_tickers.csv')   # 종목 몇 개만 볼 땐 하루치 목록을 덮어쓰지 않음
    with open(f, 'w', newline='', encoding='utf-8-sig') as fo:
        cols = ['kind', 't', 'name', 'etf', 'date', 'close', 'vol_x', 'line', 'stop', 'stop_pct', 'half', 'half_pct', 'cloud', 'cloud_pct', 'atr_pct', 'r126', 'dvol_m', 'star_ago', 'star_kind']
        w = csv.DictWriter(fo, fieldnames=cols, extrasaction='ignore'); w.writeheader(); [w.writerow(x) for x in brk + ret]
    print(f'\n기준일 {day} · 돌파 {len(brk)}개 · 되돌림 {len(ret)}개 · {time.time()-t0:.0f}초 · 전체 목록 {f}')
    if len(sys.argv) == 1:   # 전체 검색이면 여의도병하 '종목 찾기' 탭용 파일도 저장 (GitHub에서는 SCAN_OUT_DIR 폴더에)
        ODIR = os.environ.get('SCAN_OUT_DIR') or os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop', '여의도병하')
        os.makedirs(ODIR, exist_ok=True); YDB = os.path.join(ODIR, 'scan_latest.json')
        try:
            js = dict(date=day, at=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M'), scanned=len(tick), filters=f'주가 ${MINP} 이상 · 20일 평균 거래대금 ${MINDV/1e6:.0f}M 이상 · 하루 변동폭(ATR) {MINATR}% 이상 · 6개월 +{MINR126*100:.0f}% 이상 · 레버리지/인버스 ETF 제외',
                      brk=brk, ret=ret)
            tmp = YDB + '.tmp'; open(tmp, 'w', encoding='utf-8').write(json.dumps(js, ensure_ascii=False)); os.replace(tmp, YDB); print('여의도병하 저장', YDB)
            # 브리핑용 짧은 글 (구글 드라이브에 올려서 아침·저녁 브리핑이 읽음)
            L = [f'병하 종목찾기 · 미국 전 종목 · {day} 종가 기준 · 검색 {js["at"]} KST · {len(tick)}종목 · {js["filters"]}', '']
            for title, A, k in (('① 돌파 (돌파★★ 먼저, 거래량 많은 순)', brk, 'brk'), ('② 되돌림·눌림목 (돌파★★ 뒤 먼저, 손절 폭 좁은 순)', ret, 'ret')):
                L.append(f'{title} — 전체 {len(A)}개')
                if not A: L.append('  없음')
                for r, x in enumerate(A[:5], 1):
                    L.append(f"  {r}. {x['t']} {x['kind']} ({x['name'][:30]}) 종가 ${x['close']} · 거래량 {x['vol_x']}배" + (f" · {x['star_ago']}봉 전 돌파{x['star_kind']}" if k == 'ret' else '')
                             + f" · ATR {x['atr_pct']}% · 6개월 {x['r126']:+}%" + f" | 손절 ${x['stop']} ({x['stop_pct']}%) · 반익 ${x['half']} (+{x['half_pct']}%){' → 남은 손절 본전' if k == 'ret' else ''} · 완익 = 종가가 구름 아래 (지금 ${x['cloud']}, {x['cloud_pct']}%)")
                L.append('')
            L.append('규칙: 손절 = 종가로 깨면 전부 / 반익 = 장중 닿으면 절반 / 완익 = 종가가 구름 아래면 나머지. 시험 성적(이 거름망 적용): 돌파★+절반 승률 약 50%·평균 +2~5%·20일 안 최고 +13~17%, 되돌림 승률 약 55%·평균 +1~3% (과거 결과, 권유 아님)')
            open(os.path.join(ODIR, 'scan_latest.txt'), 'w', encoding='utf-8').write('\n'.join(L))
            if os.environ.get('SCAN_OUT_DIR'): open(os.path.join(ODIR, f'scan_{day}.txt'), 'w', encoding='utf-8').write('\n'.join(L))
        except Exception as e: print('여의도병하 저장 실패', e)
    for title, L in (('돌파', brk), ('되돌림', ret)):
        print(f'\n== {title} 상위 10')
        for k, x in enumerate(L[:10], 1):
            print(f"{k:2}. {x['kind']:5} {x['t']:6} {x['name'][:34]:34} {x['etf']:3} 종가 {x['close']:>9} 거래량 {x['vol_x']}배 돌파선 {x['line']} 손절 {x['stop']} ({x['stop_pct']}%) 반익 {x['half']} (+{x['half_pct']}%) 완익=구름아래 종가 {x['cloud']} ({x['cloud_pct']}%) ATR {x['atr_pct']}% 거래대금 ${x['dvol_m']}M" + (f" · {x['star_ago']}봉 전 {x['star_kind']}" if title == '되돌림' else ''))
