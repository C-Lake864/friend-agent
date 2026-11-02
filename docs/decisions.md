# 결정 기록

## 2026-11-02 -- Langfuse 관측 실습은 friend-agent 저장소에서 한다
선택: Langfuse 실습을 LLM 에이전트인 이 저장소(friend-agent)에서 한다. 로컬 실행은 uv 로 만든 Python 3.13 가상환경(.venv)과 claude-haiku-5-5 모델로 한다. 버린 대안: 다람쥐 정원(Squirrel-Garden)에 LLM 기능을 새로 넣고 계측, 개념만 정리.
이유: 다람쥐 정원에는 LLM 호출이 없어 트레이스에 남을 모델 호출, 토큰, 비용이 없다. 교안의 문제 상황(날씨 도구, 기억, 요금)이 이 저장소의 구조와 그대로 맞는다. 기본 모델(claude-opus-5-5)은 한 메시지에 3~4번 불리는 실습에 비용이 커서 haiku 로 낮췄다.
다시 볼 조건: 다람쥐 정원에 LLM 기능을 넣게 될 때, haiku 로는 도구 선택 품질이 실습에 쓰기 어려울 만큼 낮을 때.

## 2026-11-02 -- 트레이스에는 원문을 보내되 보내기 전에 패턴을 가린다
선택: 사용자 메시지, 모델 입력과 답, 기억 메모는 원문으로 Langfuse 에 보낸다. 다만 Langfuse SDK 의 마스킹으로 전화번호, 이메일, 카드 번호, API 키처럼 보이는 문자열은 보내기 전에 가린다. 서버 로그에 원문을 남기지 않는 기존 규칙은 그대로 둔다. 버린 대안: 원문 그대로 보내기, 원문을 빼고 길이와 개수만 보내기.
이유: 원문이 없으면 엉뚱한 답이 도구 실패 때문인지 모델이 지어낸 것인지 읽을 수 없다. Langfuse 는 접근할 수 있는 사람이 정해진 관측 도구이고, 흔한 개인정보 패턴만 가려도 노출 위험을 크게 줄인다.
다시 볼 조건: 서비스를 실제로 공개해 다른 사람의 대화가 쌓이기 시작할 때(보관 기간과 개인정보처리방침 문구를 함께 정한다), 가려지지 않은 개인정보가 트레이스에서 발견될 때.

## 2026-11-02 -- Langfuse 세션은 사용자 ID 와 한국 날짜로 나눈다
선택: session_id 를 "사용자 ID + 한국 날짜(YYYY-MM-DD)" 로 만들어 한 사람의 하루치 대화를 세션 하나로 본다. 버린 대안: 마지막 대화에서 6시간 넘게 쉬면 새 세션.
이유: 이 서비스에는 대화방이 따로 없고 대화가 계속 이어진다. 날짜로 나누면 저장소를 더 읽지 않아도 되고, "어제 저녁" 같은 제보를 날짜로 바로 찾을 수 있다.
다시 볼 조건: 자정 전후로 이어지는 대화를 한 묶음으로 봐야 할 때, 사용자 시간대가 한국 밖으로 넓어질 때.

## 2026-11-02 -- Langfuse 계측을 staging 에 올려 리비전별로 비교한다
선택: langfuse-tracing 브랜치를 push 하고 staging 대상 PR 로 머지해 friend-agent-staging 에 배포한다. Langfuse 비밀키는 사용자가 콘솔에서 Secret Manager 의 langfuse-secret-key 로 만들고, friend-agent-runtime 계정에는 이 시크릿 하나의 읽기 권한만 준다. 공개키와 접속 주소는 일반 환경변수로 넣는다. 배포는 기존 deploy.yml(소스 배포)을 그대로 쓴다. 버린 대안: 로컬에서 version 값을 바꿔 두 버전 흉내 내기, staging 건너뛰기.
이유: "업데이트한 뒤로 느려졌다"를 확인하려면 실제 리비전 이름(K_REVISION)이 다른 트레이스가 필요하다. 배포 계정, WIF, staging 환경 변수가 이미 준비되어 있다.
다시 볼 조건: production 에도 키를 넣을 때(트레이스는 environment=production 으로 따로 쌓인다), 교안 G 항목처럼 Actions 에서 이미지를 만드는 방식으로 바꿀 때.

## 2026-11-02 -- 배포 단계에 --clear-base-image 를 더한다
선택: deploy.yml 배포 단계 flags 에 --clear-base-image 를 더해, 서비스에 남은 "기본 이미지(base image)" 설정을 배포 때마다 지운다. 버린 대안: 두 서비스에서 gcloud 로 한 번만 지우기.
이유: friend-agent, friend-agent-staging 은 처음에 Dockerfile 없이 소스 배포(기본 이미지 사용)로 만들어져 build-base-image 설정이 남아 있다. 저장소에 Dockerfile 이 들어온 뒤 staging 배포가 "Base image is not supported for services built from Dockerfile" 로 멈췄고, production 에도 같은 설정이 남아 있어 main 머지 때 똑같이 멈춘다. 워크플로에 적어 두면 두 환경 모두 풀리고 이유가 코드에 남는다. 사용자가 추천안을 선택.
다시 볼 조건: 교안 G 항목처럼 Actions 에서 이미지를 만들어 image 로 배포하도록 바꿀 때(이 옵션이 필요 없어진다).

## 2026-11-02 -- 소스 배포 빌드는 friend-agent-builder 계정으로 한다
선택: friend-agent-builder 서비스 계정을 만들어 roles/run.builder 하나만 주고, 배포 계정(friend-agent-deployer)은 이 계정 하나에만 iam.serviceAccountUser 를 받는다. deploy.yml 배포 단계 flags 에 --build-service-account 로 저장소 변수 BUILD_SA 를 넘긴다. 버린 대안: squirrel-garden-builder 같이 쓰기, 기본 compute 계정을 대신 쓸 권한 주기.
이유: staging 배포가 "caller does not have permission to act as service account"(기본 compute 계정)로 멈췄다. 기본 compute 계정은 프로젝트 roles/editor 를 갖고 있어, 그 계정을 대신 쓸 권한을 주면 배포 계정이 사실상 편집자가 된다(AGENTS.md 의 넓은 역할 금지). 다람쥐 정원과 같은 방식이고, 앱마다 계정을 나눠 권한이 섞이지 않게 한다. 사용자가 추천안을 선택.
다시 볼 조건: Actions 에서 이미지를 만들어 image 로 배포하도록 바꿀 때(빌드 계정이 필요 없어진다).
