# Huong dan ket noi VPS Oracle

## Thong tin may chu

- Instance: `Kaiwa Sensei SV`
- Public IP: `138.2.6.44`
- He dieu hanh: Ubuntu
- Tai khoan SSH: `ubuntu`
- Cong SSH: `22`

## Ket noi bang SSH

File private key dang nam trong thu muc `~/Downloads`:

```text
ssh-key-2025-03-29.key
ssh-key-2025-03-29 (1).key
ssh-key-2025-03-29 (2).key
```

Ba file tren la cac ban sao cua cung mot khoa. Chi can dung mot file, uu tien file khong co hau to:

```bash
chmod 600 ~/Downloads/ssh-key-2025-03-29.key
ssh -i ~/Downloads/ssh-key-2025-03-29.key \
  -o IdentitiesOnly=yes \
  ubuntu@138.2.6.44
```

Khong commit, gui qua chat, hoac chia se file `.key`.

## Kiem tra khoa

Xem fingerprint ma khong hien noi dung private key:

```bash
ssh-keygen -y -f ~/Downloads/ssh-key-2025-03-29.key | ssh-keygen -lf -
```

Fingerprint hien tai:

```text
SHA256:bq+OSQgoGClc59n+uJKnBAFc7pKVlcwA7sgR5Ee/FBo
```

## Sao chep file

Tai file tu VPS ve may:

```bash
scp -i ~/Downloads/ssh-key-2025-03-29.key \
  ubuntu@138.2.6.44:/duong/dan/file .
```

Day file tu may len VPS:

```bash
scp -i ~/Downloads/ssh-key-2025-03-29.key \
  ./file ubuntu@138.2.6.44:/home/ubuntu/
```

## Thu muc va dich vu quan trong

- Source code: `/home/ubuntu/Workspace/changedetection.io`
- Datastore Docker: volume `changedetection-data`, gan vao `/datastore`
- Backup datastore: `/var/backups/changedetection/`
- Container: `changedetection`
- Nginx: cong public `80`, proxy den `127.0.0.1:5000`
- VPS hien dung Docker truc tiep; chua cai Docker Compose.
- Image ung dung phai duoc build tu repo `tugnt/changedetection.io`, dat ten
  `changedetection-local:<commit>`. Image `ghcr.io/dgtlmoon/changedetection.io`
  la ban upstream, khong chua thay doi giao dien rieng cua repo nay.

Lenh kiem tra nhanh:

```bash
sudo docker ps
sudo docker logs --tail 100 changedetection
sudo nginx -t
sudo systemctl status nginx --no-pager
sudo firewall-cmd --zone=public --list-all
curl -I http://127.0.0.1/
```

## GitHub tren VPS

GitHub CLI da dang nhap bang tai khoan `tugnt`:

```bash
gh auth status
git config --global user.name
git config --global user.email
```

Khong in hoac chia se noi dung file `~/.config/gh/hosts.yml` vi file nay chua credential.

## Du lieu khi deploy lai

Settings, watches va lich su khong mat neu container moi van gan dung volume:

```text
changedetection-data:/datastore
```

Script deploy o muc duoi tu dong dung container sau khi build xong, backup
datastore khi khong con ghi du lieu, roi thay container. Container cu duoc giu
lai o trang thai stopped de rollback. Backup co quyen `600`.

Neu can backup rieng, dung container trong luc sao luu, sau do khoi dong lai.
Neu lenh tar gap loi, van phai chay `sudo docker start changedetection`:

```bash
timestamp=$(date +%Y%m%d-%H%M%S)
sudo mkdir -p /var/backups/changedetection
sudo docker stop -t 60 changedetection
sudo tar -C /var/lib/docker/volumes/changedetection-data/_data \
  -czf "/var/backups/changedetection/datastore-${timestamp}.tar.gz" .
sudo chmod 600 "/var/backups/changedetection/datastore-${timestamp}.tar.gz"
sudo docker start changedetection
```

## Quy trinh push code, build va deploy giao dien moi

### 1. Build va push tu may local

Chay trong thu muc project tren Mac:

```bash
cd /Users/ts-trongtung.nguyen/Workspace/changedetection.io
git status --short --branch
git fetch origin
```

Neu local va remote bi phan ky, hop nhat va xu ly conflict truoc khi push.
Khong force-push `master`.

Khi sua SCSS, build CSS truoc khi commit. Dockerfile copy CSS da build vao
image, khong tu chay npm:

```bash
npm --prefix changedetectionio/static/styles ci
npm --prefix changedetectionio/static/styles run build
```

Neu can kiem tra build Python o local:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup.py build
```

Review diff va them dung cac file code can deploy. Khong them datastore,
private key, file `.env` hoac credential. CSS `styles.css` da build can duoc
commit cung SCSS. Vi du:

```bash
git diff --check
git diff --stat
git add -u
# Them rieng cac file moi thuoc thay doi; lan thiet lap quy trinh nay:
git add scripts/deploy-vps.sh HUONG_DAN_KET_NOI_VPS.md
git diff --cached --stat
git commit -m "Update application and VPS deployment workflow"
```

Push bang tai khoan co quyen ghi repo `tugnt/changedetection.io`:

```bash
gh auth switch --hostname github.com --user tugnt
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin master
```

Neu can, chuyen lai tai khoan GitHub mac dinh sau khi push:

```bash
gh auth switch --hostname github.com --user ts-trong-tung-nguyen-rakuten
```

### 2. Pull, build image va deploy tren VPS

Ket noi SSH bang key o muc dau, sau do chay:

```bash
cd /home/ubuntu/Workspace/changedetection.io
git status --short --branch
git pull --ff-only origin master
git log -1 --oneline
bash scripts/deploy-vps.sh
```

Script `scripts/deploy-vps.sh` thuc hien:

1. Khoa deploy de tranh hai lan deploy dong thoi.
2. Kiem tra container hien tai dung volume `changedetection-data`, network
   `bridge` va port `127.0.0.1:5000:5000` theo cau hinh VPS nay.
3. Build image `changedetection-local:<commit>` tu Dockerfile va code checkout.
   Container dang chay van phuc vu trong luc build.
4. Giu cac bien moi truong tuy chinh cua container cu trong file tam quyen rieng.
5. Dung container, backup datastore vao `/var/backups/changedetection/`.
6. Doi ten container cu thanh `changedetection-rollback-<timestamp>`, tao
   container moi voi cung volume, port va restart policy `unless-stopped`.
7. Cho ung dung san sang, so sanh CSS HTTP voi CSS trong repo, kiem tra
   `/tags/list` qua Nginx. Neu deploy loi sau khi dung container cu, script
   tu khoi dong lai container cu.

Script in ten image, file backup va ten container rollback khi thanh cong.
Lan build dau tren VPS ARM64 co the mat vai phut. Cac lan tiep theo dung
Docker build cache. Script khong can npm tren VPS vi CSS da commit tu local.

### 3. Kiem tra sau deploy

Tren VPS:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
sudo docker inspect changedetection --format 'Image={{.Config.Image}} Revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
sudo docker logs --tail 50 changedetection
curl -I http://127.0.0.1/tags/list
sha256sum changedetectionio/static/styles/styles.css
curl -fsS 'http://127.0.0.1/static/styles/styles.css?verify=deploy' | sha256sum
```

Hai SHA256 CSS phai giong nhau. Sau do mo `http://138.2.6.44/tags/list` va
reload bang `Cmd+Shift+R`. Cookie `css_dark_mode` giu lua chon sang/toi cua
nguoi dung; neu truoc do da chon light mode, bam nut doi theme de chon dark.

Neu public van cu nhung localhost moi, kiem tra Nginx/CDN. Neu CSS trong
container van cu, kiem tra image/container dang chay; `git pull` va
`docker restart` khong cap nhat code ben trong image cu.

### 4. Rollback thu cong

Lay ten container cu tu output deploy hoac:

```bash
sudo docker ps -a --filter name=changedetection-rollback
```

Dat dung ten container cu, roi chay:

```bash
rollback_container=changedetection-rollback-YYYYMMDD-HHMMSS
sudo docker stop -t 60 changedetection
sudo docker rm changedetection
sudo docker rename "$rollback_container" changedetection
sudo docker start changedetection
curl -I http://127.0.0.1/
```

Rollback container van dung datastore hien tai. Neu phien ban moi da thay doi
schema/du lieu, can danh gia va phuc hoi backup rieng; khong tu dong giai nen
de tranh ghi de cac thay doi moi. Khong xoa volume `changedetection-data`.
Sau khi xac nhan ban moi on dinh, co the xoa rieng container rollback cu bang
`sudo docker rm <ten-container-cu>`.

### Lan deploy da xac nhan: 2026-10-01 UTC / 2026-10-02 JST

- Source ung dung: commit `48273a989e66d062648fb32fb744fc28f0b4d1b6`.
- Image dang chay: `changedetection-local:48273a98`.
- Datastore: giu nguyen volume `changedetection-data`.
- Backup: `/var/backups/changedetection/datastore-20261001-161842.tar.gz`.
- Container cu: `changedetection-rollback-20261001-161842` (stopped).
- Public `/`, `/tags/list`, `/settings`: HTTP 200.
- HTML mac dinh: `data-darkmode="true"`.
- CSS public: 108.596 bytes, trung voi CSS moi trong repo.
- SHA256 CSS:
  `2c05965e8989d3a24a03604238b1535ec1dca7661787c2528e6fee4076613569`.

Cac commit tai lieu/script sau commit ung dung tren khong thay doi giao dien.
Lan deploy tiep theo, script mac dinh build va gan image theo commit checkout
hien tai.

## Xu ly loi thuong gap

### `Permission denied (publickey)`

Kiem tra dung username, file key va quyen file:

```bash
chmod 600 ~/Downloads/ssh-key-2025-03-29.key
ssh -vv -i ~/Downloads/ssh-key-2025-03-29.key \
  -o IdentitiesOnly=yes ubuntu@138.2.6.44
```

### Khong truy cap duoc cong 80

Kiem tra ca OCI Security List va firewall trong VPS. Firewall tren VPS phai co service `http`:

```bash
sudo firewall-cmd --zone=public --permanent --add-service=http
sudo firewall-cmd --reload
```

### Can thoat SSH

```bash
exit
```
