"""테스트는 실제 AI를 부르지 않는다. 가짜 응답(fake)과 서버 메모리 저장소로 실행한다.

실제 AI 호출은 비용이 들고, 느리고, 같은 질문에도 답이 달라서 통과와 실패를 판정할 수 없다.
Langfuse 키도 비워 둔다. .env 에 키가 있어도 테스트 기록이 Langfuse 프로젝트에 쌓이지 않게 한다.
"""
import os

os.environ.update({"LLM_PROVIDER": "fake", "LLM_API_KEY": "", "STORAGE": "memory", "AUTH": "none",
                   "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""})
os.environ.setdefault("ENV", "test")
