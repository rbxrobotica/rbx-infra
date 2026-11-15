# Preservation kubectl build

This is an RBX build of the upstream Kubernetes v1.36.6 client, reported as
`v1.36.6-rbx.1`. It keeps the client on minor 1.36 for the observed 1.36 cluster.
No deployed workload digest changes merely by building this artifact.

`main.go` is copied without modification from
<https://raw.githubusercontent.com/kubernetes/kubernetes/v1.36.6/cmd/kubectl/kubectl.go>.
Its SHA-256 is
`b9660b8cacc734cf667f56fd55ae622e8f829536ac18aadf5fb1db0f8eb7edd5`.
The accompanying upstream Apache 2.0 license is included in the runtime image.

The November image scan rejected the old official v1.36.5 binary's Go standard
library and `golang.org/x/net`/`golang.org/x/text` dependencies. Official v1.36.6
still declares the affected net/text versions, so changing the download URL alone
does not resolve that scan. This build uses the pinned Go 1.27.4 image, upstream
Kubernetes modules v0.36.6 and the security updates locked in `go.mod`/`go.sum`.
The complete assembled image must pass the unchanged HIGH/CRITICAL scan.

For a future update, verify upstream source provenance, review the module/lock
diff, build with `-mod=readonly`, check the reported client version and run the
existing preservation fixture and full image scan. It remains a custom build;
do not label it as the upstream release binary or broaden its runtime RBAC.

Voice: R4 with R6 overlay, RBX Voice System v0.1. Authored with Codex.
