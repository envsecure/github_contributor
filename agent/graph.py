from __future__ import annotations

from typing import Literal

from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import (
    parse_input_node,
    fetch_info_node,
    clone_and_analyze_node,
    generate_plan_node,
    plan_approved_node,
    execute_changes_node,
)
from context import SessionContext


def _router_analysis(state: AgentState) -> Literal["fetch_info", "clone_and_analyze", "generate_plan", END]:
    if state.error:
        return END
    if state.done:
        return END
    return {
        "parsed": "fetch_info",
        "info_fetched": "clone_and_analyze",
        "analyzed": "generate_plan",
        "plan_ready": END,
        "awaiting_input": END,
    }.get(state.step, END)


def _router_execution(state: AgentState) -> Literal["execute_changes", END]:
    if state.error:
        return END
    if state.done:
        return END
    if state.step == "approved":
        return "execute_changes"
    return END


def build_analysis_graph(ctx: SessionContext) -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("parse_input", lambda s: parse_input_node(s, ctx))
    workflow.add_node("fetch_info", lambda s: fetch_info_node(s, ctx))
    workflow.add_node("clone_and_analyze", lambda s: clone_and_analyze_node(s, ctx))
    workflow.add_node("generate_plan", lambda s: generate_plan_node(s, ctx))

    workflow.set_entry_point("parse_input")

    workflow.add_conditional_edges("parse_input", _router_analysis)
    workflow.add_conditional_edges("fetch_info", _router_analysis)
    workflow.add_conditional_edges("clone_and_analyze", _router_analysis)
    workflow.add_conditional_edges("generate_plan", _router_analysis)

    return workflow.compile()


def build_execution_graph(ctx: SessionContext) -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("plan_approved", lambda s: plan_approved_node(s, ctx))
    workflow.add_node("execute_changes", lambda s: execute_changes_node(s, ctx))

    workflow.set_entry_point("plan_approved")

    workflow.add_conditional_edges("plan_approved", _router_execution)
    workflow.add_conditional_edges("execute_changes", _router_execution)

    return workflow.compile()
