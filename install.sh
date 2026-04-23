#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INSTALL_HOME="${OPENCHRONICLE_INSTALL_HOME:-$HOME/.openchronicle}"
VENV_DIR="${INSTALL_HOME}/venv"
PYTHON_SPEC="${OPENCHRONICLE_PYTHON:-3.12}"
BIN_DIR_OVERRIDE=""
INJECT_MODE="prompt"  # prompt | all | none

UV_BIN=""
OPENCHRONICLE_BIN=""
INSTALL_BIN_DIR=""
PYTHON_TARGET=""

log() {
  printf '[openchronicle-install] %s\n' "$*"
}

warn() {
  printf '[openchronicle-install] Warning: %s\n' "$*" >&2
}

die() {
  printf '[openchronicle-install] Error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: bash install.sh [options]

Installs OpenChronicle into a dedicated virtualenv, compiles the macOS AX
helpers, creates an `openchronicle` shim, and optionally injects MCP config
into detected clients.

Options:
  --python <version>       Python version to target when a managed runtime is needed
                           (default: 3.12)
  --bin-dir <path>         Directory to place the `openchronicle` shim in
  --yes                    Auto-inject all detected MCP client configs
  --no-client-config       Skip MCP client config prompts entirely
  -h, --help               Show this help
EOF
}

version_ge() {
  local lhs="$1"
  local rhs="$2"
  local lhs_major=0 lhs_minor=0 lhs_patch=0
  local rhs_major=0 rhs_minor=0 rhs_patch=0
  local IFS=.

  read -r lhs_major lhs_minor lhs_patch <<< "${lhs}"
  read -r rhs_major rhs_minor rhs_patch <<< "${rhs}"

  lhs_minor="${lhs_minor:-0}"
  lhs_patch="${lhs_patch:-0}"
  rhs_minor="${rhs_minor:-0}"
  rhs_patch="${rhs_patch:-0}"

  if (( lhs_major != rhs_major )); then
    (( lhs_major > rhs_major ))
    return
  fi
  if (( lhs_minor != rhs_minor )); then
    (( lhs_minor > rhs_minor ))
    return
  fi
  (( lhs_patch >= rhs_patch ))
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --python)
        [[ $# -ge 2 ]] || die "--python requires a value"
        PYTHON_SPEC="$2"
        shift 2
        ;;
      --bin-dir)
        [[ $# -ge 2 ]] || die "--bin-dir requires a value"
        BIN_DIR_OVERRIDE="$2"
        shift 2
        ;;
      --yes)
        INJECT_MODE="all"
        shift
        ;;
      --no-client-config)
        INJECT_MODE="none"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done
}

require_repo_root() {
  [[ -f "${ROOT_DIR}/pyproject.toml" ]] || die "run this script from the repository root"
  [[ -d "${ROOT_DIR}/src/openchronicle" ]] || die "repository layout looks incomplete"
}

check_platform() {
  [[ "$(uname -s)" == "Darwin" ]] || die "OpenChronicle currently supports macOS only"
  local product_version
  product_version="$(sw_vers -productVersion 2>/dev/null || true)"
  [[ -n "${product_version}" ]] || die "could not determine macOS version via sw_vers"
  local major
  major="${product_version%%.*}"
  version_ge "${major}" "13" || die "macOS 13+ required (found ${product_version})"
}

ensure_xcode_clt() {
  if xcode-select -p >/dev/null 2>&1 && command -v swiftc >/dev/null 2>&1; then
    return
  fi

  warn "Xcode Command Line Tools are required to compile the AX binaries."
  if command -v xcode-select >/dev/null 2>&1; then
    xcode-select --install >/dev/null 2>&1 || true
  fi
  die "install Xcode Command Line Tools (xcode-select --install), then rerun this script"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    return
  fi

  log "uv not found; installing it"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "failed to install uv via curl"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh || die "failed to install uv via wget"
  else
    die "uv not found and neither curl nor wget is available to install it"
  fi

  local candidate
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [[ -x "${candidate}" ]]; then
      UV_BIN="${candidate}"
      export PATH="$(dirname "${candidate}"):${PATH}"
      return
    fi
  done

  die "uv installation finished but the binary was not found in a standard user bin directory"
}

find_compatible_system_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    return 1
  fi

  local version
  version="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || true)"
  [[ -n "${version}" ]] || return 1
  if version_ge "${version}" "3.11"; then
    command -v python3
    return 0
  fi
  return 1
}

prepare_python_target() {
  local system_python=""
  if system_python="$(find_compatible_system_python)"; then
    log "using system Python at ${system_python}"
    PYTHON_TARGET="${system_python}"
    return 0
  fi

  log "system Python < 3.11; installing managed Python ${PYTHON_SPEC} via uv"
  "${UV_BIN}" python install "${PYTHON_SPEC}" || die "failed to install Python ${PYTHON_SPEC} via uv"
  PYTHON_TARGET="${PYTHON_SPEC}"
}

install_package() {
  local python_target="$1"
  rm -rf "${VENV_DIR}"
  mkdir -p "${INSTALL_HOME}"

  log "creating virtualenv at ${VENV_DIR}"
  "${UV_BIN}" venv "${VENV_DIR}" --python "${python_target}" || die "failed to create virtualenv"

  log "installing OpenChronicle into the virtualenv"
  "${UV_BIN}" pip install --python "${VENV_DIR}/bin/python" "${ROOT_DIR}" \
    || die "failed to install OpenChronicle into ${VENV_DIR}"

  OPENCHRONICLE_BIN="${VENV_DIR}/bin/openchronicle"
  [[ -x "${OPENCHRONICLE_BIN}" ]] || die "expected CLI not found at ${OPENCHRONICLE_BIN}"
}

compile_bundled_binaries() {
  log "compiling bundled AX helper binaries"
  "${VENV_DIR}/bin/python" - <<'PY' || die "failed to compile bundled AX binaries"
from openchronicle.capture.ax_capture import _resolve_helper_path
from openchronicle.capture.watcher import _resolve_watcher_path

helper = _resolve_helper_path()
watcher = _resolve_watcher_path()
if helper is None:
    raise SystemExit("mac-ax-helper not available after install")
if watcher is None:
    raise SystemExit("mac-ax-watcher not available after install")
print(f"helper={helper}")
print(f"watcher={watcher}")
PY
}

choose_install_bin_dir() {
  if [[ -n "${BIN_DIR_OVERRIDE}" ]]; then
    mkdir -p "${BIN_DIR_OVERRIDE}"
    [[ -w "${BIN_DIR_OVERRIDE}" ]] || die "--bin-dir is not writable: ${BIN_DIR_OVERRIDE}"
    printf '%s' "${BIN_DIR_OVERRIDE}"
    return 0
  fi

  local home_local="${HOME}/.local/bin"
  case ":${PATH}:" in
    *":${home_local}:"*)
      mkdir -p "${home_local}"
      if [[ -w "${home_local}" ]]; then
        printf '%s' "${home_local}"
        return 0
      fi
      ;;
  esac

  local path_dir
  local -a path_parts
  IFS=':' read -r -a path_parts <<< "${PATH}"
  for path_dir in "${path_parts[@]}"; do
    [[ -n "${path_dir}" ]] || continue
    [[ "${path_dir}" == "${home_local}" ]] && continue
    [[ -d "${path_dir}" && -w "${path_dir}" ]] || continue
    printf '%s' "${path_dir}"
    return 0
  done

  mkdir -p "${home_local}"
  [[ -w "${home_local}" ]] || die "could not find or create a writable bin directory"
  printf '%s' "${home_local}"
}

install_shim() {
  INSTALL_BIN_DIR="$(choose_install_bin_dir)"
  local shim_path="${INSTALL_BIN_DIR}/openchronicle"
  cat > "${shim_path}" <<EOF
#!/usr/bin/env bash
exec "${OPENCHRONICLE_BIN}" "\$@"
EOF
  chmod +x "${shim_path}"
  export PATH="${INSTALL_BIN_DIR}:${PATH}"
  log "installed openchronicle shim at ${shim_path}"
}

verify_install() {
  "${INSTALL_BIN_DIR}/openchronicle" status >/dev/null \
    || die "installation verification failed ('openchronicle status' did not succeed)"
}

prompt_yes_no() {
  local prompt="$1"
  local reply
  if [[ ! -t 0 ]]; then
    return 1
  fi
  read -r -p "${prompt} [Y/n] " reply
  if [[ -z "${reply}" ]]; then
    return 0
  fi
  [[ "${reply}" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]
}

maybe_inject_client() {
  local client="$1"
  local label="$2"

  case "${INJECT_MODE}" in
    none)
      return 0
      ;;
    all)
      log "injecting MCP config into ${label}"
      ;;
    prompt)
      if ! prompt_yes_no "Detected ${label}. Inject OpenChronicle MCP config now?"; then
        return 0
      fi
      ;;
    *)
      die "unexpected INJECT_MODE=${INJECT_MODE}"
      ;;
  esac

  if ! "${INSTALL_BIN_DIR}/openchronicle" install "${client}"; then
    warn "failed to inject MCP config for ${label}; you can retry later with 'openchronicle install ${client}'"
  fi
}

inject_detected_clients() {
  local codex_cfg="$HOME/.codex/config.toml"
  local claude_code_cfg="$HOME/.claude.json"
  local claude_desktop_cfg="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
  local opencode_cfg="$HOME/.config/opencode/opencode.json"
  local opencode_jsonc="$HOME/.config/opencode/opencode.jsonc"

  if [[ -f "${codex_cfg}" ]]; then
    if command -v codex >/dev/null 2>&1; then
      maybe_inject_client "codex" "Codex CLI"
    else
      warn "found ${codex_cfg}, but \`codex\` is not on PATH; skipping MCP injection"
    fi
  fi

  if [[ -f "${claude_code_cfg}" ]]; then
    if command -v claude >/dev/null 2>&1; then
      maybe_inject_client "claude-code" "Claude Code"
    else
      warn "found ${claude_code_cfg}, but \`claude\` is not on PATH; skipping MCP injection"
    fi
  fi

  if [[ -f "${claude_desktop_cfg}" ]]; then
    maybe_inject_client "claude-desktop" "Claude Desktop"
  fi

  if [[ -f "${opencode_cfg}" || -f "${opencode_jsonc}" ]]; then
    maybe_inject_client "opencode" "opencode"
  fi
}

print_summary() {
  cat <<EOF

OpenChronicle installed successfully.

Install root : ${INSTALL_HOME}
Virtualenv   : ${VENV_DIR}
CLI shim     : ${INSTALL_BIN_DIR}/openchronicle

Next steps:
  1. Grant Accessibility permission to your terminal:
     System Settings -> Privacy & Security -> Accessibility
  2. Start the daemon:
     openchronicle start
  3. Check status:
     openchronicle status
EOF

  case ":${PATH}:" in
    *":${INSTALL_BIN_DIR}:"*)
      ;;
    *)
      warn "${INSTALL_BIN_DIR} is not on your PATH in this shell. Add it before using 'openchronicle' from a new terminal."
      ;;
  esac
}

main() {
  parse_args "$@"
  require_repo_root
  check_platform
  ensure_xcode_clt
  ensure_uv

  prepare_python_target
  [[ -n "${PYTHON_TARGET}" ]] || die "failed to determine a Python target"
  install_package "${PYTHON_TARGET}"
  compile_bundled_binaries
  install_shim
  verify_install
  inject_detected_clients
  print_summary
}

main "$@"
