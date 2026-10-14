from concurrent.futures import ThreadPoolExecutor
import time


def run_simulator_batch(channels,action):
    """One serialized operation per physical simulator; independent devices run concurrently."""
    grouped={}
    for global_ch,sim,local_ch in channels:
        entry=grouped.setdefault(id(sim),(sim,[],[]))
        entry[1].append(local_ch);entry[2].append(global_ch)
    started=time.perf_counter()
    def execute(entry):
        sim,local,global_channels=entry
        try:
            ok=bool(action(sim,local))
            error='' if ok else getattr(sim,'last_error','command failed')
        except Exception as exc: ok=False;error=str(exc)
        return {'ok':ok,'channels':global_channels,'error':error}
    if not grouped: return {'ok':False,'results':[],'elapsed':0}
    with ThreadPoolExecutor(max_workers=len(grouped)) as pool:
        results=list(pool.map(execute,grouped.values()))
    return {'ok':all(r['ok'] for r in results),'results':results,'elapsed':time.perf_counter()-started}
