#!/usr/bin/env bash
# Run on the documented VPS, from the repository checkout.
set -Eeuo pipefail
umask 077
cd "$(dirname "$0")/.."

exec 9>/tmp/changedetection-deploy.lock
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
sudo -n docker version >/dev/null
revision=$(git rev-parse HEAD)
image=${1:-changedetection-local:$(git rev-parse --short=8 HEAD)}
stamp=$(date +%Y%m%d-%H%M%S)
rollback_name="changedetection-rollback-${stamp}"
backup="/var/backups/changedetection/datastore-${stamp}.tar.gz"
state_dir=$(mktemp -d)
old_stopped=false
old_renamed=false
success=false

cleanup() {
    result=$?
    trap - EXIT
    if [ "$success" != true ] && [ "$old_stopped" = true ]; then
        echo 'Deployment failed; restoring the previous container.' >&2
        if [ "$old_renamed" = true ]; then
            sudo docker rm -f changedetection >/dev/null 2>&1 || true
            sudo docker rename "$rollback_name" changedetection
        fi
        sudo docker start changedetection
    fi
    rm -rf "$state_dir"
    exit "$result"
}
trap cleanup EXIT

sudo docker inspect changedetection > "$state_dir/container.json"
old_image=$(sudo docker inspect changedetection --format '{{.Image}}')
sudo docker image inspect "$old_image" > "$state_dir/image.json"
python3 - "$state_dir" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
container = json.loads((root / 'container.json').read_text())[0]
image = json.loads((root / 'image.json').read_text())[0]
mounts = container['Mounts']
if len(mounts) != 1 or mounts[0].get('Name') != 'changedetection-data' or mounts[0]['Destination'] != '/datastore':
    raise SystemExit('Unexpected mounts: review the deployment configuration first.')
ports = container['HostConfig']['PortBindings']
if ports != {'5000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '5000'}]}:
    raise SystemExit('Unexpected published ports: review the deployment configuration first.')
if container['HostConfig']['NetworkMode'] != 'bridge':
    raise SystemExit('Unexpected Docker network: review the deployment configuration first.')
defaults = dict(item.split('=', 1) for item in image['Config'].get('Env', []))
overrides = []
for item in container['Config'].get('Env', []):
    name, value = item.split('=', 1)
    if defaults.get(name) != value:
        if '\n' in value:
            raise SystemExit('Multiline environment value: review the deployment configuration first.')
        overrides.append(item)
(root / 'runtime.env').write_text('\n'.join(overrides) + '\n')
PY

if [ "${SKIP_BUILD:-0}" = 1 ]; then
    sudo docker image inspect "$image" >/dev/null
else
    sudo docker build --label "org.opencontainers.image.revision=${revision}" -t "$image" .
fi
sudo docker run --rm --entrypoint python "$image" -c \
    'import changedetectionio; from pathlib import Path; assert (Path(changedetectionio.__file__).parent / "static/styles/styles.css").is_file()'

sudo mkdir -p /var/backups/changedetection
volume_path=$(sudo docker volume inspect changedetection-data --format '{{.Mountpoint}}')
sudo docker stop -t 60 changedetection
old_stopped=true
sudo tar -C "$volume_path" -czf "$backup" .
sudo chmod 600 "$backup"
sudo tar -tzf "$backup" >/dev/null
sudo docker rename changedetection "$rollback_name"
old_renamed=true
sudo docker run -d --name changedetection --hostname changedetection \
    --restart unless-stopped --env-file "$state_dir/runtime.env" \
    -p 127.0.0.1:5000:5000 -v changedetection-data:/datastore "$image"

ready=false
for attempt in $(seq 1 30); do
    if curl --fail --silent --max-time 5 http://127.0.0.1:5000/ -o "$state_dir/home.html"; then
        ready=true
        break
    fi
    sleep 2
done
[ "$ready" = true ] || { echo 'Application did not become ready.' >&2; exit 1; }
curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:5000/static/styles/styles.css?deploy=${stamp}" -o "$state_dir/styles.css"
cmp changedetectionio/static/styles/styles.css "$state_dir/styles.css"
curl --fail --silent --show-error --max-time 10 http://127.0.0.1/tags/list -o /dev/null
success=true
echo "Deployment successful: ${image}"
echo "Backup: ${backup}"
echo "Previous container (stopped): ${rollback_name}"
