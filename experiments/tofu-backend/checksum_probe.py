#!/usr/bin/env python3
"""Small independent checksum/conditional probes in declared synthetic buckets."""
import argparse,base64,hashlib,json
from pathlib import Path
import core


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sdk-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.exists():raise ValueError('preserve_previous_receipt')
    b=core.load();runtime=core.client(b,'runtime',args.sdk_dir);archive=core.client(b,'archive-probe',args.sdk_dir)
    payload=b'owned-synthetic-checksum-canary';md5=base64.b64encode(hashlib.md5(payload,usedforsecurity=False).digest()).decode()
    report={'type':'synthetic checksum transport probe','run_id':b['run_id'],'at_utc':core.utc(),'observations':{}}
    for name,client,role,key,conditional in [
        ('conditional_with_md5',runtime,'state',b['prefix']+'active/probes/checksum-conditional',True),
        ('archive_with_md5',archive,'archive',b['prefix']+'snapshots/checksum-canary',False)]:
        kwargs={'Bucket':b['buckets'][role]['bucketName'],'Key':key,'Body':payload,'ServerSideEncryption':'AES256','ContentMD5':md5}
        if conditional:kwargs['IfNoneMatch']='*'
        result,_=core.safe_call(client.put_object,**kwargs);report['observations'][name]=result
        if conditional and result['ok']:
            again,_=core.safe_call(client.put_object,**kwargs);report['observations']['conditional_conflict_with_md5']=again
    with args.output.open('x') as stream:json.dump(report,stream,indent=2)
    print(json.dumps(report))


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'completed':False,'error_type':type(e).__name__}));raise SystemExit(1)
