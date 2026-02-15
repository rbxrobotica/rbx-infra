# Makefile for rbx-infra
# Common operations for infrastructure management

.PHONY: help bootstrap validate lint k9s argocd-status

# Default target
help:
	@echo "RBX Infrastructure - Available Commands:"
	@echo ""
	@echo "  bootstrap    - Initial cluster setup (install ArgoCD + root Application)"
	@echo "  validate     - Validate all Kubernetes manifests"
	@echo "  lint         - Lint YAML files"
	@echo "  k9s          - Open K9s terminal UI"
	@echo "  argocd-status - Show ArgoCD application status"
	@echo "  diff         - Show pending changes (argocd diff)"
	@echo ""

# Bootstrap a new cluster
bootstrap:
	@echo "Installing ArgoCD..."
	kubectl apply -k platform/argocd
	@echo ""
	@echo "Waiting for ArgoCD to be ready..."
	kubectl wait --for=condition=available --timeout=300s deployment/argocd-server -n argocd
	@echo ""
	@echo "Applying root Application..."
	kubectl apply -f gitops/app-of-apps/root.yml
	@echo ""
	@echo "Bootstrap complete! Access ArgoCD at: https://argocd.rbx.ia.br"
	@echo "Initial password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"

# Validate all manifests
validate:
	@echo "Validating Kubernetes manifests..."
	@find . -name "*.yml" -o -name "*.yaml" | grep -v ".github" | xargs kubeconform -summary

# Lint YAML files
lint:
	@echo "Linting YAML files..."
	yamllint -d '{extends: default, rules: {line-length: {max: 150}}}' .

# Open K9s
k9s:
	k9s

# ArgoCD status
argocd-status:
	@echo "ArgoCD Application Status:"
	@argocd app list 2>/dev/null || echo "argocd CLI not configured. Run: argocd login"

# Show pending changes
diff:
	argocd app diff root

# Create a new application
new-app:
	@read -p "Application name: " name; \
	mkdir -p apps/prod/$$name; \
	mkdir -p core/namespaces; \
	echo "Created apps/prod/$$name/ - add your manifests there"

# Sync all applications
sync:
	argocd app sync root

# Generate documentation
docs:
	@echo "Generating documentation..."
	@tree -L 3 -I '.git' . > docs/structure.txt
