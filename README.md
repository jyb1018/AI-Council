# AI-Council

**사용 중인 AI 구독의 공식 CLI를 연결하여, 독립 답변 → 익명 상호 비평 → 답변 수정 → 최종 종합을 수행하는 로컬 MCP 서버입니다.**

Version **1.0.0** · Python **3.11+** · macOS / Linux / WSL · MIT

```text
MCP 지원 클라이언트 / ai-council CLI
                  │
             Council engine
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
   codex exec  claude -p  agy stream-json
   ChatGPT     Claude     Google 계정
        └─────────┼─────────┘
          SQLite 실행 기록
```

API 키를 수집하거나 구독 OAuth 토큰을 추출하지 않습니다. 이미 로그인한 공식 CLI를 로컬 subprocess로 실행합니다. 웹 Chat/Work/Cowork 세션을 자동 클릭하거나 그 대화 메모리를 복사하는 제품은 아닙니다.

> **실제 계정 검증 범위를 구분해 주세요.** 모의 제공자 기반 토론·장애 복구 테스트와 실제 CLI 응답 형식 기반 파서 테스트가 포함되어 있습니다. 사용자 구독 계정으로 실제 모델을 호출한 검증은 수행하지 않았습니다. `demo` 결과는 모두 `[SIMULATED]`이며 모델 성능의 증거가 아닙니다. CLI 변경·구독 정책·추가 크레딧 설정은 별도로 확인하셔야 합니다.

## 1. 설치하고 계정 없이 실행하기

```bash
git clone https://github.com/jyb1018/AI-Council.git
cd AI-Council
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

ai-council demo 'Personal State OS는 SQLite로 시작해도 될까요?'
```

`demo`는 세 모의 제공자로 10회 호출을 수행하며 실제 CLI나 네트워크를 사용하지 않습니다. 결과 JSON에서 `status: completed`, `simulated: true`, `attempts: 10`을 확인해 주세요. 새 기본 모의 DB는 macOS에서 `~/Library/Application Support/ai-council/demo.sqlite3`, Linux/WSL에서 `~/.local/share/ai-council/demo.sqlite3`입니다. macOS에서도 기존 `~/.local/share/ai-council/demo.sqlite3`가 있으면 그 파일을 계속 사용합니다.

저장 경로에 쓰기 권한이 없으면 `database_permissions` 오류가 표시됩니다. `ai-council demo --database "$PWD/.council/demo.sqlite3"`처럼 쓰기 가능한 경로를 명시할 수 있습니다.

PyPI에는 이 저장소 버전을 게시하지 않았습니다. 위처럼 저장소에서 설치해 주세요. 네이티브 Windows는 지원하지 않으며 WSL을 사용해 주세요.

## 채팅방형 Web UI와 Buzz

```bash
ai-council web --demo --database "$PWD/.council/web-demo.sqlite3" --open
```

참여자별 모델·추론 수준, 비평·수정 라운드, 종합 담당을 선택하고 토론을 시작할 수 있습니다. 검증된 각 답변을 전체 토론 완료 전에 표시하며 중지·기록 재조회도 지원합니다. 실제 CLI 목록을 조회하므로 모델마다 서로 다른 추론 옵션을 보존합니다.

설치형 Buzz에는 사용자 정의 ACP 런타임으로 연결합니다. 채팅방에서 질문하고 각 LLM 의견을 받으며, 참여자 설정은 로컬 Web 화면을 사용합니다. 로컬 UI·ACP 경계와 **실제 Buzz 채널의 모의 토론 및 Codex·Claude 운영 토론(실제 7회 호출, 답글 9건)을 검증했습니다.** [연결 방법·현재 제한](docs/buzz.md)을 참고해 주세요.

## 2. 실제 구독 연결하기

공식 클라이언트를 먼저 설치하고 터미널에서 직접 로그인해 주세요. 모델 이름은 계정과 CLI가 제공하는 값만 사용하며, 이 프로젝트에서 임의 버전을 하드코딩하지 않습니다.

| ID 예시 | 공식 클라이언트 | 인증·실행 방식 | 기본 상태 |
|---|---|---|---|
| `codex` | Codex CLI | `codex login` 후 `codex exec` | 인증·비용 설정 확인 필요 |
| `claude` | Claude Code | `claude auth login` 후 `claude -p` | 인증·비용 설정 확인 필요 |
| `gemini` | Antigravity CLI | `agy`에서 Google 로그인 후 headless stream-json | **비활성화, 명시적 동의 필요** |

```bash
cp examples/council.toml council.toml
codex login
claude auth login
ai-council --config "$PWD/council.toml" doctor
```

`doctor`는 실행 파일, 필수 옵션, 버전, 확인 가능한 인증 유형을 조회합니다. 모델 추론을 요청하지 않으며 남은 제공자 quota를 추측하지 않습니다. 인증 확인은 토큰 파일을 직접 읽지 않고 공식 CLI의 상태 명령만 사용합니다.

**계정의 추가 사용량/크레딧 결제 설정을 확인한 후**, 사용할 제공자 설정의 `subscription_confirmed = true`로 변경해 주세요. 이 설정은 사용자의 확인 기록이지 제공자 결제를 차단하는 API가 아닙니다. Claude Console/API-key 인증은 허용하지 않고 Codex는 ChatGPT 로그인 방식을 강제합니다. Council 자체에는 API fallback이나 자동 재시도가 없습니다.

```bash
ai-council --config "$PWD/council.toml" debate \
  'Personal State OS에 event sourcing이 필요한가요?' \
  --participants codex,claude --rounds 1 --chair codex \
  --idempotency-key architecture-001
```

Google를 추가하려면 `enabled`, `subscription_confirmed`, `allow_inherited_tools`를 모두 `true`로 설정해야 합니다. **Antigravity는 기존 도구·설정을 상속합니다. 임시 작업 폴더만으로 파일 접근을 격리했다고 볼 수 없습니다.** 해당 CLI의 도구 권한과 MCP 설정을 감사하고, 가능하면 별도 OS 사용자/격리 환경에서 실행해 주세요. 안전한 무도구 실행을 일괄 보장할 공식 옵션을 확인하지 못해 기본 활성화하지 않았습니다. 상세한 근거와 제한은 [제공자 문서](docs/providers.md)에 있습니다.

## 3. 토론 프로토콜

| 단계 | 처리 방식 | 보장하는 경계 |
|---|---|---|
| Blind position | 모든 참여자에게 동일한 질문·자료만 전달합니다. | **모든 독립 답변이 성공하기 전에는 상대 답변을 전달하지 않습니다.** |
| Cross-critique | 작성자를 무작위 A/B/C…로 치환하고 각 참여자가 다른 모든 답변을 비평합니다. | 자기 답변 대상 금지, 누락·중복 대상 금지입니다. |
| Revision | 자신의 답변과 자신에게 향한 비평을 전달합니다. | 수정 사항을 별도 구조화 필드에 기록합니다. |
| Synthesis | 지정 의장에게 익명 수정안·비평 기록을 전달합니다. | 합의·미합의·다음 행동·한계를 분리합니다. |

참여자는 2–6개, 비평/수정 반복은 1–3회입니다. 기본은 1회입니다. 성공 시 호출 수는 **`N × (1 + 2R) + 1`**입니다. 2개 모델/1회는 7회, 3개 모델/1회는 10회, 3개 모델/2회는 16회입니다. 한 호출에서 모든 상대를 비평하므로 비평 항목이 늘어나도 호출 수 자체는 제곱으로 증가하지 않습니다. 토큰·문맥 크기는 여전히 증가합니다.

표시는 익명화하지만 모델의 문체나 자기소개까지 완전히 숨기는 암호학적 익명성은 아닙니다. 서로 다른 CLI 호출마다 새 프로세스/작업 폴더를 사용하며 세션 재사용은 하지 않습니다. `chair` 생략 시 첫 참여자가 의장입니다. 의장 편향과 모델 간 오류 상관관계는 남습니다. 단일 모델 기준선은 `ask`로 비교해 주세요.

## 4. MCP 연결

[설정 예시](examples/mcp.json)의 절대 경로를 수정하여 **로컬 stdio MCP를 실행할 수 있는 클라이언트**에 등록해 주세요.

```json
{
  "mcpServers": {
    "ai-council": {
      "command": "/ABSOLUTE/PATH/AI-Council/.venv/bin/ai-council",
      "args": ["--config", "/ABSOLUTE/PATH/AI-Council/council.toml", "serve"]
    }
  }
}
```

GUI로 실행한 클라이언트의 `PATH`가 터미널과 다를 수 있습니다. 이때 TOML의 각 `executable`을 공식 CLI의 절대 경로로 지정해 주세요. 서버 자체의 경로와 혼동하지 마세요.

**브라우저 ChatGPT 등에 원격 URL만 등록하는 방식은 이 버전에 포함되지 않습니다.** 원격 HTTP/OAuth 브리지, SaaS 인증·계정 공유, 공개 터널은 제공하지 않습니다. 클라이언트가 로컬 stdio를 지원하는지 확인해 주세요.

| MCP tool | 용도 |
|---|---|
| `council_debate` | 질문·참여자·rounds·chair로 전체 토론을 시작합니다. |
| `council_review` | `material`로 제공한 설계/문서를 같은 프로토콜로 검토합니다. |
| `council_ask` | 단일 `provider`의 기준선 답변을 실행합니다. |
| `council_status` / `council_result` | 진행 상태 / 완료 결과와 선택적 transcript를 읽습니다. |
| `council_cancel` / `council_resume` | 취소 / 명시적으로 승인한 재개를 수행합니다. |
| `council_sessions` / `council_providers` | 최근 세션 / 로컬 제공자 설정을 확인합니다. |

호출 예시:

```json
{
  "question": "이 설계에서 가장 먼저 바꿔야 할 점은 무엇인가요?",
  "participants": ["codex", "claude"],
  "context": "단일 사용자용 MCP 서버이며 SQLite에 세션을 저장합니다.",
  "rounds": 1,
  "chair": "codex",
  "idempotency_key": "review-001"
}
```

시작 도구는 즉시 `session_id`를 반환합니다. `council_status({"session_id":"…"})`를 수 초 간격으로 조회한 뒤, 완료되면 `council_result({"session_id":"…","transcript":true})`를 사용해 주세요. 중간 답변과 별칭 매핑은 완료 전에는 노출하지 않습니다. 실패한 세션을 완료한 것처럼 축소 종합하지 않습니다.

실행 작업은 **MCP 서버 프로세스가 살아 있는 동안** 진행됩니다. 클라이언트가 서버를 종료하면 작업도 중단됩니다. 재시작 시 자동 재과금하지 않고 `interrupted`로 표시합니다. 재개는 `council_resume`의 `accept_duplicate_cost: true`에 대한 사용자 동의가 필요합니다. 중단 직전 응답 저장에 실패한 호출은 이미 과금됐어도 반복될 수 있습니다.

## 5. CLI·자료·내보내기

```bash
# 단일 모델 기준선
ai-council --config council.toml ask '이 설계를 검토해 주세요.' --participants codex

# 설계 파일과 출처 자료를 명시적으로 제공
ai-council --config council.toml review '실패 복구 관점에서 검토해 주세요.' \
  --participants codex,claude --context-file design.md --sources-file examples/sources.json

ai-council --config council.toml status
ai-council --config council.toml result SESSION_ID --transcript
ai-council --config council.toml export SESSION_ID --output report.md --format markdown
ai-council --config council.toml export SESSION_ID --output report.json --transcript
ai-council --config council.toml resume SESSION_ID --accept-duplicate-cost
```

CLI 토론은 완료까지 기다린 뒤 JSON을 출력합니다. `Ctrl+C`는 실행을 중단하고 완료된 단계는 남깁니다. 내보내기는 기본적으로 기존 파일을 덮어쓰지 않습니다. 덮어쓰려면 `--overwrite`를 명시해 주세요. 공유할 보고서의 기밀 내용은 직접 검토해 주세요.

`sources`는 `id`, `title`, `content`, 선택적 `url`을 갖는 배열입니다. 서버는 자료를 웹에서 자동 조회하지 않습니다. 응답의 `supporting_sources`가 제공된 ID인지 검증하지만, **출처 내용의 진위나 주장을 실제로 뒷받침하는지는 자동 검증하지 않습니다.** 모델의 `confidence`도 보정된 확률이 아닙니다.

## 6. 운영·안전 경계

설정은 `--config` 또는 `AI_COUNCIL_CONFIG`로 명시합니다. 현재 작업 폴더의 임의 설정을 자동 로드하지 않습니다. DB 상대 경로는 TOML 위치를 기준으로 해석합니다.

기본 제한은 세션당 32회 시도, 설정 ID별 UTC 하루 100회 시도, 전체 동시 호출 3개, 활성 세션 2개, 같은 실제 CLI 종류별 동시 호출 1개입니다. 실패·중단 시도도 예산에서 차감합니다. **이는 로컬 호출 횟수 제한이며 제공자의 실제 남은 구독량이나 토큰 예산은 아닙니다.** 계정별 잔여량은 `null`로 표시합니다. 동일 계정에 여러 설정 ID를 만들면 일일 제한은 ID별로 적용됩니다.

기본 CLI 호출 제한 시간은 300초, 전체 세션 제한은 1,800초입니다. 프롬프트는 최대 120,000자이며 초과하면 실패합니다. 답변·비평을 몰래 잘라내지 않습니다. stdout와 stderr 합산 최대 1 MiB를 넘기면 프로세스 그룹을 종료합니다.

SQLite는 한 DB당 한 writer만 허용합니다. 이미 MCP 서버가 실행 중이면 별도 CLI 실행은 다른 DB를 쓰거나 MCP 도구를 사용해 주세요. `status/result/export` 읽기는 동시에 가능합니다. 새 기본 실제 DB는 macOS에서 `~/Library/Application Support/ai-council/council.sqlite3`, Linux/WSL에서 `~/.local/share/ai-council/council.sqlite3`입니다. macOS에서도 기존 `~/.local/share/ai-council/council.sqlite3`가 있으면 그 파일을 계속 사용하며 자동 이동하지 않습니다. 실제 DB와 모의 DB는 각 파일의 존재 여부로 독립적으로 선택합니다. 명시적 설정은 이 기본 경로보다 우선하며, 예시 설정은 저장소 아래 `.council/state.sqlite3`를 사용합니다.

**DB는 암호화되지 않습니다.** 질문·자료·응답이 평문으로 저장됩니다. 파일 권한은 0600으로 제한하지만 같은 사용자·관리자의 접근을 차단하지는 않습니다. 로그에는 원문 CLI 오류나 토큰을 남기지 않으며 인증 환경변수는 allowlist에서 제외합니다. 단, 공식 CLI는 본인 설정과 인증 저장소를 읽습니다. 이것은 강한 OS 샌드박스가 아닙니다. 자세한 위협 모델은 [SECURITY.md](SECURITY.md)를 읽어 주세요.

## 7. 개발·검증

```bash
python -m pip install -e '.[dev]'
ruff check .
python -m pytest -q --cov=ai_council
python -m build
```

테스트는 블라인드 경계, 익명 비평 대상, 잘못된 JSON/출처, 예산·중복 요청, 프로세스 취소·출력 제한, SQLite 재시작, CLI 내보내기 및 **실제 MCP SDK stdio 왕복**을 포함합니다. MCP SDK가 없는 제한 환경에서는 wire test가 skip되며 CI에서는 SDK 설치를 필수로 확인합니다. 실제 제공자 인증·사용량을 CI에 넣지 않습니다.

자동화 결과는 저장소의 [Actions](https://github.com/jyb1018/AI-Council/actions)에서 확인해 주세요. 실패한 CI나 수행하지 않은 실계정 테스트를 검증 완료로 취급하지 않습니다. 새 모델/CLI 버전을 적용할 때에는 `doctor → ask → 2인 debate → 3인 debate` 순서로 적은 사용량부터 확인해 주세요.

구조·상태 전이는 [아키텍처 문서](docs/architecture.md), 공식 CLI 근거는 [제공자 문서](docs/providers.md), 변경 내역은 [CHANGELOG](CHANGELOG.md)를 참고해 주세요.
