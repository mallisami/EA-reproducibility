#!/usr/bin/env python3
"""Read-only Azure/kind E38 preflight. No cluster mutations and no installation."""
import argparse,json,platform,shutil,subprocess,sys,datetime,os
from pathlib import Path
from e38_model import frozen_planner_certificate

def command(args,timeout=15):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
        return {'exit':p.returncode,'stdout':p.stdout.strip()[:1500],'stderr':p.stderr.strip()[:1000]}
    except Exception as exc:return {'exit':None,'error':repr(exc)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--context',default='kind-e38');p.add_argument('--output',type=Path);args=p.parse_args()
    # Check the filesystem actually containing durable trial output, not only HOME.
    trial_root=Path(__file__).resolve().parent.parent/'results'
    trial_root.mkdir(parents=True,exist_ok=True)
    disk=shutil.disk_usage(str(trial_root))
    home_disk=shutil.disk_usage(str(Path.home()))
    memory_available_bytes=None
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                memory_available_bytes=int(line.split()[1])*1024;break
    except (OSError,ValueError,IndexError):pass
    report={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'hostname':platform.node(),'platform':platform.platform(),
       'cpu_count':os.cpu_count(),'trial_output_disk_free_gb':round(disk.free/1e9,3),
       'home_disk_free_gb':round(home_disk.free/1e9,3),
       'memory_available_gb':round(memory_available_bytes/1e9,3) if memory_available_bytes is not None else None,
       'checks':{},'read_only':True,'live_experiment_trials':0,
       'planner_certificate':list(frozen_planner_certificate()['observations'])}
    for exe,cmd in {'docker':['docker','info','--format','{{.ServerVersion}}'],
        'kind':['kind','version'],'kubectl':['kubectl','version','--client'],
        'python3':['python3','--version']}.items():
        report['checks'][exe]=command(cmd) if shutil.which(exe) else {'exit':None,'error':'executable missing'}
    if shutil.which('kubectl'):
        report['checks']['current_context']=command(['kubectl','config','current-context'])
        report['checks']['nodes']=command(['kubectl','--context',args.context,'get','nodes','--no-headers'])
        report['checks']['permission_create_namespaces']=command(['kubectl','--context',args.context,'auth','can-i','create','namespaces'])
        report['checks']['permission_create_configmaps']=command(['kubectl','--context',args.context,'auth','can-i','create','configmaps','--all-namespaces'])
        report['checks']['permission_patch_configmaps']=command(['kubectl','--context',args.context,'auth','can-i','patch','configmaps','--all-namespaces'])
        report['checks']['permission_create_pods']=command(['kubectl','--context',args.context,'auth','can-i','create','pods','--all-namespaces'])
        report['checks']['permission_delete_pods']=command(['kubectl','--context',args.context,'auth','can-i','delete','pods','--all-namespaces'])
        report['checks']['permission_ssar']=command(['kubectl','--context',args.context,'auth','can-i','create','selfsubjectaccessreviews.authorization.k8s.io'])
    if shutil.which('kind'):
        report['checks']['kind_clusters']=command(['kind','get','clusters'])
    ctx=report['checks'].get('current_context',{}).get('stdout')
    report['safe_context_verified']=ctx==args.context and args.context.startswith('kind-e38')
    report['baseline_tools_available']=all(report['checks'][x].get('exit')==0 for x in ('docker','kind','kubectl','python3'))
    nodes=report['checks'].get('nodes',{})
    report['ready_nodes']=sum(len(line.split())>=2 and line.split()[1]=='Ready'
             for line in nodes.get('stdout','').splitlines()) if nodes.get('exit')==0 else 0
    clusters=report['checks'].get('kind_clusters',{})
    report['dedicated_kind_cluster_present']=(clusters.get('exit')==0 and
        'e38' in clusters.get('stdout','').splitlines())
    required=('permission_create_namespaces','permission_create_configmaps',
              'permission_patch_configmaps','permission_create_pods','permission_delete_pods',
              'permission_ssar')
    report['permissions_verified']=all(report['checks'].get(k,{}).get('exit')==0 and
                report['checks'][k].get('stdout','').lower()=='yes' for k in required)
    report['disk_headroom_8gb']=disk.free>=8_000_000_000
    report['memory_headroom_4gb']=(memory_available_bytes is not None and memory_available_bytes>=4_000_000_000)
    report['ready_for_smoke_software_only']=(report['safe_context_verified'] and
        report['baseline_tools_available'] and report['ready_nodes']>=1 and
        report['dedicated_kind_cluster_present'] and report['permissions_verified'] and
        report['disk_headroom_8gb'] and report['memory_headroom_4gb'])
    out=json.dumps(report,indent=2)+'\n'
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(out)
    print(out)
    return 0 if report['ready_for_smoke_software_only'] else 2
if __name__=='__main__':sys.exit(main())
