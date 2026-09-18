# Slide Localize — 한국어 빠른 시작

**웹:** https://ppt-slide-localize.fly.dev/  
**Skill:** `ppt-slide-localize` (`skills/ppt-slide-localize/`)

## 웹에서 쓰기

1. 사이트 우측 상단에서 **한국어** UI를 선택합니다 (브라우저가 한국어면 자동).
2. PDF / PPTX / 페이지 PNG ZIP을 업로드합니다.
3. **대상 언어**에서 `한국어` (또는 EN / 中文 / 日本語)를 고릅니다.
4. (선택) Advanced에서 Image 엔진 · LLM을 고릅니다. 기본 Cursor는 API 키가 필요 없습니다.
5. **작업 제출** → Jobs에서 진행률 확인 → 완료 후 **PDF 다운로드**.

### 참고

- 한 번에 **파일 1개 = Job 1개**. 여러 덱은 연속 제출하면 됩니다.
- 대략 **페이지당 ~1분** (모델·배치·Automation 부하에 따라 다름).
- 지원 출력 언어 **4종:** EN · 中文 · 한국어 · 日本語.
- 작업은 **이 브라우저 탭**에만 보이며, 탭을 닫으면 곧 삭제됩니다.
- Automation이 `resource_exhausted`면 잠시 후 다시 제출하면 됩니다.

## Cursor Skill

Agent에 `ppt-slide-localize` skill을 설치하면 웹과 같은 파이프라인(export → 분批 생성 → assemble)을 따릅니다. `job.json`의 `providers`로 Image/LLM을 전환할 수 있습니다.
