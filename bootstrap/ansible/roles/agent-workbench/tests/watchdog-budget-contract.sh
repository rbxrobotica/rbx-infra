#!/usr/bin/env bash
set -euo pipefail

role_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner="${role_dir}/files/rbx-agent-runner.sh"
watchdog="${role_dir}/files/rbx-agent-watchdog.sh"

bash -n "${runner}"
bash -n "${watchdog}"

auth_re="$(sed -n "s/^AUTH_RE='\(.*\)'$/\1/p" "${watchdog}")"
[[ -n "${auth_re}" ]]

# Regression from mission-2026-00030: an ordinary streamed usage number must
# never be interpreted as an HTTP authentication failure.
if printf '%s\n' '{"type":"system","estimated_tokens":401}' | grep -Eiq "${auth_re}"; then
  echo "auth regex matched a bare estimated_tokens value" >&2
  exit 1
fi

for signature in \
  'HTTP 401 Unauthorized' \
  'status_code: 403' \
  'Not logged in' \
  'OAuth session expired' \
  'invalid api key'
do
  if ! printf '%s\n' "${signature}" | grep -Eiq "${auth_re}"; then
    echo "auth regex missed expected signature: ${signature}" >&2
    exit 1
  fi
done

# Budget contract: provider result usage is checked against token max_cost;
# a global breach creates a shared marker, defers stop while busy, and blocks
# all subsequent claims until explicit operator review.
grep -q 'max_cost=.*\.max_cost' "${runner}"
grep -q 'mission_tokens > mission_token_cap' "${runner}"
grep -q 'stop_reason="cost_limit_reached"' "${runner}"
grep -q 'BUDGET PAUSE marker present; refusing new claims' "${runner}"
grep -q 'ACTIVE_MISSION_FILE' "${runner}"
grep -q 'touch "${BUDGET_STOP_FILE}"' "${watchdog}"
grep -q 'budget stop deferred: active mission' "${watchdog}"
grep -q 'if \[\[ "${runner_busy}" == "true" \]\]' "${watchdog}"

# Execute the watchdog against a hermetic HOME with mocked systemd/journal.
# The first pass observes an active mission and must defer stop; the second
# observes an idle runner and must issue the stop. The fixture also contains
# estimated_tokens=401 to exercise the auth false-positive regression end to
# end, not only against the extracted regex.
fixture="$(mktemp -d)"
trap 'rm -rf "${fixture}"' EXIT
mkdir -p \
  "${fixture}/home/rbx/runner" \
  "${fixture}/home/rbx/logs" \
  "${fixture}/home/.rbx/watchdog" \
  "${fixture}/bin"
cp "${watchdog}" "${fixture}/home/rbx/runner/rbx-agent-watchdog.sh"
printf '%s\n' \
  'WATCHDOG_STALL_MIN=20' \
  'WATCHDOG_VERIFY_STREAK=3' \
  'WATCHDOG_MONITORING_REPO=' \
  'WATCHDOG_24H_TOKEN_CAP=100' \
  > "${fixture}/home/rbx/runner/.env"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if [[ "$*" == "--user is-active rbx-agent-runner" ]]; then exit 0; fi' \
  'if [[ "$*" == "--user stop rbx-agent-runner" ]]; then echo stop >> "${SYSTEMCTL_LOG}"; exit 0; fi' \
  'exit 0' \
  > "${fixture}/bin/systemctl"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "${fixture}/bin/journalctl"
chmod +x "${fixture}/bin/systemctl" "${fixture}/bin/journalctl"
printf '%s\n' \
  '{"type":"result","usage":{"input_tokens":80,"cache_creation_input_tokens":20,"cache_read_input_tokens":30,"output_tokens":10},"estimated_tokens":401}' \
  > "${fixture}/home/rbx/logs/mission-2026-90001.log"
printf '%s|%s\n' "$$" 'mission-2026-90001' \
  > "${fixture}/home/.rbx/watchdog/active-mission"

export SYSTEMCTL_LOG="${fixture}/systemctl.log"
first_output="$(HOME="${fixture}/home" PATH="${fixture}/bin:/usr/bin:/bin" \
  bash "${fixture}/home/rbx/runner/rbx-agent-watchdog.sh" 2>&1)"
[[ -f "${fixture}/home/.rbx/watchdog/budget-stop" ]]
[[ ! -f "${SYSTEMCTL_LOG}" ]]
grep -q 'budget stop deferred: active mission mission-2026-90001' <<< "${first_output}"
if grep -Eq 'ALERT .*auth-fail|suppressed .*auth-fail' <<< "${first_output}"; then
  echo "watchdog emitted auth-fail for estimated_tokens=401 fixture" >&2
  exit 1
fi

rm -f "${fixture}/home/.rbx/watchdog/active-mission"
HOME="${fixture}/home" PATH="${fixture}/bin:/usr/bin:/bin" \
  bash "${fixture}/home/rbx/runner/rbx-agent-watchdog.sh" >/dev/null 2>&1
grep -q '^stop$' "${SYSTEMCTL_LOG}"

echo "watchdog budget contract: ok"
