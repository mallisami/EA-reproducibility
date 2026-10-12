#!/usr/bin/env python3
import json, platform, shutil, subprocess

def run(a):
    p = subprocess.run(a, text=True, capture_output=True)
    return {'cmd': a, 'returncode': p.returncode, 'stdout': p.stdout.strip(), 'stderr': p.stderr.strip()}

checks = {'platform': platform.platform(), 'kubectl_path': shutil.which('kubectl')}
if not checks['kubectl_path']:
    checks['status'] = 'INFRA_FAILURE'
    checks['reason'] = 'kubectl not installed'
else:
    checks['client'] = run(['kubectl', 'version', '--client', '-o', 'json'])
    checks['cluster'] = run(['kubectl', 'cluster-info'])
    checks['context'] = run(['kubectl', 'config', 'current-context'])
    required = [
        ('create', 'namespaces', None), ('delete', 'namespaces', None), ('get', 'nodes', None),
        ('create', 'pods', 'flashir-e13'), ('get', 'pods', 'flashir-e13'), ('list', 'pods', 'flashir-e13'),
        ('delete', 'pods', 'flashir-e13'), ('patch', 'pods', 'flashir-e13'),
        ('create', 'statefulsets.apps', 'flashir-e13'), ('get', 'statefulsets.apps', 'flashir-e13'),
        ('patch', 'statefulsets.apps', 'flashir-e13'), ('delete', 'statefulsets.apps', 'flashir-e13'),
        ('create', 'services', 'flashir-e13'), ('get', 'services', 'flashir-e13'), ('delete', 'services', 'flashir-e13'),
        ('create', 'endpointslices.discovery.k8s.io', 'flashir-e13'), ('get', 'endpointslices.discovery.k8s.io', 'flashir-e13'),
        ('patch', 'endpointslices.discovery.k8s.io', 'flashir-e13'), ('delete', 'endpointslices.discovery.k8s.io', 'flashir-e13')
    ]
    perms = []
    for verb, resource, namespace in required:
        cmd = ['kubectl', 'auth', 'can-i', verb, resource]
        if namespace:
            cmd += ['-n', namespace]
        rec = run(cmd)
        rec.update({'verb': verb, 'resource': resource, 'namespace': namespace})
        perms.append(rec)
    checks['permissions'] = perms
    permission_ok = all(x['returncode'] == 0 and x['stdout'].lower().startswith('yes') for x in perms)
    checks['status'] = 'PASS' if checks['cluster']['returncode'] == 0 and permission_ok else 'INFRA_FAILURE'
    if not permission_ok:
        checks['reason'] = 'one or more required Kubernetes permissions are unavailable'
print(json.dumps(checks, indent=2))
