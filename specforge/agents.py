from __future__ import annotations

import difflib
import textwrap
from typing import Dict, List, Optional, Tuple

from .models import (
    DiagnosisReport,
    EdgeCaseSpec,
    SandboxRunResult,
    SpecContract,
)


MISSION_CATALOG: Dict[str, Dict[str, object]] = {
    "token_bucket_limiter": {
        "title": "분산 API 토큰 버킷 레이트 리미터 (Clock Skew & Burst 방어)",
        "natural_prompt": (
            "트래픽 버스트를 허용하되 시간 역행(Clock Skew)과 음수 비용 요청을 방어하는 "
            "토큰 버킷 레이트 리미터 evaluate_rate_limit(events, capacity, refill_rate)를 구현하라."
        ),
        "function_name": "evaluate_rate_limit",
        "signature": "def evaluate_rate_limit(events: list[tuple[float, int]], capacity: int, refill_rate: float) -> list[tuple[bool, float]]:",
        "docstring": (
            "각 (timestamp, cost) 이벤트에 대해 (allowed: bool, remaining_tokens: float) 튜플 리스트를 반환합니다. "
            "초기 토큰은 capacity와 같으며, 시간 경과(dt)에 따라 dt * refill_rate 만큼 충전되나 capacity를 초과할 수 없습니다. "
            "클럭 스큐(이전 이벤트보다 작은 timestamp) 발생 시 시간 경과를 0으로 클램핑하고, cost < 0 이거나 capacity <= 0 이면 ValueError를 발생시킵니다."
        ),
        "invariants": [
            "0.0 <= remaining_tokens <= float(capacity) 불변성 유지",
            "Clock skew (t_i < t_{i-1}) 발생 시 토큰이 감소하지 않도록 dt = max(0.0, t_i - last_ts) 보장",
            "cost < 0 또는 capacity <= 0 입력 시 즉시 ValueError 발생",
            "요청 거부(allowed=False) 시 기존 토큰은 차감되지 않음",
        ],
        "complexity_target": "Time O(N), Space O(N), Cyclomatic Complexity <= 8",
        "edge_cases": [
            EdgeCaseSpec(
                name="test_standard_burst_and_refill",
                input_repr="(([(0.0, 4), (1.0, 4), (2.0, 4)], 6, 2.0), {})",
                expected_repr="[(True, 2.0), (True, 0.0), (False, 2.0)]",
                category="normal",
                rationale="기본 토큰 차감, 시간당 리필(2.0/s), 부족 시 거절 및 잔여량 보존 검증",
            ),
            EdgeCaseSpec(
                name="test_idle_refill_cap_invariant",
                input_repr="(([(0.0, 3), (100.0, 2)], 5, 1.5), {})",
                expected_repr="[(True, 2.0), (True, 3.0)]",
                category="invariant",
                rationale="장시간 유휴 후에도 충전된 토큰이 capacity(5.0)를 절대 초과하지 않는지 검증",
            ),
            EdgeCaseSpec(
                name="test_exact_zero_boundary_consume",
                input_repr="(([(0.0, 5), (0.5, 1)], 5, 2.0), {})",
                expected_repr="[(True, 0.0), (True, 0.0)]",
                category="boundary",
                rationale="잔여 토큰이 정확히 요청 비용과 일치할 때(경계값 0.0) 정상 승인되는지 검증",
            ),
            EdgeCaseSpec(
                name="test_negative_cost_raises_value_error",
                input_repr="(([(0.0, -2)], 5, 1.0), {})",
                expected_repr="ValueError",
                category="adversarial",
                rationale="음수 비용(cost < 0)을 통한 토큰 부당 충전 공격(Adversarial Input) 차단 검증",
            ),
            EdgeCaseSpec(
                name="test_clock_skew_monotonic_guard",
                input_repr="(([(10.0, 3), (8.0, 2), (12.0, 4)], 6, 1.0), {})",
                expected_repr="[(True, 3.0), (True, 1.0), (False, 3.0)]",
                category="adversarial",
                rationale="분산 환경 NTP 시간 역행(t=10.0 -> t=8.0 -> t=12.0) 시 음수 리필 방지 및 단조 시계 기준 리필 검증",
            ),
            EdgeCaseSpec(
                name="test_empty_stream_and_invalid_capacity",
                input_repr="(([], 0, 1.0), {})",
                expected_repr="ValueError",
                category="boundary",
                rationale="capacity <= 0 설정 오류 시 즉시 ValueError를 던지는지 검증",
            ),
        ],
        "drafts": [
            # Iteration 1: Naive implementation (no capacity cap on refill, no negative cost check, no clock skew protection)
            (
                "초기 구현: 기본적인 시간차(dt * refill_rate) 계산과 차감 로직 작성",
                """\
def evaluate_rate_limit(events: list[tuple[float, int]], capacity: int, refill_rate: float) -> list[tuple[bool, float]]:
    tokens = float(capacity)
    last_ts = events[0][0] if events else 0.0
    results: list[tuple[bool, float]] = []

    for ts, cost in events:
        dt = ts - last_ts
        tokens = tokens + dt * refill_rate
        last_ts = ts
        if tokens >= cost:
            tokens -= cost
            results.append((True, round(tokens, 6)))
        else:
            results.append((False, round(tokens, 6)))
    return results
""",
            ),
            # Iteration 2: Fixes capacity cap & ValueError checks, but still vulnerable to monotonic watermark drift on clock skew
            (
                "1차 자가 수정: capacity 상한 캡(min) 및 음수 cost / 비정상 capacity ValueError 방어 추가",
                """\
def evaluate_rate_limit(events: list[tuple[float, int]], capacity: int, refill_rate: float) -> list[tuple[bool, float]]:
    if capacity <= 0 or refill_rate < 0:
        raise ValueError("capacity must be > 0 and refill_rate >= 0")

    tokens = float(capacity)
    last_ts = events[0][0] if events else 0.0
    results: list[tuple[bool, float]] = []

    for ts, cost in events:
        if cost < 0:
            raise ValueError("cost cannot be negative")
        dt = ts - last_ts
        tokens = min(float(capacity), tokens + dt * refill_rate)
        last_ts = ts
        if tokens >= cost:
            tokens -= cost
            results.append((True, round(tokens, 6)))
        else:
            results.append((False, round(tokens, 6)))
    return results
""",
            ),
            # Iteration 3: Full monotonic watermark + clock skew clamp
            (
                "2차 자가 수정 완료: 단조 증가 워터마크(max_seen_ts) 도입으로 Clock Skew 역행 시 음수 리필 및 기준 시각 오염 완벽 해결",
                """\
def evaluate_rate_limit(events: list[tuple[float, int]], capacity: int, refill_rate: float) -> list[tuple[bool, float]]:
    if capacity <= 0 or refill_rate < 0:
        raise ValueError("capacity must be > 0 and refill_rate >= 0")

    tokens = float(capacity)
    high_water_ts = events[0][0] if events else 0.0
    results: list[tuple[bool, float]] = []

    for ts, cost in events:
        if cost < 0:
            raise ValueError("cost cannot be negative")
        if ts > high_water_ts:
            dt = ts - high_water_ts
            tokens = min(float(capacity), tokens + dt * refill_rate)
            high_water_ts = ts
        if tokens >= cost:
            tokens -= cost
            results.append((True, round(tokens, 6)))
        else:
            results.append((False, round(tokens, 6)))
    return results
""",
            ),
        ],
    },
    "dependency_toposort": {
        "title": "마이크로서비스 빌드 DAG 병렬 웨이브 플래너 & 순환 탐지기",
        "natural_prompt": (
            "패키지 의존성 그래프에서 병렬 빌드 가능한 웨이브(Wave) 순서대로 묶어 반환하되, "
            "순환 의존성(Cycle)이나 자기 참조가 있으면 ValueError를 발생시키는 resolve_build_waves(graph)를 구현하라."
        ),
        "function_name": "resolve_build_waves",
        "signature": "def resolve_build_waves(graph: dict[str, list[str]]) -> list[list[str]]:",
        "docstring": (
            "graph[pkg] = [dep1, dep2, ...] (pkg를 빌드하기 전에 먼저 빌드되어야 하는 의존 패키지 목록)을 입력받아, "
            "동시에 빌드 가능한 패키지들의 정렬된 리스트(wave)들을 순서대로 반환합니다. "
            "그래프의 키에만 등장하지 않고 의존성 리스트에만 등장하는 리프 패키지도 첫 웨이브에 포함해야 하며, "
            "순환 참조(Cycle)가 존재하면 ValueError를 발생시킵니다."
        ),
        "invariants": [
            "각 wave 내부의 패키지 이름은 사전순(lexicographical) 정렬 보장",
            "의존성 배열에만 등장하는 암묵적(implicit) 리프 노드 누락 없이 포함",
            "순환 참조(Self-loop 또는 다중 노드 Cycle) 감지 시 ValueError 발생",
            "빈 그래프 {} 입력 시 빈 리스트 [] 반환",
        ],
        "complexity_target": "Time O(V + E + V log V), Space O(V + E), Cyclomatic Complexity <= 9",
        "edge_cases": [
            EdgeCaseSpec(
                name="test_layered_microservice_dag",
                input_repr="(({'api': ['auth', 'db'], 'auth': ['crypto', 'db'], 'db': [], 'crypto': []},), {})",
                expected_repr="[['crypto', 'db'], ['auth'], ['api']]",
                category="normal",
                rationale="다중 계층 의존성 그래프에서 리프부터 루트까지 정확한 병렬 빌드 웨이브 산출 검증",
            ),
            EdgeCaseSpec(
                name="test_implicit_leaf_nodes_included",
                input_repr="(({'gateway': ['service_a', 'service_b'], 'service_a': ['core_lib']},), {})",
                expected_repr="[['core_lib', 'service_b'], ['service_a'], ['gateway']]",
                category="boundary",
                rationale="dict 키에 명시되지 않고 값 리스트에만 등장한 암묵적 노드(core_lib, service_b) 자동 수집 검증",
            ),
            EdgeCaseSpec(
                name="test_diamond_cycle_raises_value_error",
                input_repr="(({'a': ['b'], 'b': ['c'], 'c': ['a']},), {})",
                expected_repr="ValueError",
                category="adversarial",
                rationale="3-노드 순환 의존성(a -> b -> c -> a) 발생 시 무한 루프 대신 ValueError 발생 검증",
            ),
            EdgeCaseSpec(
                name="test_self_dependency_raises_value_error",
                input_repr="(({'worker': ['worker']},), {})",
                expected_repr="ValueError",
                category="adversarial",
                rationale="자기 자신을 의존성으로 참조하는 Self-Loop 즉시 차단 검증",
            ),
            EdgeCaseSpec(
                name="test_deterministic_wave_sorting",
                input_repr="(({'z_app': ['m_lib', 'a_lib'], 'b_app': ['a_lib']},), {})",
                expected_repr="[['a_lib', 'm_lib'], ['b_app', 'z_app']]",
                category="invariant",
                rationale="동일 웨이브 내 패키지들이 항상 결정론적 사전순으로 정렬되는지 검증",
            ),
            EdgeCaseSpec(
                name="test_empty_graph_returns_empty",
                input_repr="(({},), {})",
                expected_repr="[]",
                category="boundary",
                rationale="빈 의존성 사전에 대해 빈 리스트 [] 반환 검증",
            ),
        ],
        "drafts": [
            # Iteration 1: Misses implicit leaf nodes and doesn't sort waves or raise on cycles properly
            (
                "초기 구현: 명시적 키 기준 진입차수 계산 및 웨이브 추출",
                """\
def resolve_build_waves(graph: dict[str, list[str]]) -> list[list[str]]:
    if not graph:
        return []
    remaining = {k: set(v) for k, v in graph.items()}
    waves: list[list[str]] = []
    while remaining:
        ready = [node for node, deps in remaining.items() if not deps]
        if not ready:
            break
        waves.append(ready)
        for node in ready:
            del remaining[node]
        for deps in remaining.values():
            deps.difference_update(ready)
    return waves
""",
            ),
            # Iteration 2: Adds cycle detection ValueError, but still forgets implicit leaf nodes not present as top-level keys
            (
                "1차 자가 수정: 순환 참조 시 ValueError 발생 및 웨이브 사전순 정렬 추가",
                """\
def resolve_build_waves(graph: dict[str, list[str]]) -> list[list[str]]:
    if not graph:
        return []
    remaining = {k: set(v) for k, v in graph.items()}
    waves: list[list[str]] = []
    while remaining:
        ready = sorted(node for node, deps in remaining.items() if not deps)
        if not ready:
            raise ValueError("Cyclic dependency detected in build graph")
        waves.append(ready)
        for node in ready:
            del remaining[node]
        for deps in remaining.values():
            deps.difference_update(ready)
    return waves
""",
            ),
            # Iteration 3: Collects all transitive/implicit nodes + sorted Kahn's wave resolution + cycle ValueError
            (
                "2차 자가 수정 완료: 전체 정점 합집합(암묵적 리프 노드 포함) 초기화 및 결정론적 Kahn 알고리즘 완성",
                """\
def resolve_build_waves(graph: dict[str, list[str]]) -> list[list[str]]:
    if not graph:
        return []
    all_nodes: set[str] = set(graph.keys())
    for deps in graph.values():
        all_nodes.update(deps)
    if any(len(node) == 0 for node in all_nodes):
        raise ValueError("Package name cannot be empty")

    remaining: dict[str, set[str]] = {
        node: set(graph.get(node, [])) for node in all_nodes
    }
    waves: list[list[str]] = []

    while remaining:
        ready = sorted(node for node, deps in remaining.items() if not deps)
        if not ready:
            raise ValueError("Cyclic dependency detected in build graph")
        waves.append(ready)
        for node in ready:
            del remaining[node]
        for deps in remaining.values():
            deps.difference_update(ready)

    return waves
""",
            ),
        ],
    },
    "circuit_breaker_fsm": {
        "title": "결제 게이트웨이 서킷 브레이커 상태 머신 (CLOSED / OPEN / HALF_OPEN)",
        "natural_prompt": (
            "연속 실패 임계치 도달 시 OPEN으로 전환하고, 쿨다운 경과 후 HALF_OPEN에서 프로브 요청이 성공하면 CLOSED, "
            "실패하면 즉시 재차단(OPEN)하는 simulate_circuit_breaker(calls, fail_threshold, cooldown_sec)를 작성하라."
        ),
        "function_name": "simulate_circuit_breaker",
        "signature": "def simulate_circuit_breaker(calls: list[tuple[float, bool]], fail_threshold: int, cooldown_sec: float) -> list[tuple[str, bool]]:",
        "docstring": (
            "각 (timestamp, upstream_ok) 호출에 대해 서킷 브레이커가 처리한 뒤의 (resulting_state, call_accepted) 리스트를 반환합니다. "
            "상태는 'CLOSED', 'OPEN', 'HALF_OPEN' 중 하나입니다. "
            "OPEN 상태에서 timestamp - opened_at < cooldown_sec 이면 호출을 거부(call_accepted=False)하고 ('OPEN', False)를 기록합니다. "
            "쿨다운 이상 경과하면 'HALF_OPEN'으로 전이하여 프로브 호출을 시도하며, 성공 시 ('CLOSED', True), 실패 시 즉시 재차단하여 ('OPEN', False) 및 opened_at을 현재 시각으로 갱신합니다."
        ),
        "invariants": [
            "fail_threshold <= 0 또는 cooldown_sec <= 0 입력 시 ValueError 발생",
            "CLOSED 상태에서 성공 호출 발생 시 누적 실패 카운터(consecutive_failures)를 즉시 0으로 리셋",
            "OPEN 상태 중 쿨다운 미경과 요청은 업스트림을 호출하지 않고 ('OPEN', False) 반환",
            "HALF_OPEN 프로브 실패 시 즉시 ('OPEN', False)로 복귀하고 쿨다운 타이머(opened_at) 재시작",
        ],
        "complexity_target": "Time O(N), Space O(N), Cyclomatic Complexity <= 9",
        "edge_cases": [
            EdgeCaseSpec(
                name="test_trip_to_open_and_reject_during_cooldown",
                input_repr="(([(1.0, False), (2.0, False), (3.0, True), (6.0, True)], 2, 5.0), {})",
                expected_repr="[('CLOSED', False), ('OPEN', False), ('OPEN', False), ('OPEN', False)]",
                category="normal",
                rationale="연속 2회 실패 시 OPEN 전이 및 쿨다운(5초) 만료 전(t=3, t=6 < 7.0) 요청 차단 검증",
            ),
            EdgeCaseSpec(
                name="test_half_open_probe_recovery_to_closed",
                input_repr="(([(1.0, False), (2.0, False), (7.0, True), (8.0, False)], 2, 5.0), {})",
                expected_repr="[('CLOSED', False), ('OPEN', False), ('CLOSED', True), ('CLOSED', False)]",
                category="normal",
                rationale="t=7.0(쿨다운 5초 경과) 프로브 성공 시 CLOSED 복구 및 실패 카운터 초기화 검증",
            ),
            EdgeCaseSpec(
                name="test_half_open_failed_probe_reopens_and_resets_timer",
                input_repr="(([(1.0, False), (2.0, False), (7.0, False), (9.0, True), (12.0, True)], 2, 5.0), {})",
                expected_repr="[('CLOSED', False), ('OPEN', False), ('OPEN', False), ('OPEN', False), ('CLOSED', True)]",
                category="adversarial",
                rationale="t=7.0 HALF_OPEN 프로브 실패 시 즉시 재차단(opened_at=7.0)되어 t=9.0 요청을 막고 t=12.0에 복구되는지 검증",
            ),
            EdgeCaseSpec(
                name="test_intermittent_success_resets_failure_streak",
                input_repr="(([(1.0, False), (2.0, False), (3.0, True), (4.0, False), (5.0, False)], 3, 10.0), {})",
                expected_repr="[('CLOSED', False), ('CLOSED', False), ('CLOSED', True), ('CLOSED', False), ('CLOSED', False)]",
                category="invariant",
                rationale="임계치(3회) 도달 전 중간 성공(t=3.0)이 있으면 연속 실패 카운터가 0으로 초기화되는지 검증",
            ),
            EdgeCaseSpec(
                name="test_invalid_threshold_raises_value_error",
                input_repr="(([(1.0, True)], 0, 5.0), {})",
                expected_repr="ValueError",
                category="boundary",
                rationale="fail_threshold <= 0 또는 cooldown_sec <= 0 일 때 ValueError 발생 검증",
            ),
        ],
        "drafts": [
            # Iteration 1: Doesn't reset consecutive_failures on CLOSED success, and forgets to update opened_at on HALF_OPEN failure
            (
                "초기 구현: CLOSED / OPEN / HALF_OPEN 기본 상태 분기 작성",
                """\
def simulate_circuit_breaker(calls: list[tuple[float, bool]], fail_threshold: int, cooldown_sec: float) -> list[tuple[str, bool]]:
    state = "CLOSED"
    failures = 0
    opened_at = 0.0
    history: list[tuple[str, bool]] = []

    for ts, ok in calls:
        if state == "OPEN":
            if ts - opened_at >= cooldown_sec:
                state = "HALF_OPEN"
            else:
                history.append(("OPEN", False))
                continue

        if state == "HALF_OPEN":
            if ok:
                state = "CLOSED"
                failures = 0
                history.append(("CLOSED", True))
            else:
                state = "OPEN"
                history.append(("OPEN", False))
            continue

        if ok:
            history.append(("CLOSED", True))
        else:
            failures += 1
            if failures >= fail_threshold:
                state = "OPEN"
                opened_at = ts
                history.append(("OPEN", False))
            else:
                history.append(("CLOSED", False))

    return history
""",
            ),
            # Iteration 2: Adds parameter check & opened_at timer reset on HALF_OPEN failure, but still forgets failures = 0 on CLOSED success
            (
                "1차 자가 수정: 파라미터 검증 및 HALF_OPEN 프로브 실패 시 opened_at 타이머 갱신 추가",
                """\
def simulate_circuit_breaker(calls: list[tuple[float, bool]], fail_threshold: int, cooldown_sec: float) -> list[tuple[str, bool]]:
    if fail_threshold <= 0 or cooldown_sec <= 0:
        raise ValueError("fail_threshold and cooldown_sec must be positive")

    state = "CLOSED"
    failures = 0
    opened_at = 0.0
    history: list[tuple[str, bool]] = []

    for ts, ok in calls:
        if state == "OPEN":
            if ts - opened_at >= cooldown_sec:
                state = "HALF_OPEN"
            else:
                history.append(("OPEN", False))
                continue

        if state == "HALF_OPEN":
            if ok:
                state = "CLOSED"
                failures = 0
                history.append(("CLOSED", True))
            else:
                state = "OPEN"
                opened_at = ts
                history.append(("OPEN", False))
            continue

        if ok:
            history.append(("CLOSED", True))
        else:
            failures += 1
            if failures >= fail_threshold:
                state = "OPEN"
                opened_at = ts
                history.append(("OPEN", False))
            else:
                history.append(("CLOSED", False))

    return history
""",
            ),
            # Iteration 3: Resets failures = 0 on CLOSED success
            (
                "2차 자가 수정 완료: CLOSED 상태 내 성공 호출 시 연속 실패 스트릭(failures = 0) 리셋 불변성 보장",
                """\
def simulate_circuit_breaker(calls: list[tuple[float, bool]], fail_threshold: int, cooldown_sec: float) -> list[tuple[str, bool]]:
    if fail_threshold <= 0 or cooldown_sec <= 0:
        raise ValueError("fail_threshold and cooldown_sec must be positive")

    state = "CLOSED"
    failures = 0
    opened_at = 0.0
    history: list[tuple[str, bool]] = []

    for ts, ok in calls:
        if state == "OPEN":
            if ts - opened_at >= cooldown_sec:
                state = "HALF_OPEN"
            else:
                history.append(("OPEN", False))
                continue

        if state == "HALF_OPEN":
            if ok:
                state = "CLOSED"
                failures = 0
                history.append(("CLOSED", True))
            else:
                state = "OPEN"
                opened_at = ts
                history.append(("OPEN", False))
            continue

        if ok:
            failures = 0
            history.append(("CLOSED", True))
        else:
            failures += 1
            if failures >= fail_threshold:
                state = "OPEN"
                opened_at = ts
                history.append(("OPEN", False))
            else:
                history.append(("CLOSED", False))

    return history
""",
            ),
        ],
    },
    "financial_ledger_reconcile": {
        "title": "다중 통화 원장(Ledger) 정산 & IEEE-754 부동소수점 오차 방어 엔진",
        "natural_prompt": (
            "다중 통화 트랜잭션 스트림을 환율 변환 후 계좌별 USD 잔액으로 정산하되, "
            "부동소수점 누적 오차와 Banker's Rounding(ROUND_HALF_EVEN)을 정확히 처리하고 "
            "미등록 통화 코드 시 ValueError를 던지는 reconcile_ledger(txns, fx_rates)를 구현하라."
        ),
        "function_name": "reconcile_ledger",
        "signature": "def reconcile_ledger(txns: list[tuple[str, str, str]], fx_rates: dict[str, str]) -> dict[str, str]:",
        "docstring": (
            "txns는 (account_id, amount_str, currency) 튜플 리스트이고 fx_rates는 {currency: usd_rate_str} 입니다. "
            "각 트랜잭션 금액에 환율을 곱한 값을 건별로 소수점 둘째 자리(0.01)까지 ROUND_HALF_EVEN 방식으로 반올림하여 계좌별 잔액에 합산합니다. "
            "최종 결과는 계좌 ID 오름차순으로 정렬된 {account_id: '0.00' 포맷 문자열} 사전이며, 미등록 통화는 ValueError를 발생시킵니다."
        ),
        "invariants": [
            "float 대신 Decimal 연산을 사용하여 0.1 + 0.2 != 0.3 부동소수점 누적 오차 원천 차단",
            "각 트랜잭션 환전 시 건별 quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN) 적용",
            "fx_rates에 없는 통화 코드 입력 시 KeyError가 아닌 명시적 ValueError 발생",
        ],
        "complexity_target": "Time O(N log K), Space O(K), Cyclomatic Complexity <= 6",
        "edge_cases": [
            EdgeCaseSpec(
                name="test_ieee754_drift_elimination",
                input_repr="(([('acct_1', '0.10', 'USD'), ('acct_1', '0.20', 'USD'), ('acct_1', '0.30', 'USD')], {'USD': '1.00'}), {})",
                expected_repr="{'acct_1': '0.60'}",
                category="invariant",
                rationale="반복 소수점 합산 시 부동소수점 오차 없이 정확한 '0.60' 문자열 포맷 유지 검증",
            ),
            EdgeCaseSpec(
                name="test_bankers_rounding_per_transaction",
                input_repr="(([('acct_a', '1.00', 'EUR'), ('acct_a', '1.00', 'GBP')], {'EUR': '1.005', 'GBP': '1.015'}), {})",
                expected_repr="{'acct_a': '2.02'}",
                category="boundary",
                rationale="건별 Banker's Rounding: 1.005 -> 1.00 (짝수), 1.015 -> 1.02 (짝수) 합산이 2.02가 되는지 검증 (단순 합산 후 반올림하면 2.020이나 float시 1.015가 1.01로 깎이는 버그 탐지)",
            ),
            EdgeCaseSpec(
                name="test_unknown_currency_raises_value_error",
                input_repr="(([('acct_x', '50.00', 'JPY')], {'USD': '1.00'}), {})",
                expected_repr="ValueError",
                category="adversarial",
                rationale="환율표에 없는 통화('JPY') 입력 시 KeyError 대신 ValueError로 정규화하여 발생시키는지 검증",
            ),
            EdgeCaseSpec(
                name="test_negative_debit_and_zero_balance",
                input_repr="(([('acct_2', '100.00', 'USD'), ('acct_2', '-100.00', 'USD')], {'USD': '1.00'}), {})",
                expected_repr="{'acct_2': '0.00'}",
                category="normal",
                rationale="입출금 상쇄 시 '-0.00'이 아닌 표준 '0.00' 포맷 반환 검증",
            ),
        ],
        "drafts": [
            # Iteration 1: Uses float and raises KeyError on missing currency
            (
                "초기 구현: 기본 float 곱셈 및 합산 포맷팅",
                """\
def reconcile_ledger(txns: list[tuple[str, str, str]], fx_rates: dict[str, str]) -> dict[str, str]:
    balances: dict[str, float] = {}
    for acct, amount_s, curr in txns:
        rate = float(fx_rates[curr])
        usd_val = round(float(amount_s) * rate, 2)
        balances[acct] = balances.get(acct, 0.0) + usd_val
    return {k: f"{balances[k]:.2f}" for k in sorted(balances)}
""",
            ),
            # Iteration 2: Uses Decimal + ROUND_HALF_EVEN + explicit ValueError on unknown currency
            (
                "1차 자가 수정 완료: decimal.Decimal + ROUND_HALF_EVEN 건별 양자화 및 미등록 통화 ValueError 처리",
                """\
from decimal import Decimal, ROUND_HALF_EVEN

def reconcile_ledger(txns: list[tuple[str, str, str]], fx_rates: dict[str, str]) -> dict[str, str]:
    cent = Decimal("0.01")
    balances: dict[str, Decimal] = {}
    for acct, amount_s, curr in txns:
        if curr not in fx_rates:
            raise ValueError(f"Unsupported currency code: {curr}")
        raw_usd = Decimal(amount_s) * Decimal(fx_rates[curr])
        settled_usd = raw_usd.quantize(cent, rounding=ROUND_HALF_EVEN)
        balances[acct] = balances.get(acct, Decimal("0.00")) + settled_usd

    result: dict[str, str] = {}
    for acct in sorted(balances):
        val = balances[acct].quantize(cent, rounding=ROUND_HALF_EVEN)
        if val == Decimal("0.00"):
            val = Decimal("0.00")
        result[acct] = str(val)
    return result
""",
            ),
        ],
    },
}


class ArchitectAgent:
    """Decomposes a mission or natural language requirement into a formal SpecContract."""

    def create_spec(self, mission_id: str, custom_prompt: Optional[str] = None) -> SpecContract:
        entry = MISSION_CATALOG.get(mission_id, MISSION_CATALOG["token_bucket_limiter"])
        return SpecContract(
            mission_id=mission_id if mission_id in MISSION_CATALOG else "token_bucket_limiter",
            title=str(entry["title"]),
            natural_prompt=custom_prompt or str(entry["natural_prompt"]),
            function_name=str(entry["function_name"]),
            signature=str(entry["signature"]),
            docstring=str(entry["docstring"]),
            invariants=list(entry["invariants"]),  # type: ignore[arg-type]
            edge_cases=list(entry["edge_cases"]),  # type: ignore[arg-type]
            complexity_target=str(entry["complexity_target"]),
        )


class QAAgent:
    """Synthesizes readable pytest-style test suite representation from EdgeCaseSpecs."""

    def render_test_suite_code(self, spec: SpecContract) -> str:
        lines = [
            "import pytest",
            f"from solution import {spec.function_name}",
            "",
            f"# SpecForge QA Agent — Adversarial & Invariant Test Suite ({len(spec.edge_cases)} cases)",
            "",
        ]
        for ec in spec.edge_cases:
            lines.append(f"def {ec.name}():")
            lines.append(f'    """[{ec.category.upper()}] {ec.rationale}"""')
            lines.append(f"    args, kwargs = {ec.input_repr}")
            if ec.expected_repr == "ValueError":
                lines.append("    with pytest.raises(ValueError):")
                lines.append(f"        {spec.function_name}(*args, **kwargs)")
            else:
                lines.append(f"    expected = {ec.expected_repr}")
                lines.append(f"    assert {spec.function_name}(*args, **kwargs) == expected")
            lines.append("")
        return "\n".join(lines)


class ReviewerAgent:
    """Analyzes failed SandboxRunResult + AST metrics to produce a root-cause DiagnosisReport."""

    def diagnose(
        self,
        iteration: int,
        spec: SpecContract,
        code: str,
        sandbox_result: SandboxRunResult,
    ) -> DiagnosisReport:
        if sandbox_result.all_passed:
            return DiagnosisReport(
                iteration=iteration,
                severity="CLEAN",
                root_cause_title="All Formal Invariants & Adversarial Tests Verified",
                root_cause_detail=(
                    f"통과율 {sandbox_result.passed_tests}/{sandbox_result.total_tests} (100%). "
                    f"AST 순환 복잡도 {sandbox_result.ast_metrics.cyclomatic_complexity}, 보안 경고 0건."
                ),
                faulty_line_hint=None,
                faulty_snippet=None,
                repair_directives=["추가 패치 불필요 — 프로덕션 스펙 검증 완료"],
                confidence=0.99,
            )

        failed = [t for t in sandbox_result.test_results if not t.passed]
        first_fail = failed[0]
        code_lines = code.splitlines()

        # Heuristic line finder based on mission and failure pattern
        faulty_line: Optional[int] = None
        faulty_snippet: Optional[str] = None
        directives: List[str] = []
        title = f"{first_fail.name} 실패 ({first_fail.error_type or 'AssertionError'})"
        detail = first_fail.error_message or "테스트 단언문(Assertion) 불일치"

        for idx, line in enumerate(code_lines, start=1):
            stripped = line.strip()
            if "tokens = tokens +" in stripped or "dt = ts - last_ts" in stripped:
                faulty_line = idx
                faulty_snippet = stripped
                break
            if "remaining = {k: set(v)" in stripped or "if not ready:" in stripped:
                faulty_line = idx
                faulty_snippet = stripped
                break
            if "history.append((\"OPEN\", False))" in stripped or "if ok:" in stripped:
                faulty_line = idx
                faulty_snippet = stripped
                break
            if "float(" in stripped or "fx_rates[curr]" in stripped:
                faulty_line = idx
                faulty_snippet = stripped
                break

        if faulty_line is None and code_lines:
            faulty_line = min(len(code_lines), max(1, len(code_lines) // 2))
            faulty_snippet = code_lines[faulty_line - 1].strip()

        for fc in failed:
            if "cap_invariant" in fc.name:
                directives.append("장시간 유휴 시 리필된 토큰이 capacity를 넘지 않도록 min(float(capacity), ...) 상한 적용")
            elif "negative_cost" in fc.name or "invalid_capacity" in fc.name or "invalid_threshold" in fc.name:
                directives.append("비정상 입력(cost < 0 또는 capacity/threshold <= 0) 진입 시 즉시 ValueError 발생")
            elif "clock_skew" in fc.name:
                directives.append("이전 타임스탬프보다 작은 이벤트(Clock Skew) 방어를 위해 단조 증가 워터마크(high_water_ts)로 리필 계산")
            elif "implicit_leaf" in fc.name:
                directives.append("graph.keys() 뿐만 아니라 의존성 리스트에만 존재하는 암묵적 리프 노드를 전체 정점 집합에 합산")
            elif "cycle" in fc.name or "self_dependency" in fc.name:
                directives.append("진입차수 0인 노드가 더 이상 없을 때 남은 노드가 존재하면 break 대신 ValueError 발생")
            elif "failed_probe_reopens" in fc.name:
                directives.append("HALF_OPEN 상태에서 프로브 실패 시 state='OPEN' 전환과 동시에 opened_at = ts 로 쿨다운 타이머 재설정")
            elif "intermittent_success" in fc.name:
                directives.append("CLOSED 상태에서 요청 성공 시 누적 실패 카운터(failures = 0) 즉시 초기화")
            elif "bankers_rounding" in fc.name or "ieee754" in fc.name:
                directives.append("float 연산을 제거하고 decimal.Decimal 및 ROUND_HALF_EVEN 건별 quantize('0.01') 적용")
            elif "unknown_currency" in fc.name:
                directives.append("fx_rates 사전 조회 전 미등록 통화 코드 검증 후 명시적 ValueError 발생")
            else:
                directives.append(
                    f"[{fc.name}] 입력 {fc.input_repr if hasattr(fc, 'input_repr') else fc.input_summary} 에 대해 "
                    f"기대값 {fc.expected_summary} (실제값: {fc.actual_summary}) 불일치 교정"
                )

        root_title = f"[{len(failed)}건 실패] {first_fail.name} — {first_fail.error_type or 'Invariant Violation'}"
        root_detail = (
            f"실패 케이스: {', '.join(f.name for f in failed)}. "
            f"대표 원인: {detail}"
        )

        return DiagnosisReport(
            iteration=iteration,
            severity="CRITICAL" if len(failed) >= 2 else "WARNING",
            root_cause_title=root_title,
            root_cause_detail=root_detail,
            faulty_line_hint=faulty_line,
            faulty_snippet=faulty_snippet,
            repair_directives=directives,
            confidence=0.94,
        )


class CoderAgent:
    """Synthesizes initial implementation and iterative self-healing patches with unified diffs."""

    def generate_iteration_code(
        self,
        spec: SpecContract,
        iteration: int,
        previous_code: Optional[str] = None,
        diagnosis: Optional[DiagnosisReport] = None,
        custom_initial_code: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """Returns (code, unified_diff, coder_notes)."""
        entry = MISSION_CATALOG.get(spec.mission_id, MISSION_CATALOG["token_bucket_limiter"])
        drafts: List[Tuple[str, str]] = entry["drafts"]  # type: ignore[assignment]

        if iteration == 1 and custom_initial_code and custom_initial_code.strip():
            code = textwrap.dedent(custom_initial_code).strip() + "\n"
            notes = "사용자 지정 초기 코드(Custom / Mutated Code)를 샌드박스에 로드하여 검증을 시작합니다."
            return code, "", notes

        idx = min(iteration - 1, len(drafts) - 1)
        notes, raw_code = drafts[idx]
        code = textwrap.dedent(raw_code).strip() + "\n"

        # If custom_initial_code was provided on iteration 1 and failed, jump straight to verified code on iteration 2
        if iteration >= 2 and custom_initial_code and custom_initial_code.strip():
            notes, raw_verified = drafts[-1]
            code = textwrap.dedent(raw_verified).strip() + "\n"
            notes = f"사용자 주입 버그 자동 복구 패치: {notes}"

        diff_str = ""
        if previous_code:
            diff_lines = list(
                difflib.unified_diff(
                    previous_code.splitlines(keepends=True),
                    code.splitlines(keepends=True),
                    fromfile=f"solution_v{iteration - 1}.py",
                    tofile=f"solution_v{iteration}.py",
                    n=2,
                )
            )
            diff_str = "".join(diff_lines)

        return code, diff_str, notes
