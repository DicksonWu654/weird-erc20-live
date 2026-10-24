"""Poll the C27 handoff every ten minutes, within C26's five-hour window."""
import datetime
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[2]
PROGRESS=ROOT/'data/tier2/v2/c26_progress.json'


def main():
    while True:
        d=json.loads(PROGRESS.read_text())
        now=datetime.datetime.now(datetime.UTC)
        deadline=datetime.datetime.fromisoformat(d['blind300_deadline'].replace('Z','+00:00'))
        latest=datetime.datetime.fromisoformat(d['polls'][-1]['at'].replace('Z','+00:00'))
        wake=min(deadline,latest+datetime.timedelta(seconds=d['poll_interval_seconds']))
        delay=(wake-now).total_seconds()
        if delay>0:time.sleep(delay)
        now=datetime.datetime.now(datetime.UTC)
        exists=(ROOT/'data/verify/blind300/summary.csv').is_file()
        poll=dict(at=now.isoformat(),summary_exists=exists)
        d['polls'].append(poll)
        d['blind_watch_status']='available' if exists else 'deadline_existing_blind_sets' if now>=deadline else 'waiting'
        PROGRESS.write_text(json.dumps(d,indent=2)+'\n')
        print(json.dumps(poll),flush=True)
        if exists or now>=deadline:return


if __name__=='__main__':main()
