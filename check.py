import sys, glob
sys.path.insert(0, '.')
from parser import EggdropParser

p = EggdropParser()

all_files = sorted(glob.glob('/home/ubuntu/eggdrop/logs/lrh.log.*'))
feb_files = [f for f in all_files if any(
    d in f for d in ['02032026','02042026','02052026','02062026',
                     '02072026','02082026','02092026','02102026',
                     '02112026','02122026']
)]

print(f"Files found for Feb 3-12: {len(feb_files)}")
for f in feb_files:
    fname = f.split('/')[-1]
    fname_date = p._date_from_filename(f)
    print(f"\n--- {fname}  (filename->date: {fname_date}) ---")
    try:
        with open(f, 'r', encoding='utf-8', errors='replace') as fh:
            for i, line in enumerate(fh):
                if i >= 5: break
                print(f"  {repr(line.rstrip())}")
    except Exception as e:
        print(f"  ERROR reading file: {e}")
    evs = p.parse_file(f)
    msg_evs = [e for e in evs if e.type == 'message']
    dates_seen = sorted(set(e.timestamp.strftime('%Y-%m-%d') for e in evs))
    print(f"  Total events: {len(evs)}, messages: {len(msg_evs)}, dates: {dates_seen}")
