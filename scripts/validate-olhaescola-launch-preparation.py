#!/usr/bin/env python3
"""Validate that the proposed Olha Escola release remains inert until activation."""
from pathlib import Path
import re, subprocess, yaml
root=Path(__file__).resolve().parents[1]
render=subprocess.check_output(['kubectl','kustomize',str(root/'apps/prod/olhaescola')],text=True)
docs=list(yaml.safe_load_all(render))
by_name={(d['kind'],d['metadata']['name']):d for d in docs}
config=by_name[('ConfigMap','olhaescola-config')]['data']
for flag in ('LOCAL_PREVIEW','PUBLIC_SUBMISSIONS_ENABLED','MODERATION_ENABLED','ORPHAN_RECONCILIATION_ENABLED','LEGAL_REVIEW_APPROVED'):
    assert config[flag]=='false',f'{flag} não está fechado'
assert config['FACE_DETECTOR_BINARY']=='/app/face-detector'
assert config['FACE_DETECTOR_MODEL']=='/app/models/face_detection_yunet_2023mar.onnx'
assert not any(key in config for key in ('REKOGNITION_REGION','COMPREHEND_REGION','AWS_REGION'))
assert config['CONTROLLER_NAME']=='Leandro Damasio' and config['CONTACT_EMAIL']=='ceo@rbxsystems.ch'
for name in ('olhaescola-api','olhaescola-web'):
    d=by_name[('Deployment',name)]
    assert d['spec']['replicas']==0,f'{name} possui réplicas ativas'
    pod=d['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False
    assert pod['securityContext']['runAsNonRoot'] is True
    c=pod['containers'][0]
    assert re.fullmatch(r'ghcr\.io/rbxrobotica/'+name+r':sha-[a-f0-9]{40}',c['image']),c['image']
    assert c['securityContext']['readOnlyRootFilesystem'] is True
    assert c['securityContext']['allowPrivilegeEscalation'] is False
    if name=='olhaescola-web': assert all('secretRef' not in e for e in c['envFrom'])
postgres=by_name[('Service','olhaescola-postgres')]
assert not postgres['spec'].get('selector'), 'Kustomize injetou selector no banco externo'
assert ('Endpoints','olhaescola-postgres') in by_name
assert all(d['kind']!='StatefulSet' for d in docs)
for d in docs:
    assert d['kind'] not in ('Secret','ExternalSecret','RoleBinding','ClusterRoleBinding')
    if d['kind']=='Ingress':
        assert d['spec']['rules'][0]['http']['paths'][0]['backend']['service']['name']=='olhaescola-web'
assert not (root/'gitops/app-of-apps/olhaescola.yml').exists()
for path in (root/'gitops/app-of-apps').glob('*.y*ml'):
    for d in yaml.safe_load_all(path.read_text()):
        if isinstance(d,dict): assert d.get('metadata',{}).get('name')!='olhaescola'
print(f'OK: {len(docs)} recursos renderizados; zero réplicas; envios fechados; banco selectorless; nenhuma Application ativa, Secret ou permissão nova.')
