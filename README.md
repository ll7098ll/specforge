# SpecForge (`specforge`)

**Autonomous 7-Role Multi-Agent Python Software Factory — Subprocess Self-Healing, AST Mutation Testing & Empirical Big-O Profiler**

`SpecForge`는 자연어 요구사항을 형식 명세(`SpecContract`)로 분해하고, 적대적 테스트 스위트(`Adversarial Pytest`)를 생성한 뒤, **실제 격리된 Python 서브프로세스 샌드박스**에서 다음 3단계를 자율적으로 수행하는 멀티 에이전트 소프트웨어 팩토리입니다.

1. **Phase 1 — Self-Healing Code Synthesis**: 테스트가 100% 통과할 때까지 `ReviewerAgent`가 Traceback/AST를 진단하고 `CoderAgent`가 `Unified Diff` 패치를 반복 생성합니다.
2. **Phase 2 — AST Mutation Testing & QA Self-Hardening**: 검증된 코드의 Python AST를 `ast.NodeTransformer`로 강제 변이(Mutants)시켜 기존 테스트 스위트의 사각지대(`SURVIVED` 돌연변이)를 찾아내고, `QAAgent`가 **Killer Test**를 자동 합성해 Mutation Score 100%를 달성합니다.
3. **Phase 3 — Empirical Big-O & Memory Profiling (`tracemalloc`)**: 입력 규모 $N \in [100, 200, 400, 800]$ 스케일링 벤치마크를 실행해 로그-로그 회귀 기울기($\alpha$)로 $O(N)$ vs $O(N^2)$ 시간 복잡도와 피크 힙 메모리(KiB)를 실측 검증합니다.

---

## 1. 아키텍처 & 에이전트 파이프라인

```
[1. ARCHITECT] ──(SpecContract & Invariants)──▶ [2. QA AGENT]
      │                                               │
      ▼                                      (Adversarial Suite)
[3. CODER AGENT] ───(Candidate Code / Diff)───▶ [4. SANDBOX]
      ▲                                               │
      │                                     (AST + Subprocess Run)
      └────(Repair Directives & Root Cause)─── [5. REVIEWER]
                                                      │
                                              (100% Pass Verified)
                                                      ▼
[2. QA AGENT] ◀──(Survived Mutants)── [6. AST MUTATOR] ──▶ [7. BIG-O PROFILER]
 (Killer Tests)                                             (tracemalloc & α)
```

---

## 2. 내장 엔지니어링 미션 (4종)

| Mission ID | 주제 | Self-Healing | AST Mutation Hardening | Big-O Speedup ($N=800$) |
| :--- | :--- | :---: | :---: | :---: |
| `token_bucket_limiter` | 분산 API 토큰 버킷 레이트 리미터 | 3 Iters | `75.0% → 100.0%` (8 Mutants) | `O(N)` (`3.2x` vs Naive $O(N^2)$) |
| `dependency_toposort` | 마이크로서비스 빌드 DAG 웨이브 플래너 | 3 Iters | `80.0% → 100.0%` (5 Mutants) | `O(V + E log V)` (`14.6x` vs $O(V^2)$) |
| `circuit_breaker_fsm` | 결제 게이트웨이 서킷 브레이커 FSM | 3 Iters | `87.5% → 100.0%` (8 Mutants) | `O(N)` (`5.8x` vs Naive $O(N^2)$) |
| `financial_ledger_reconcile` | 다중 통화 원장(Ledger) 정밀 정산 | 2 Iters | `50.0% → 100.0%` (4 Mutants) | `O(N + K log K)` (`19.4x` vs $O(K\cdot N)$) |

---

## 3. 설치 및 실행 방법

```bash
# 1. 전체 유닛·통합 테스트 실행 (Self-Healing + AST Mutation + Big-O Profiler)
python3 -m unittest discover -s tests -v

# 2. 터미널 TUI로 단일 미션 3-Phase 파이프라인 실행
python3 -m specforge.cli --mission token_bucket_limiter

# 3. 4개 미션 전체 연속 실행
python3 -m specforge.cli --all

# 4. 로컬 API 및 웹 워크벤치 서버 실행 (http://127.0.0.1:8765)
python3 -m specforge.server
```
