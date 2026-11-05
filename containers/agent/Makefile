IMAGE := ghcr.io/benthomasson/reasonsforge-agent
TAG := latest
PLATFORM := linux/amd64

.PHONY: build push run clean

build:
	podman build --platform $(PLATFORM) -t $(IMAGE):$(TAG) .

push:
	podman build --platform $(PLATFORM) -t $(IMAGE):$(TAG) . && podman push $(IMAGE):$(TAG)

run:
	podman run --gpus all -it $(IMAGE):$(TAG)

clean:
	podman rmi $(IMAGE):$(TAG) 2>/dev/null || true
