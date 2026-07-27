# Work IQ Outlook Mail MCP 설정

Work IQ Chat A2A의 `work_iq_preview`는 이메일 조회와 업무 컨텍스트 추론용입니다. 이 데모의 실제 메일 발송은 Foundry Toolbox에서 별도로 제공하는 **Work IQ > Outlook Mail** MCP 옵션을 사용합니다.

## 1. 테넌트 준비

메일을 발송할 Microsoft 365 사용자 계정에 다음이 필요합니다.

1. Microsoft 365 Copilot 라이선스
2. Microsoft 365 관리 센터의 **Agents > Tools > Registry**에서 **Work IQ Outlook Mail MCP Server**가 Available 상태
3. 현재 사용자가 Agent Identity Blueprint 앱 권한을 변경하고 사용자 consent를 부여할 수 있는 Entra 관리자 권한

Outlook Mail은 Microsoft 관리 OAuth를 사용합니다. client secret이나 별도 redirect URI를 만들지 않습니다. Live 전환 스크립트는 Microsoft first-party Agent Tools service principal을 확인하고, 배포된 agent blueprint에 `McpServers.Mail.All`을 선언한 뒤 현재 로그인 사용자에게만 delegated consent를 부여합니다.

## 2. 초기 Azure 배포

```powershell
azd up
```

이 단계는 Foundry 프로젝트, 모델, Hosted Agent, Blob Storage를 만들고 `MAIL_MODE=fake`로 agent를 배포합니다.

## 3. Outlook Mail Toolbox 생성

1. Microsoft Foundry에서 배포된 프로젝트의 **Build > Tools > Toolboxes**를 엽니다.
2. **Create toolbox**를 선택합니다.
3. Toolbox 이름을 정확히 `agent-tools`로 지정합니다.
4. **Add > Add tool > Catalog**에서 `Outlook Mail`을 검색합니다.
5. **Work IQ Mail** Remote MCP를 선택하고 **Create**를 선택합니다.
6. endpoint와 **Managed OAuth Identity Passthrough** 기본값을 변경하지 않고 **Connect**를 선택합니다.
7. Toolbox를 Publish합니다.

게시된 connection은 `WorkIQMail`, endpoint는 Microsoft catalog가 제공하는 `mcp_MailTools`를 사용합니다.

## 4. Live mail 활성화

```powershell
./scripts/enable-live-mail.ps1
```

스크립트는 `agent-tools` 존재 여부를 확인하고, 현재 로그인 사용자에게 필요한 Mail scope를 최소 범위로 부여하고, `MAIL_MODE=live`로 바꾼 뒤 `notify-agent`만 새 버전으로 배포합니다.

## 5. 검증

```powershell
./scripts/smoke-test.ps1 -IncludeSummarize
```

수신함에서 제목이 `[AI Glasses]`로 시작하는 메일을 확인합니다. 메일 발송이 실패하면 Hosted Agent 로그에서 다음을 확인합니다.

- Toolbox에 Outlook Mail 도구가 실제로 노출되는지
- Agent Tools service principal과 `McpServers.Mail.All` 사용자 consent가 준비됐는지
- agent identity와 사용자가 Foundry User 역할을 갖는지
- 사용자에게 Microsoft 365 Copilot 라이선스가 있는지
- Responses protocol이 `2.0.0`인지

### Conditional Access token 갱신

`TokenCreatedWithOutdatedPolicies` 또는 `Continuous access evaluation ... InteractionRequired`가 표시되면 Microsoft Graph Conditional Access 정책이 Azure CLI의 cached token 발급 이후 변경된 상태입니다. 현재 사용자 cache만 제거하고 Graph scope로 다시 로그인한 뒤 배포를 재실행합니다.

```powershell
$user = az account show --query user.name --output tsv
$tenant = azd env get-value AZURE_TENANT_ID
$subscription = azd env get-value AZURE_SUBSCRIPTION_ID

az logout --username $user
az login --tenant $tenant --scope https://graph.microsoft.com/.default
az account set --subscription $subscription
azd up
```

배포 hook은 Agent 배포 전에 Graph token을 검사하므로, 재인증이 필요하면 다섯 Agent를 배포하기 전에 위 명령을 안내하고 중단합니다.

참고: Work IQ와 Outlook Mail 도구는 preview이며 VNet-restricted Foundry 프로젝트를 지원하지 않습니다.