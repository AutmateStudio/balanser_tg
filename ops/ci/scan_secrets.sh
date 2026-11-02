#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'Скрипт CI рассчитан на Linux x64; локально используйте официальный Gitleaks 8.30.1.'
  exit 2
fi
tool_dir="${RUNNER_TEMP:-/tmp}/lidogen-gitleaks"
mkdir -p "$tool_dir"
curl --fail --location --silent --show-error --retry 3 \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
  --output "$tool_dir/gitleaks.tar.gz"
printf '%s  %s\n' '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb' "$tool_dir/gitleaks.tar.gz" | sha256sum --check --status
tar -xzf "$tool_dir/gitleaks.tar.gz" -C "$tool_dir" gitleaks
"$tool_dir/gitleaks" dir . --redact=100 --no-banner
