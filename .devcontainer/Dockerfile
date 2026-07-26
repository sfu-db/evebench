# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.11-trixie AS dev



ARG USERNAME=user
ARG USER_UID=1000
ARG USER_GID=1000


ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/user

ENV VIRTUAL_ENV=/opt/venv

ENV UV_TOOL_BIN_DIR=/home/user/.local/bin
ENV PATH=$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR:$PATH
ENV UV_PROJECT_ENVIRONMENT=$VIRTUAL_ENV

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN --mount=type=cache,target=/var/cache/apt/ \
    --mount=type=cache,target=/var/lib/apt/ \
    apt-get update && \
    apt-get install --no-install-recommends --yes \
      build-essential \
      ca-certificates \
      curl \
      git \
      graphviz \
      locales \
      sqlite3 \
      ssh \
      sudo \
      vim \
      tmux \
      wget \
      unzip && \
    # Download and extract Java 17
    wget -qO /tmp/openjdk-17.tar.gz https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.10%2B7/OpenJDK17U-jdk_x64_linux_hotspot_17.0.10_7.tar.gz && \
    mkdir -p /opt/java/openjdk-17 && \
    tar -xzf /tmp/openjdk-17.tar.gz -C /opt/java/openjdk-17 --strip-components=1 && \
    rm /tmp/openjdk-17.tar.gz && \
    # Set up user
    groupadd --gid $USER_GID user && \
    useradd --create-home --no-log-init --gid $USER_GID --uid $USER_UID --shell /usr/bin/bash user && \
    mkdir -p /opt/venv && chown user:user /opt/venv && \
    echo 'user ALL=(root) NOPASSWD:ALL' > /etc/sudoers.d/user && chmod 0440 /etc/sudoers.d/user

ENV JAVA_HOME=/opt/java/openjdk-17
ENV PATH=${JAVA_HOME}/bin:${PATH}

# Install Gradle
RUN wget https://services.gradle.org/distributions/gradle-7.3.3-bin.zip -P /tmp && \
    unzip -d /opt/gradle /tmp/gradle-7.3.3-bin.zip && \
    rm /tmp/gradle-7.3.3-bin.zip

ENV GRADLE_HOME=/opt/gradle/gradle-7.3.3
ENV PATH=${GRADLE_HOME}/bin:${PATH}

RUN git config --system --add safe.directory '*'
RUN sh -c "$(curl -fsSL https://starship.rs/install.sh)" -- --yes
RUN printf 'export PATH="%s:$PATH"\n' "$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR" > /etc/profile.d/devcontainer-path.sh

SHELL ["/bin/bash", "-c"]

USER user

RUN mkdir -p "$HOME/.local/bin" "$HOME/.local/share"


RUN uv venv "${VIRTUAL_ENV}"



ARG NODE_VERSION="24"
ARG NVM_VERSION="0.40.3"
ARG PNPM_VERSION="latest"
ENV NVM_DIR=/home/user/.nvm
ENV NVM_SYMLINK_CURRENT=true

RUN mkdir -p "$NVM_DIR" \
    && curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh | bash \
    && source "$NVM_DIR/nvm.sh" \
    && nvm install "$NODE_VERSION" \
    && nvm alias default "$NODE_VERSION" \
    && nvm use default \
    && corepack enable \
    && corepack prepare pnpm@${PNPM_VERSION} --activate



RUN mkdir -p "$HOME/.history" && \
    echo 'export HISTFILE=$HOME/.history/.bash_history' >> "$HOME/.bashrc" && \
    echo 'export HISTSIZE=10000'                        >> "$HOME/.bashrc" && \
    echo 'export HISTFILESIZE=20000'                    >> "$HOME/.bashrc" && \
    echo 'export HISTCONTROL=ignoredups:erasedups'      >> "$HOME/.bashrc" && \
    echo 'shopt -s histappend'                          >> "$HOME/.bashrc" && \
    echo 'PROMPT_COMMAND="history -a; history -c; history -r"' >> "$HOME/.bashrc" && \
    echo 'export PATH=$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR:$PATH' >> "$HOME/.bashrc" && \
    echo '[[ $- == *i* ]] && bind "\e[A":history-search-backward' >> "$HOME/.bashrc" && \
    echo '[[ $- == *i* ]] && bind "\e[B":history-search-forward'  >> "$HOME/.bashrc" && \
    echo 'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi'  > "$HOME/.bash_profile" && \
    echo 'export NVM_DIR=/home/user/.nvm'                          >> "$HOME/.bashrc" && \
    echo 'export PATH=$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR:$NVM_DIR/current/bin:$PATH' >> "$HOME/.bashrc" && \
    echo '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'          >> "$HOME/.bashrc" && \
    echo '[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"' >> "$HOME/.bashrc"

SHELL ["/bin/bash", "-lc"]

USER root
RUN mkdir -p /workspaces/evebench && chown user:user /workspaces/evebench
USER user
