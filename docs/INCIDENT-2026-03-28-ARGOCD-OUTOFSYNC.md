# Incident Report: ArgoCD OutOfSync Issues

**Date**: 2026-03-28 / 2026-03-29
**Severity**: P2 (High)
**Status**: Resolved
**Duration**: ~2 hours (ArgoCD) + follow-up session (robson-prod DB connectivity)

## Summary

Multiple ArgoCD Applications were stuck in OutOfSync status despite no actual differences between Git and cluster state. The root cause was the use of `ServerSideApply=true` causing field manager conflicts.

## Impact

**Affected Applications**:
- argocd (core platform)
- cert-manager (TLS certificate management)
- robson-prod (production trading bot)
- All other applications (potential future issues)

**User Impact**:
- No service outages (all applications remained Healthy)
- GitOps automation partially broken (manual intervention required)
- Reduced confidence in ArgoCD sync status

## Timeline (UTC-3)

- **00:00** - User reports ArgoCD showing OutOfSync for multiple apps
- **00:15** - Investigation started, identified OutOfSync resources
- **00:30** - Root cause identified: ServerSideApply field manager conflicts
- **00:45** - Added `ignoreDifferences` for webhook caBundle fields
- **01:00** - Removed ServerSideApply from cert-manager → Synced ✅
- **01:15** - Removed ServerSideApply from robson-prod → Synced ✅
- **01:30** - Removed ServerSideApply from all remaining applications
- **02:00** - Documentation created, incident resolved

## Root Cause

### Technical Details

ArgoCD's `ServerSideApply=true` option uses Kubernetes Server-Side Apply (SSA) for resource management. When combined with Helm or other resource managers, SSA creates field manager conflicts:

1. **Helm** creates/updates resource → becomes field manager for certain fields
2. **ArgoCD with SSA** tries to manage same resource → creates competing field manager
3. **Kubernetes API** sees two managers for same fields → reports differences
4. **ArgoCD** sees differences → marks as OutOfSync
5. **Cycle repeats** even after successful sync operations

### Affected Resource Types

Most problematic with:
- `apps/v1 Deployment` - spec.selector field
- `apps/v1 StatefulSet` - spec.selector field
- `batch/v1 CronJob` - spec.jobTemplate fields
- `admissionregistration.k8s.io` webhooks - caBundle field

## Resolution

### Immediate Fix

1. Removed `ServerSideApply=true` from all ArgoCD Applications
2. Added `RespectIgnoreDifferences=true` where needed
3. Configured `ignoreDifferences` for controller-managed fields

### Files Modified

```
platform/argocd/application.yml
platform/cert-manager/application.yml
gitops/app-of-apps/robson-prod.yml
gitops/app-of-apps/argos-radar.yml
gitops/app-of-apps/thalamus.yml
gitops/app-of-apps/truthmetal.yml
gitops/app-of-apps/lda-prod.yml
gitops/app-of-apps/rbx-ia-br.yml
gitops/app-of-apps/rbxsystems-ch.yml
gitops/app-of-apps/platform.yml
```

### Commits

- `dcb5d24` - Add ignoreDifferences for argocd and cert-manager
- `7b02430` - Remove ServerSideApply from cert-manager
- `52018f5` - Remove ServerSideApply from robson-prod
- `<current>` - Remove ServerSideApply from all applications

## Prevention

### Policy Changes

1. **Ban ServerSideApply** in all ArgoCD Applications (documented in ARGOCD-BEST-PRACTICES.md)
2. **Use ignoreDifferences** for controller-managed fields only
3. **Always include RespectIgnoreDifferences** when using ignoreDifferences

### Process Improvements

1. Created `docs/ARGOCD-BEST-PRACTICES.md` with guidelines
2. Updated `CLAUDE.md` with reference to best practices (if needed)
3. All future Applications must follow the documented patterns

### Monitoring

- Set up alerts for applications stuck OutOfSync > 5 minutes
- Regular review of Application sync status
- Automated checks in CI for ServerSideApply usage

## Lessons Learned

### What Went Well

- Quick identification of root cause through field manager inspection
- Systematic approach to fixing all affected applications
- No service disruptions during incident

### What Could Be Improved

- Earlier detection (should have noticed pattern across multiple apps)
- Better understanding of ServerSideApply implications before use
- Automated validation of ArgoCD Application manifests

### Action Items

- [ ] Set up automated scanning for `ServerSideApply=true` in CI
- [ ] Add pre-commit hook to warn about ServerSideApply usage
- [ ] Review all Application templates for compliance
- [ ] Share best practices with team

## References

- [ArgoCD Best Practices](./ARGOCD-BEST-PRACTICES.md)
- [Kubernetes Server-Side Apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)
- [ArgoCD Sync Options](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/)

## Related Issues

- CNI outage (2026-03-23) - Different root cause, documented in project_incident_2026_03.md
