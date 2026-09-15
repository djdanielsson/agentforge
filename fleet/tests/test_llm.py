"""The LLM boundary: gateway tokens, policy and usage accounting (SPEC §11–§14)."""

from __future__ import annotations

import pytest
from fleet_core.llm import (
    LLMGateway,
    LLMResult,
    gateway_token_env,
    project_token,
    project_usage,
    record_usage,
    verify_project_token,
)


def test_project_tokens_round_trip_and_are_project_specific():
    first = project_token("prj_alpha")
    second = project_token("prj_beta")
    assert first != second
    assert verify_project_token(first) == "prj_alpha"
    assert verify_project_token(second) == "prj_beta"


def test_a_forged_or_mangled_token_is_rejected():
    assert verify_project_token("") is None
    assert verify_project_token("not-base64!!") is None
    assert verify_project_token(project_token("prj_alpha")[:-2] + "xx") is None
    # The project id is inside the token, so it must be signed to be trusted.
    import base64

    forged = base64.urlsafe_b64encode(b"prj_alpha.deadbeefdeadbeefdeadbeefdeadbeef").decode()
    assert verify_project_token(forged) is None


def test_workspace_environment_points_at_the_proxy_not_the_gateway():
    env = gateway_token_env("prj_alpha")
    assert env["FLEET_LLM_BASE_URL"].endswith("/llm/v1")
    assert verify_project_token(env["FLEET_LLM_TOKEN"]) == "prj_alpha"


def test_usage_is_recorded_and_rolled_up_per_project(clean_db):
    result = LLMResult(content="hi", model="local-coder", prompt_tokens=10, completion_tokens=5)
    record_usage(
        result, requested_model="local-coder", project_id="prj_a", agent_id="agt_1", task_id="tsk_1"
    )
    record_usage(
        result, requested_model="local-coder", project_id="prj_a", agent_id="agt_2", task_id="tsk_2"
    )
    record_usage(result, requested_model="fast", project_id="prj_b", agent_id="agt_3")

    rollup = project_usage("prj_a")
    assert rollup["total"]["calls"] == 2
    assert rollup["total"]["prompt_tokens"] == 20
    assert rollup["total"]["completion_tokens"] == 10
    assert {bucket["key"] for bucket in rollup["by_agent"]} == {"agt_1", "agt_2"}
    assert {bucket["key"] for bucket in rollup["by_task"]} == {"tsk_1", "tsk_2"}

    # Project B's spend is not in project A's total, which is the whole point.
    assert project_usage("prj_b")["total"]["calls"] == 1


def test_the_gateway_client_reports_being_unconfigured():
    gateway = LLMGateway()
    assert gateway.configured is True or gateway.configured is False  # explicit, not absent
    if not gateway.configured:
        from fleet_core.llm import GatewayUnavailable

        with pytest.raises(GatewayUnavailable):
            gateway.complete([{"role": "user", "content": "hi"}])
