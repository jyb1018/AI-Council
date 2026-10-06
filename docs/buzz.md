# Buzz 채팅방 및 로컬 Web UI

## 현재 지원 범위

설치형 **Buzz 0.5.26**의 공식 사용자 정의 ACP 런타임을 사용합니다. Buzz 앱 자체를 수정하지 않습니다. Buzz 채팅방은 질문 입력과 의견 공유를 담당하고, AI-Council의 로컬 Web 화면은 여러 참여자의 모델·추론 설정 및 실행 관찰을 담당합니다. Buzz 기본 에이전트 설정창의 단일 추론 선택기를 여러 LLM 설정으로 오인하지 않도록 ACP 모델 옵션은 별도로 광고하지 않습니다.

검증된 답변이 SQLite에 기록되면 Web UI와 Buzz bridge가 1초 간격으로 확인합니다. 전체 토론 완료를 기다리지 않고 각 단계의 참여자 답변을 표시합니다. 토큰 단위 초안이나 내부 사고 과정은 공개하지 않습니다. 모델에게 제공되는 독립 답변 장벽은 그대로 유지됩니다.

## Web 화면 실행

```bash
# 계정이나 실제 모델 호출 없이 UI 확인
ai-council web --demo --database "$PWD/.council/web-demo.sqlite3" --open

# 실제 제공자: 먼저 기존 TOML의 구독 인증 및 Antigravity 도구 정책을 확인
ai-council --config "$PWD/council.toml" web \
  --database "$PWD/.council/web.sqlite3" \
  --endpoint "$PWD/.council/web-endpoint.web.json" --open
```

화면 오른쪽에서 참여자·모델·추론·라운드·종합 담당을 선택하고 저장합니다. 실행 중에는 설정 변경이 거부됩니다. 한 Web 서버는 한 번에 하나의 토론을 실행합니다. 기존 MCP 서버와 동시에 사용할 때에는 별도 DB를 지정해 주세요.

서버는 `127.0.0.1`에만 바인딩합니다(기본 포트 8765, `--port`로 변경). 서버가 안내하는 `http://127.0.0.1:PORT/#TOKEN` 링크를 사용합니다. 토큰은 URL fragment에서 읽은 뒤 주소창에서 제거하고 탭의 sessionStorage에 보관합니다. API는 bearer 인증과 Host/Origin 검사를 수행합니다. 인증 링크나 endpoint 파일을 채팅방·Git에 공유하지 마세요.

모델·추론 초안은 DB 옆 `*.web-profile.json`에 저장됩니다. 기존 TOML의 실행 파일·구독 동의·도구 권한·예산을 변경할 수는 없습니다. 재시작 후 저장된 선택이 CLI 목록에 없으면 자동 교체하지 않고 오류를 표시합니다.

## Buzz 연결

1. Buzz 앱에서 본인이 개인 키를 안전하게 보관하고 초기 설정을 완료합니다. 사용할 워크스페이스와 전용 채널을 선택합니다. 개인 키를 AI-Council 설정에 복사하지 않습니다.
2. 위 Web 서버를 실행해 두고 사용할 채널 UUID를 확인합니다.
3. 설치된 가상환경에서 다음 명령으로 **자격 증명 없는** 런타임 정의를 생성합니다.

```bash
ai-council buzz-config \
  --endpoint "$PWD/.council/web-endpoint.web.json" \
  --channel YOUR-CHANNEL-UUID > "$PWD/.council/ai-council.json"
```

4. Buzz의 사용자 정의 런타임 설정에 이 JSON을 등록합니다. macOS Buzz 0.5.26은 앱 데이터의 `custom_harnesses/` 디렉터리도 읽습니다. 기본 위치는 `~/Library/Application Support/xyz.block.buzz.app/custom_harnesses/`입니다. 생성된 `command`는 현재 Python 절대 경로이므로 해당 가상환경을 유지해야 합니다.
5. 에이전트 생성 화면에서 Customize로 런타임 **AI-Council**을 선택한 뒤 **Use harness defaults**로 전환합니다. 이 bridge는 여러 LLM 설정을 Web에 위임하므로 Buzz에서 개별 모델을 선택하지 않습니다. 개인 에이전트의 접근 정책은 **owner-only**, 실행 풀은 하나로 유지합니다. Buzz의 공식 MCP 서버가 에이전트 식별 정보를 전달해야 합니다. 식별 정보가 없으면 실행 전 `buzz_identity`로 실패합니다.
6. 설정한 채널에서 에이전트를 멘션하고 한 메시지에 한 질문을 보냅니다.

```text
@AI-Council /council Personal State OS에 event sourcing이 필요한가요?
```

bridge는 명시한 채널만 허용하며 일반 채팅·과거 대화·여러 이벤트를 합친 요청·steering을 토론 시작으로 해석하지 않습니다. Buzz가 전달하는 현재 이벤트를 멱등성 키로 사용합니다. 각 의견과 최종 결과는 해당 메시지의 스레드에 게시됩니다. Web에서 직접 시작한 토론은 자동으로 Buzz에 게시하지 않습니다.

기본 Buzz CLI 경로는 `/Applications/Buzz.app/Contents/MacOS/buzz`입니다. 다른 환경에서는 생성된 args에 `--buzz-binary`와 공식 실행 파일의 절대 경로를 추가합니다. Buzz의 자격 증명은 런타임이 전달한 메모리 내 환경만 사용하며 LLM CLI나 Web 서버에 전달하지 않습니다.

## 실패·취소·재연결

- Web의 토론 중지 또는 Buzz ACP 취소는 실행 중인 Council과 CLI 프로세스를 취소합니다.
- Buzz 게시에 실패하면 자동 재게시하지 않고 토론을 취소합니다. 이미 완료한 답변은 Web과 SQLite에 남습니다. Web 서버에 연결할 수 없어 취소 확인도 실패하면 Buzz 에이전트 실행 기록에 이를 알립니다.
- 동일 이벤트 재전달은 같은 Council 세션을 재사용합니다. endpoint 옆 `buzz-delivery/`의 전송 기록으로 이미 확인된 게시를 생략합니다. 같은 채널의 bridge 실행은 프로세스 내부 및 파일 잠금으로 직렬화합니다.
- 게시 성공과 로컬 기록 저장은 하나의 트랜잭션이 아닙니다. 그 사이 프로세스가 종료되면 재전달 시 중복 게시될 수 있습니다. 게시 결과가 불명확하면 채팅방을 확인해 주세요. 외부 게시의 exactly-once는 보장하지 않습니다.
- 중단된 실제 모델 호출은 자동 재개하지 않습니다. 기존 `resume --accept-duplicate-cost` 동의 규칙이 유지됩니다.

## 모델·추론 조회 계약

| CLI | 모델 목록 | 추론 수준 |
|---|---|---|
| Codex | `app-server` 초기화 → `model/list` 전체 페이지, 숨김 모델 포함 | 각 모델의 `supportedReasoningEfforts`를 그대로 표시 |
| Claude | 공식 stream-json 제어 프로토콜의 initialize 응답 `models` | 각 모델의 `supportedEffortLevels`; 목록이 없으면 기본값만 |
| Antigravity | `agy models`의 정확한 모델 ID와 변형 | 설치된 `agy --help`의 전체 `--effort` 옵션 |

Antigravity는 현재 모델별 capability 목록을 제공하지 않습니다. 따라서 모든 CLI 옵션을 노출하되 **CLI 전체 옵션이며 모델별 지원은 미확인**이라고 표시합니다. 목록에 없는 모델을 추정·합성하거나 공통 3단계로 축약하지 않습니다. 실제 실행이 거부되는 조합은 실패로 표시하며 자동으로 낮은 수준으로 바꾸지 않습니다. 표시되는 설정은 요청값이며, 제공자가 실제 적용한 추론량을 입증하는 것은 아닙니다.

2026-10-06 설치본에 구현 코드로 추론 없이 조회한 결과: Codex 9개 모델(low/medium/high/xhigh/max/ultra의 모델별 부분집합), Claude 12개 모델(모델별 옵션 상이), Antigravity 14개 모델 변형 및 CLI 옵션 low/medium/high/xhigh/max. 값은 고정하지 않고 재조회합니다.

공식 Buzz 근거: [설치 버전의 ACP 연동 문서](https://github.com/block/buzz/blob/desktop-v0.5.26/crates/buzz-acp/README.md), [이벤트 프레이밍](https://github.com/block/buzz/blob/desktop-v0.5.26/crates/buzz-acp/src/queue.rs), [사용자 정의 런타임](https://github.com/block/buzz/blob/desktop-v0.5.26/desktop/src-tauri/src/managed_agents/custom_harnesses.rs).

## 검증 상태

로컬 Web의 모의 10회 토론·설정 저장·기록 재조회, HTTP 인증/Origin/Host 차단, 진행 중 설정 잠금·관찰·독립 장벽·취소, 실제 ACP stdio 초기화와 ACP→HTTP→모의10호출→fake Buzz CLI12게시(같은 이벤트 재전달 추가0건), 모의 Buzz 전송의 중복/손상 기록/실패/응답 유실, 세 CLI의 실제 모델 목록 조회를 검증했습니다. **2026-10-06 사용자 승인 후 실제 Buzz relay에서도 모의 토론 10회와 스레드 답글 12건을 확인했습니다. 이후 운영 전환에서 Codex·Claude 실제 추론 7회와 Buzz 답글 9건도 검증했습니다.**

## 이 컴퓨터에 등록된 연결

- 워크스페이스: `wss://sesac-ecommerce01.communities.buzz.xyz`
- 채널: `AI Council` (`580f1e42-3eb9-444d-a083-d997e6d86a2b`)
- 에이전트: `AI-Council`, 이 컴퓨터 실행, Only me, 동시 실행1개
- 현재 모드: **실제 Codex + Claude**, 1라운드, 종합 담당 Codex. 운영 DB는 `.council/buzz-live.sqlite3`, 모의 기록은 기존 DB에 보존합니다.
- 운영 프로필: Codex `gpt-6-sol / medium`, Claude `sonnet / medium`(조회된 이름 Sonnet 5.5). `.council/buzz-live.web-profile.json`에 저장되며 Web에서 변경할 수 있습니다. Gemini는 기존 TOML의 비활성/미동의 설정을 유지합니다.
- 기존 `council.toml`의 두 제공자 구독 동의와 공식 CLI 로그인 점검을 확인했습니다. 실제 청구액과 모든 모델·추론 조합의 성공을 보장하지는 않습니다.

저장소 루트에서 연결 서버를 다시 실행할 수 있습니다. 이미 실행 중이면 포트/DB 중복 오류가 발생하므로 기존 서버를 먼저 확인해 주세요.

```bash
.venv/bin/ai-council --config "$PWD/council.toml" web \
  --database "$PWD/.council/buzz-live.sqlite3" --port 8767 \
  --endpoint "$PWD/.council/buzz-endpoint.web.json" --open
```

등록된 에이전트는 이 endpoint 경로를 사용합니다. 서버가 실행 중일 때 채널에서 `@AI-Council /council 질문`을 보냅니다. Buzz의 자동 멘션 목록에서 AI-Council을 선택해 실제 멘션을 넣어 주세요. 서버는 자동 시작 서비스가 아니므로 컴퓨터 재시작 후 위 명령으로 실행해 주세요. 종료 직후 동일 포트가 잠시 사용 중이라고 나오면 소켓 해제를 기다린 뒤 실행합니다. 재시작하면 인증 링크가 바뀝니다.
