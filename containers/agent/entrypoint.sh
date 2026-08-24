#!/bin/bash
set -e

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="/home/${AGENT_USER}"

# --- Tailscale ---
if [ -n "$TAILSCALE_AUTHKEY" ]; then
    echo "Starting Tailscale daemon..."
    # Use userspace networking if /dev/net/tun is not available (e.g. RunPod containers)
    TS_ARGS="--state=/var/lib/tailscale/tailscaled.state"
    if [ ! -c /dev/net/tun ]; then
        echo "No /dev/net/tun, using Tailscale userspace networking..."
        TS_ARGS="$TS_ARGS --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055"
    fi
    tailscaled $TS_ARGS &
    sleep 2
    if tailscale up --authkey="$TAILSCALE_AUTHKEY" --hostname="${HOSTNAME:-reasonsforge}" --ssh 2>/dev/null; then
        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
        echo "Tailscale IP: $TAILSCALE_IP"
    else
        echo "WARNING: Tailscale failed to start, continuing without it"
    fi
fi

# --- SSH authorized keys ---
if [ -n "$SSH_AUTHORIZED_KEY" ]; then
    mkdir -p "${AGENT_HOME}/.ssh"
    echo "$SSH_AUTHORIZED_KEY" > "${AGENT_HOME}/.ssh/authorized_keys"
    chmod 700 "${AGENT_HOME}/.ssh"
    chmod 600 "${AGENT_HOME}/.ssh/authorized_keys"
    chown -R "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/.ssh"
fi

# --- Auth environment ---
auth_env=""

if [ -n "$OLLAMA_HOST" ]; then
    auth_env+="export OLLAMA_HOST=\"${OLLAMA_HOST}\"\n"
fi

if [ -n "$OLLAMA_MODELS" ]; then
    auth_env+="export OLLAMA_MODELS=\"${OLLAMA_MODELS}\"\n"
fi

if [ -n "$auth_env" ]; then
    echo -e "$auth_env" >> "${AGENT_HOME}/.bashrc"
    chown "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/.bashrc"
fi

# --- Generate SSH host keys if missing ---
if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    echo "Generating SSH host keys..."
    ssh-keygen -A 2>/dev/null || {
        ssh-keygen -t rsa -f /etc/ssh/ssh_host_rsa_key -N ''
        ssh-keygen -t ecdsa -f /etc/ssh/ssh_host_ecdsa_key -N ''
        ssh-keygen -t ed25519 -f /etc/ssh/ssh_host_ed25519_key -N ''
    }
fi

# --- Ollama ---
if command -v ollama &>/dev/null; then
    echo "Starting Ollama server..."
    ollama serve &>/var/log/ollama.log &
    sleep 2

    # Pull default model if OLLAMA_DEFAULT_MODEL is set
    if [ -n "$OLLAMA_DEFAULT_MODEL" ]; then
        echo "Pulling model: $OLLAMA_DEFAULT_MODEL"
        su - "${AGENT_USER}" -c "ollama pull $OLLAMA_DEFAULT_MODEL" || echo "WARNING: Failed to pull $OLLAMA_DEFAULT_MODEL"
    fi
fi

# --- Start SSH server ---
echo "Starting SSH server..."
/usr/sbin/sshd

echo "Container ready. Ollama + reasonsforge available."

# Keep container running
exec tail -f /dev/null
