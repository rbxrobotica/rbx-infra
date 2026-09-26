# Prepare the Satwake consumption credential source

The Briefing BTC product and Commerce need one dedicated key for
`edition.viewed`. The source Secret is
`rbx-ia-br/rbx-commerce-consumption-key`, containing only
`COMMERCE_CONSUMPTION_SERVICE_KEY`. The [creation script](../../scripts/create-commerce-consumption-key-source.sh)
reads an existing `pass` entry and creates that Secret. It does not deploy a
workload or enable the endpoint. Merging this procedure does not run it.

This is a production credential change and requires specific authorization.
Use an operator workstation with the reviewed production kubeconfig and
encrypted `pass` store. First check that the target Secret is absent and that
the `pass` entry is absent. After approval, generate a fresh 64-character
credential in `pass` without printing it:

```sh
pass generate -n rbx/commerce/consumption-service-key 64 >/dev/null
scripts/create-commerce-consumption-key-source.sh ~/.kube/config-rbx
```

The script refuses an existing Secret, a missing/multiline/short `pass` entry,
or a failed Kubernetes read. It uses `kubectl create`, so a concurrent Secret
creation fails without replacing data. It never puts the key in a command
argument or repository file. Do not use the broad `k8s-secrets` role for this
credential; its current contact task omits active Meta keys.

Verify the source Secret's **name and key names only**, without printing or
decoding its data. Then, under a separately approved GitOps change, stage the
two narrow ExternalSecret mirrors in Infra #283 and require both to report
`Ready=True`. Only after that may Infra #284 add mandatory environment
references to Commerce and Briefing BTC. Both applications read the key at
startup, so rotation needs coordinated rollout. A genuine paid edition read
through the product is still needed to prove consumption.

If Secret creation fails after `pass generate`, preserve the encrypted entry
and investigate; rerunning the creation script is safe while the source
Secret remains absent. Do not generate a second key or overwrite an existing
Secret without a separate rotation plan.
