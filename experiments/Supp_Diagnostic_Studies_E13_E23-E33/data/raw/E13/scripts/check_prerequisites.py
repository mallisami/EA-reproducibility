#!/usr/bin/env python3
import json, shutil, subprocess, platform
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def cmd(a):
    p=subprocess.run(a,capture_output=True,text=True)
    return {"returncode":p.returncode,"stdout":p.stdout.strip(),"stderr":p.stderr.strip()}
rec={
 "platform":platform.platform(),
 "kubectl_path":shutil.which("kubectl"),
 "docker_path":shutil.which("docker"),
 "kind_path":shutil.which("kind"),
 "minikube_path":shutil.which("minikube"),
 "k3d_path":shutil.which("k3d"),
}
if rec["kubectl_path"]:
    rec["kubectl_client"]=cmd(["kubectl","version","--client"])
    rec["cluster_info"]=cmd(["kubectl","cluster-info"])
else:
    rec["kubectl_client"]={"returncode":127,"stderr":"kubectl not installed"}
    rec["cluster_info"]={"returncode":127,"stderr":"kubectl not installed"}
(ROOT/"results/e13_prerequisites.json").write_text(json.dumps(rec,indent=2),encoding="utf-8")
print(json.dumps(rec,indent=2))
