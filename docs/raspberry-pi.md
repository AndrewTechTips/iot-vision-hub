# Running the hub on a Raspberry Pi

The published image (`ghcr.io/andrewtechtips/iot-vision-hub`) is built for both amd64 and arm64,
so a Raspberry Pi 4 or 5 runs the same hub, dashboard and person model as a PC. Docker picks the
right architecture by itself.

## What you need

- A **Raspberry Pi 5**, or a **Pi 4 with 4 GB** or more.
- A **64-bit** OS: Raspberry Pi OS (64-bit) or Ubuntu Server for the Pi. The image is arm64 only;
  32-bit Raspberry Pi OS cannot run it (`uname -m` must say `aarch64`).
- Storage for clips: an SSD over USB 3 lasts far longer than a microSD card under constant writes.
- Cameras the hub can open: **RTSP network cameras** (most IP cameras) or **USB webcams**.
  Camera modules on the ribbon connector go through libcamera, which the hub does not use.

## Install

Install Docker with its official script, then log out and back in so your user can use it:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
```

Fetch the compose files (or clone the repository) and start the hub with PostgreSQL:

```bash
mkdir vision-hub && cd vision-hub
base=https://raw.githubusercontent.com/AndrewTechTips/iot-vision-hub/main
curl -fsSLO "$base/docker-compose.yml" -O "$base/docker-compose.demo.yml"
docker compose pull
docker compose up --wait
docker compose exec api vision-hub create-user admin --role admin
```

To try it before connecting a camera, start the demo cameras instead: four scenes of real
footage, fetched on the first start.

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --wait
```

## Reach it from your network

The compose file publishes the hub on `127.0.0.1` only. To open it from other devices, add a
`docker-compose.override.yml` next to it (Compose reads it automatically) and tell the hub which
names it answers to:

```yaml
services:
  api:
    ports: !override
      - "8000:8000"
    environment:
      VISION_HUB_SECURITY__ALLOWED_HOSTS: localhost,127.0.0.1,raspberrypi.local,192.168.1.50
```

Then open `http://raspberrypi.local:8000`. Browsers only allow notifications and installing the
app on HTTPS pages, so for your phone use a tunnel such as
[Tailscale](https://tailscale.com/kb/1312/serve) (`tailscale serve 8000` gives the Pi an HTTPS
address on your tailnet) or a reverse proxy with a certificate.

## Cameras

Describe them in a `devices.toml` (see
[`devices.example.toml`](../devices.example.toml)), mount it, and point the hub at it. A USB
webcam also needs its device passed through:

```yaml
services:
  api:
    volumes:
      - ./devices.toml:/app/devices.toml:ro
    environment:
      VISION_HUB_VISION__DEVICES_FILE: /app/devices.toml
    devices:
      - /dev/video0:/dev/video0
    group_add:
      - video
```

The file only seeds cameras the database does not have yet; after that, manage them from the
dashboard or the API.

## Keeping up on a small CPU

Each camera has its own thread, and person checks share one more, so a Pi 5's four cores go a
long way. If the dashboard shows frames arriving late (the camera's "frame age" metric rises),
lower the work per camera:

| Setting | Default | On a Pi |
|---|---|---|
| `VISION_HUB_VISION__TARGET_FPS` (analysed frames per second) | 10 | 5 |
| `processing_width` in a camera's detection settings | 640 | 480 |
| `VISION_HUB_CLIPS__WIDTH` (event clips) | 640 | 480 |
| `VISION_HUB_PERSONS__CHECK_INTERVAL_SECONDS` | 2 | 3 |

For reference, on an Apple M4 in the Linux arm64 image, the four demo cameras together use about
1.2 cores and 410 MB of memory, and one person check takes about 0.3 s. A Pi's cores are several
times slower, so expect person checks to take longer; they run in the background and never slow
the cameras down. Measurements in [`performance.md`](performance.md).

## Updating

```bash
docker compose pull && docker compose up --wait
```

`latest` follows the main branch, which only moves when every test passes. To stay on a release,
set `VISION_HUB_IMAGE_TAG` (for example `1.0`) in a `.env` file next to the compose file.

Anyone can check that an image was built by this repository's workflow, from this source:

```bash
gh attestation verify oci://ghcr.io/andrewtechtips/iot-vision-hub:latest --owner AndrewTechTips
```
