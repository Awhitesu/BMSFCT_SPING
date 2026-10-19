"""Extract exception/caller evidence; stack candidates are not a full unwind."""
import argparse
import io
import json
import logging
from pathlib import Path
import struct
import sys


def analyze(dump_path, exception_path):
    sys.path.insert(0,str(Path(__file__).parent/'native_debug/python'))
    import pefile
    from minidump.minidumpfile import MinidumpFile
    from minidump.streams.ContextStream import CONTEXT
    # Partial minidumps need not contain PEB/environment memory.
    logging.disable(logging.CRITICAL)
    dump=MinidumpFile.parse(str(dump_path))
    exception=json.loads(Path(exception_path).read_text(encoding='ascii'))
    context=CONTEXT.parse(io.BytesIO(bytes.fromhex(exception['context'])))
    reader=dump.get_reader().get_buffered_reader()
    reader.move(context.Rsp)
    stack=reader.read(768)
    cache={}
    def symbol(address):
        for module in dump.modules.modules:
            if module.baseaddress<=address<module.baseaddress+module.size:
                if module.name not in cache:
                    try:
                        image=pefile.PE(module.name)
                        cache[module.name]=sorted((entry.address,entry.name.decode(errors='replace'))
                            for entry in image.DIRECTORY_ENTRY_EXPORT.symbols if entry.name)
                    except Exception:cache[module.name]=[]
                offset=address-module.baseaddress
                before=[entry for entry in cache[module.name] if entry[0]<=offset]
                return dict(module=Path(module.name).name,offset=hex(offset),
                    export=before[-1][1] if before else '',
                    export_offset=hex(offset-before[-1][0]) if before else '')
        return None
    python=next(module for module in dump.modules.modules if module.name.lower().endswith('python311.dll'))
    image=pefile.PE(python.name)
    none=next(python.baseaddress+entry.address for entry in image.DIRECTORY_ENTRY_EXPORT.symbols
              if entry.name==b'_Py_NoneStruct')
    candidates=[]
    for offset in range(0,len(stack),8):
        address=struct.unpack_from('<Q',stack,offset)[0]
        name=symbol(address)
        if name:candidates.append(dict(stack_offset=hex(offset),address=hex(address),**name))
    method=None
    for module in dump.modules.modules:
        if module.baseaddress<=context.Rbp<module.baseaddress+module.size:
            try:
                method=pefile.PE(module.name).get_data(context.Rbp-module.baseaddress,80).split(b'\0')[0].decode('ascii')
            except Exception:pass
            break
    return dict(pid=exception['pid'],thread=exception['thread'],
        exception_parameters=exception['parameters'],rip=hex(context.Rip),rax=hex(context.Rax),
        rcx=hex(context.Rcx),py_none=hex(none),rcx_is_py_none=context.Rcx==none,
        immediate_caller=symbol(struct.unpack_from('<Q',stack,0)[0]),
        virtual_method=method,stack_candidates=candidates)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dump',required=True)
    parser.add_argument('--exception',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();evidence=analyze(args.dump,args.exception)
    Path(args.output).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in evidence.items() if key!='stack_candidates'},indent=2))
