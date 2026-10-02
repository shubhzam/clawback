import logging
from typing import Callable

from langgraph.graph import END, START, StateGraph

from agents import classifier, dispute, extractor, orchestrator, policy, retrieve, validator
from core.deps import PipelineDeps
from graph.state import PipelineState

logger = logging.getLogger(__name__)

NODE_ORDER = ["retrieve", "classify", "extract", "policy", "validate", "orchestrate", "dispute"]


def _instrument(name: str, fn: Callable, deps: PipelineDeps) -> Callable:
    # wraps an agent so the ui gets a running/done event around every node
    def node(state: PipelineState) -> dict:
        deps.emit({"type": "agent", "case_id": state["case_id"], "agent": name, "state": "running"})
        update = fn(state, deps)
        deps.emit({"type": "agent", "case_id": state["case_id"], "agent": name, "state": "done"})
        return update

    return node


def _route_after_orchestrate(state: PipelineState) -> str:
    return "dispute" if state["decision"]["decision"] == "DISPUTE" else "end"


def build_graph(deps: PipelineDeps):
    agents = {
        "retrieve": retrieve.run,
        "classify": classifier.run,
        "extract": extractor.run,
        "policy": policy.run,
        "validate": validator.run,
        "orchestrate": orchestrator.run,
        "dispute": dispute.run,
    }
    graph = StateGraph(PipelineState)
    for name in NODE_ORDER:
        graph.add_node(name, _instrument(name, agents[name], deps))
    graph.add_edge(START, "retrieve")
    for upstream, downstream in zip(NODE_ORDER[:5], NODE_ORDER[1:6]):
        graph.add_edge(upstream, downstream)
    graph.add_conditional_edges("orchestrate", _route_after_orchestrate, {"dispute": "dispute", "end": END})
    graph.add_edge("dispute", END)
    return graph.compile()
