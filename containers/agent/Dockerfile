FROM quay.io/centos/centos:stream9

ARG AGENT_USER=agent

# All system packages in one layer
# --allowerasing: CentOS 9 ships curl-minimal which conflicts with curl
RUN dnf install -y --allowerasing \
        git tmux tar rsync python3-psutil \
        openssh-server openssh-clients \
        'dnf-command(config-manager)' \
        curl which procps-ng sudo zstd pciutils \
    && dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo \
    && dnf install -y gh \
    && curl -fsSL https://pkgs.tailscale.com/stable/centos/9/tailscale.repo -o /etc/yum.repos.d/tailscale.repo \
    && dnf install -y tailscale \
    && dnf clean all

# Create agent user with sudo
RUN mkdir -p /etc/sudoers.d \
    && useradd -m -s /bin/bash -G wheel ${AGENT_USER} \
    && echo "${AGENT_USER} ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/${AGENT_USER} \
    && chmod 0440 /etc/sudoers.d/${AGENT_USER}

# Ollama (local LLM inference on GPU)
RUN curl -fsSL https://ollama.com/install.sh | sh

# SSH server config (hardened, key-only)
RUN mkdir -p /run/sshd /etc/ssh \
    && ssh-keygen -A \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?AddressFamily.*/AddressFamily inet/' /etc/ssh/sshd_config

# Install uv + reasonsforge as agent user
USER ${AGENT_USER}
WORKDIR /home/${AGENT_USER}

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN export PATH=$HOME/.local/bin:$PATH \
    && uv tool install git+https://github.com/benthomasson/ftl-reasons \
    && uv tool install git+https://github.com/benthomasson/reasonsforge

# Workspace directory
RUN mkdir -p /home/${AGENT_USER}/workspace

# Git identity
RUN git config --global user.email 'agent@reasonsforge.com' \
    && git config --global user.name 'Reasonsforge Agent'

USER root

# Entrypoint handles: Tailscale, SSH, Ollama, then keeps container alive
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

ENTRYPOINT ["/entrypoint.sh"]
