# Provider integration notes

문서 확인일: **2026-10-06**. 아래는 공식 문서에 근거한 구현 계약이며, 실제 구독 계정 smoke test를 대체하지 않습니다. 서비스 약관 해석이나 무제한/무과금 보장이 아닙니다.

## Codex

- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Developer commands / CLI](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
- [Configuration reference](https://developers.openai.com/codex/config-reference)

`codex exec`에 stdin으로 질문을 보내고 `--json` JSONL에서 `item.completed`의 `agent_message`와 단 하나의 `turn.completed`를 확인합니다. 종료 이벤트가 없거나 오류 이벤트가 있으면 성공으로 간주하지 않습니다. usage가 없으면 0으로 꾸미지 않고 `null`을 사용합니다.

매 호출 `--ignore-user-config`, `--ephemeral`, `--skip-git-repo-check`, `--sandbox read-only`, `--output-schema`를 사용합니다. `forced_login_method="chatgpt"`, `features.shell_tool=false`, `features.unified_exec=false`, `web_search="disabled"`를 설정합니다. 초기 상태 확인은 `codex login status`로 수행합니다. `--ignore-user-config`는 일반 사용자 설정을 무시하는 옵션이지 인증을 삭제하는 옵션이 아닙니다.

read-only는 읽기 권한까지 없다는 뜻이 아닙니다. 시스템 관리 정책·미래의 새 도구까지 전부 차단한다고 보장하지 않습니다. 알려진 명령 실행·파일 변경·MCP·웹 검색 이벤트가 나타나면 결과를 거부합니다. 이 검사는 이미 실행된 도구의 부작용을 되돌리지 않습니다.

## Claude Code

- [CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Using the Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Claude Code with Pro or Max](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)

`claude -p --output-format json --json-schema ...`의 `structured_output` 또는 JSON `result`를 읽습니다. `subtype=success`이고 `is_error`가 거짓이어야 합니다. 빌트인 도구를 `--tools ""`로 비활성화하고, MCP는 `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` 및 `--disallowedTools 'mcp__*'`로 따로 차단합니다. `--safe-mode`, 빈 `--setting-sources`, `--no-session-persistence`, `--max-turns 2`를 사용합니다.

`claude auth status`의 `loggedIn=true`와 `authMethod="claude.ai"`를 요구합니다. Council은 API-key 인증이나 직접 추출한 OAuth 환경변수를 사용하지 않습니다. 조직의 관리 정책이 우선할 수 있으므로 시스템 관리 설정은 사용자가 확인해야 합니다.

문서에는 SDK/`claude -p` 사용량 정책 변경의 과거 안내가 남아 있지만, 2026-06-15 업데이트는 해당 변경이 일시 중지되었다고 명시합니다. 이를 무시하고 과거 안내만 현재 정책으로 인용하지 않습니다. 향후 정책은 달라질 수 있으며 extra usage 설정도 별도로 확인해야 합니다.

## Antigravity (Google) — opt-in

- [Headless mode](https://antigravity.google/docs/cli/headless/)
- [CLI reference](https://antigravity.google/docs/cli/reference/)
- [Installation and authentication](https://antigravity.google/docs/cli/install/)
- [Official CLI hands-on](https://codelabs.developers.google.com/antigravity-cli-hands-on)
- [Plans / credits](https://antigravity.google/pricing/)

`agy --input-format stream-json --output-format stream-json --json-schema schema.json`에 다음 한 줄을 stdin으로 전달하고 닫습니다. 이 모드에서는 `-p`를 함께 전달하지 않습니다.

```json
{"event":"user","message":{"content":"...prompt..."}}
```

최종 `event=result`의 `result.status=SUCCESS`와 `structured_output` 또는 JSON `response`를 읽습니다. 중간 `step_update`에 도구 실행이 보이면 결과를 거부하지만 이는 예방적 샌드박스가 아닙니다.

**기본 비활성화 이유:** headless 모드는 로컬 도구·권한을 상속하며, 작업 폴더 안의 파일 쓰기까지 자동 허용될 수 있습니다. 이 버전은 검증하지 않은 무도구 플래그를 임의로 만들어 전달하지 않습니다. 사용자가 로컬 설정과 연결 MCP를 감사한 후 `allow_inherited_tools=true`를 명시해야 합니다. API 키 환경변수는 전달하지 않으며, 인증·추가 크레딧 설정은 사용자가 직접 확인해야 합니다. 안정적인 무추론 계정 확인 계약은 이 어댑터에서 확정하지 못했으므로 인증 상태는 `unknown`입니다.

## Live smoke-test checklist

각 계정에서 공식 CLI로 직접 로그인해 주세요. 자동 결제/추가 크레딧을 검토하고 `subscription_confirmed=true`로 설정한 뒤 `doctor`를 실행해 주세요. 먼저 한 제공자의 `ask`가 성공하는지 확인하고, JSON 출처 ID·모델명·unknown 사용량 처리를 점검해 주세요. 다음으로 2인/1라운드 토론(7회)을 실행하고 transcript에서 position→critique→revision→synthesis 순서와 대상 수를 확인해 주세요.

CLI 플래그나 응답 형식이 바뀌면 `unsupported_cli` 또는 `protocol`로 실패하도록 설계했습니다. 검증을 건너뛰는 옵션으로 우회하지 말고 해당 어댑터와 fixture를 함께 수정해 주세요. 모델별 품질 평가는 같은 자료로 single-model/ensemble/debate를 비교하는 별도 실험이 필요합니다.

## 모델과 추론 수준 설정

TOML 제공자 설정은 선택적으로 `reasoning_effort`를 받습니다. Codex에는 `-c model_reasoning_effort="VALUE"`, Claude/Antigravity에는 `--effort VALUE`로 전달합니다. 문자열을 별도의 shell 명령으로 평가하지 않습니다. 설정을 생략하면 해당 CLI의 기본값을 사용합니다. 명시적 추론 수준 변경은 세션 identity 변경이므로 기존 세션 재개와 멱등성 재사용에 영향을 줍니다.

Web UI는 Codex app-server `model/list`의 `supportedReasoningEfforts`, Claude stream-json 초기화의 모델별 `supportedEffortLevels`, Antigravity `models` 및 `--help`를 읽습니다. 현재 계정과 설치 버전의 목록을 사용하며 공통 세 단계로 축약하지 않습니다. Antigravity는 모델별 추론 capability를 제공하지 않으므로 CLI 전체 옵션임을 표시합니다. 실제 적용 수준이 응답에 없으면 요청값만 표시하고 실적용을 추정하지 않습니다. 자세한 계약은 [Buzz/Web 문서](buzz.md)를 참고해 주세요.
