import json,time
from pathlib import Path
from test_34461a_voltage_hardware import Instrument,SETTINGS
folder=Path("logs/34461a_shorted_20261019");folder.mkdir(exist_ok=False)
d=Instrument("192.168.1.107",folder);baseline={};result={"condition":"User confirmed HI/LO shorted, no external supply","readings":[]}
try:
    assert d.connect(),d.last_error
    result["terminal"]=d.query("ROUT:TERM?")
    baseline["CONF"]=d.query("CONF?")
    for c in SETTINGS:baseline[c]=d.query(c+"?")
    label=next(k for k,v in d.FUNCTIONS.items() if v[0]=="VOLT:DC")
    for rng in (.1,1,10,.1):
        for i in range(10):
            start=time.perf_counter()
            try:value=d.measure(label,rng);error=""
            except ValueError as exc:value=None;error=str(exc)
            result["readings"].append(dict(range=rng,value=value,error=error,metadata=dict(d.last_measurement),ms=(time.perf_counter()-start)*1000))
        result.setdefault("configs",[]).append(dict(range=rng,conf=d.query("CONF?"),error=d.query("SYST:ERR?")))
        print("range",rng,"completed",flush=True)
except Exception as exc:result["error"]=str(exc)
finally:
    try:
        if baseline:
            if not d.is_connected:assert d.connect()
            d.command("CONF:"+baseline["CONF"].strip(chr(34)))
            for c in SETTINGS:d.command(c+" "+baseline[c])
            actual={c:d.query(c+"?") for c in SETTINGS};actual["CONF"]=d.query("CONF?")
            result["restored"]=actual==baseline;result["final_error"]=d.query("SYST:ERR?")
    except Exception as exc:result["cleanup_error"]=str(exc)
    d.disconnect();d.trace.close()
    (folder/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in result.items() if k!="readings"},indent=2))
