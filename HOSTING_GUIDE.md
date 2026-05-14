# Discord AI 봇 호스팅 완벽 가이드

> **Oracle Cloud Free Tier** + **Ubuntu 22.04** + **PM2**로 24시간 Discord AI 봇 호스팅하기

---

## 📌 개요

이 문서는 Discord.js 기반 AI 챗봇을 **Oracle Cloud 물리 서버**에서 호스팅하는 전체 과정을 담고 있습니다.

- **비용**: 완전 무시 (Always Free Tier)
- **OS**: Ubuntu 22.04 LTS
- **런타임**: Node.js 20.x
- **프로세스 매니저**: PM2

---

## 1️⃣ Oracle Cloud VM 인스턴스 생성

### 1.1 Compute Instance 생성

1. [Oracle Cloud 콘솔](https://cloud.oracle.com) → **Compute** → **Instances** → **Create Instance**
2. 아래 설정값 입력:

| 항목 | 설정값 |
|------|--------|
| **Name** | `mars` (원하는 이름) |
| **Image** | Canonical Ubuntu 22.04 |
| **Shape** | VM.Standard.E2.1.Micro (Always Free) |
| **SSH Keys** | 새 키 생성 또는 기존 키 업로드 |

### 1.2 네트워킹 설정 (⚠️ 중요)

**Public IP가 활성화되지 않는 경우**가 있습니다. 아래 순서로 설정하세요:

1. **Networking** 섹션 → **Edit**
2. **Virtual Cloud Network**: `Create new virtual cloud network` 선택
3. **Subnet**: `Create new public subnet` 선택
4. **Subnet IPv4 prefixes**: 드롭다운에서 값 선택 (예: `10.0.0.0/24`)
5. **Public IPv4 address**: `Yes`로 활성화

> 💡 **팁**: 만약 Subnet 드롭다운이 비활성화되면, **Networking** → **Virtual Cloud Networks** → **Start VCN Wizard** → **VCN with Internet Connectivity**로 먼저 VCN을 생성한 후, 다시 Compute 생성 시 **기존 VCN 선택**으로 진행하세요.

### 1.3 생성 완료 후 확인

- Instance 상세 페이지에서 **Public IP address** 확인
- 예: `168.110.115.17`

---

## 2️⃣ SSH 접속 (Windows)

### 2.1 SSH 키 파일 권한 설정

Oracle Cloud에서 다운로드한 `.key` 파일 권한을 수정해야 합니다.

```powershell
$keyPath = "D:\Downloads\ssh-key-2026-05-12 (1).key"

# 권한 제거 후 현재 사용자만 읽기 권한 부여
icacls $keyPath /inheritance:r
icacls $keyPath /grant:r "$($env:USERNAME):(R)"

# 확인 (SPECTREPC\이름:(R) 만 있어야 함)
icacls $keyPath
```

> ⚠️ **주의**: `Everyone`, `Authenticated Users` 등 다른 권한이 있으면 SSH 접속이 거부됩니다.

### 2.2 SSH 접속

```bash
ssh -i "D:\Downloads\ssh-key-2026-05-12 (1).key" ubuntu@168.110.115.17
```

처음 접속 시 `yes` 입력:
```
Are you sure you want to continue connecting (yes/no/[fingerprint])? yes
```

---

## 3️⃣ 서버 초기 설정

### 3.1 패키지 업데이트

```bash
sudo apt update && sudo apt upgrade -y
```

팝업(`Pending kernel upgrade`)이 뜨면 `<Ok>` 선택 (Enter).

### 3.2 Node.js 20.x 설치

기존 Node.js(예: 12.x)가 있으면 완전히 삭제 후 재설치:

```bash
# 기존 Node.js 완전 삭제
sudo apt purge -y nodejs npm
sudo apt autoremove -y
sudo rm -rf /usr/local/lib/node_modules
sudo rm -f /usr/local/bin/node /usr/local/bin/npm /usr/bin/node /usr/bin/npm

# NodeSource 저장소 추가 (20.x)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -

# Node.js 20 설치
sudo apt install -y nodejs

# 버전 확인 (v20.x.x 나와야 함)
node -v   # v20.20.2
npm -v    # 10.8.2
```

> ⚠️ **Node.js 12, 14, 16 등 구버전**이면 `?.` (옵셔널 체이닝) 문법을 못 읽어서 봇이 실행되지 않습니다.

### 3.3 PM2 설치

```bash
sudo npm install -g pm2
hash -r
which pm2   # /usr/bin/pm2 확인
```

---

## 4️⃣ 봇 코드 배포

### 4.1 GitHub에서 클론 (Public 저장소)

```bash
cd ~
git clone https://github.com/sandfairy1219/Mars-ai.git marsAI
cd marsAI
npm install
```

> **Private 저장소**일 경우: GitHub Settings → 저장소 Visibility → **Public**으로 변경 후 진행하거나, Personal Access Token을 사용하세요.

### 4.2 `.env` 파일 생성

```bash
cd ~/marsAI
nano .env
```

아래 내용 입력 (실제 값으로 교체):

```env
DISCORD_TOKEN=디스코드_봇_토큰
CLIENT_ID=디스코드_클이언트_ID
OPENCODEGO_API_KEY=오픈코드고_API_키
OPENCODEGO_API_URL=https://opencode.ai/zen/go/v1/chat/completions
DEFAULT_PROVIDER=opencodego
MODELS=opencodego|deepseek-v4-pro|DeepSeek V4 Pro,opencodego|qwen-3.6-plus|Qwen 3.6 Plus,opencodego|glm-5.1|GLM 5.1,opencodego|kimi-k2.6|Kimi K2.6
DEFAULT_MODEL=deepseek-v4-pro
```

저장: `Ctrl+O` → `Enter` → `Ctrl+X`

---

## 5️⃣ 봇 실행 (PM2)

### 5.1 PM2로 봇 시작

```bash
pm2 start ~/marsAI/index.js --name discord-bot
pm2 logs discord-bot
```

**정상 실행 시 로그 예시:**
```
🤖 봇이 준비되었습니다! marsAI#1234
🧠 기본 제공자: opencodego
📝 기본 모델: deepseek-v4-pro
📋 등록된 모델: 4개
   - DeepSeek V4 Pro [opencodego] (deepseek-v4-pro)
   - Qwen 3.6 Plus [opencodego] (qwen-3.6-plus)
   - GLM 5.1 [opencodego] (glm-5.1)
   - Kimi K2.6 [opencodego] (kimi-k2.6)
```

### 5.2 PM2 재시작 문제 해결

만약 `pm2 restart` 시 에러가 난다면:

```bash
# PM2 완전 초기화
pm2 kill
rm -rf ~/.pm2
hash -r

# 다시 시작
pm2 start ~/marsAI/index.js --name discord-bot
```

### 5.3 자동 시작 설정 (서버 재부팅 시)

```bash
pm2 startup
```

출력된 명령어를 **그대로 복사**해서 실행:
```bash
sudo env PATH=$PATH:/usr/bin /usr/lib/node_modules/pm2/bin/pm2 startup systemd -u ubuntu --hp /home/ubuntu
```

그 후 저장:
```bash
pm2 save
```

---

## 6️⃣ 문제 해결 (트러블슈팅)

### ❌ `Permission denied (publickey)`

**원인**: SSH 키 파일 권한이 너무 개방적

**해결**:
```powershell
icacls "키파일경로" /inheritance:r
icacls "키파일경로" /grant:r "$($env:USERNAME):(R)"
```

### ❌ `SyntaxError: Unexpected token '.'`

**원인**: Node.js 버전이 너무 낮음 (12.x, 14.x 등)

**해결**:
```bash
sudo apt purge -y nodejs npm
sudo rm -f /usr/bin/node /usr/bin/npm
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

### ❌ `Error: Cannot find module '/usr/local/lib/node_modules/pm2/lib/ProcessContainerFork.js'`

**원인**: Node.js 업그레이드 후 PM2 경로가 꼬임

**해결**:
```bash
pm2 kill
rm -rf ~/.pm2
hash -r
pm2 start ~/marsAI/index.js --name discord-bot
```

### ❌ `429 Too Many Requests` (Gemini API)

**원인**: Gemini 무료 티어 할당량 초과

**해결**: 
- `.env`에서 Gemini 모델 제거
- `DEFAULT_PROVIDER=opencodego`로 변경
- `DEFAULT_MODEL`을 OpenCodeGo 모델로 설정

### ❌ Discord 봇 Offline 상태

**원인**:
1. `DISCORD_TOKEN`이 잘못됨
2. `CLIENT_ID`가 잘못됨
3. `Message Content Intent`가 활성화되지 않음

**해결**:
- [Discord Developer Portal](https://discord.com/developers/applications) → Bot → **Message Content Intent** ON

### ❌ `Used disallowed intents`

**원인**: Discord Developer Portal에서 `Message Content Intent`를 켜지 않음

**해결**:
1. Discord Developer Portal → 내 애플리케이션
2. **Bot** 탭 → **Privileged Gateway Intents**
3. ✅ **MESSAGE CONTENT INTENT** 활성화
4. Save Changes

### ❌ 쓰레드 안에서 멘션 시 에러

**원인**: 쓰레드 안에서 `startThread()`를 호출할 수 없음

**해결**: `index.js`에서 쓰레드 여부를 확인:
```javascript
const isThread = message.channel.isThread?.() || false;
if (!isDM && !isThread && message.startThread) { ... }
```

---

## 7️⃣ 봇 관리 명령어 정리

### PM2 명령어

```bash
pm2 status                    # 봇 상태 확인
pm2 logs discord-bot          # 실시간 로그 보기
pm2 logs discord-bot --lines 50  # 마지막 50줄 로그
pm2 restart discord-bot       # 봇 재시작
pm2 stop discord-bot          # 봇 중지
pm2 delete discord-bot        # 봇 삭제
pm2 monit                     # 모니터링 대시보드
pm2 save                      # 현재 프로세스 목록 저장
```

### SSH 접속 명령어 (Windows)

```bash
ssh -i "D:\Downloads\ssh-key-2026-05-12 (1).key" ubuntu@168.110.115.17
```

---

## 8️⃣ 프로젝트 구조

```
marsAI/
├── index.js           # 메인 봇 코드
├── package.json       # 의존성
├── .env               # 환경 변수 (API 키 등)
├── .env.example       # 환경 변수 예시
├── .gitignore         # Git 무시 파일
└── README.md          # 사용법
```

---

## 9️⃣ 참고 사항

- **대화 히스토리**: 메모리에만 저장되며, 봇 재시작 시 초기화됩니다.
- **모델 선택**: 사용자별로 `/model` 명령어로 기본 모델을 변경할 수 있습니다.
- **.env 파일**: 절대 GitHub에 올리지 마세요. `.gitignore`에 포함되어 있어야 합니다.

---

## 📝 작성일

2026-05-12

**작성자**: sandfairy1219
