# LangGraph ships without complete type annotations: the relaxation is confined to this
# wiring-only module so that the node logic (nodes.py) stays under strict checking.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""The fix loop as a LangGraph state machine (docs/SPEC.md §6).

lint -> group -> next_group --(queue empty)--------------------------> finalize -> END
                     |------(needs human / budget exhausted)--> record -> next_group
                     '------(auto)--> propose -> guard -> apply_verify --(resolved)--> record
                                        ^         |            |
                                        '--retry--+------------'   (attempts left)
                                                  '--give up--> record
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from govagent.agent.nodes import AgentDeps, Nodes, route_after_attempt_step
from govagent.agent.state import AgentState


def build_graph(deps: AgentDeps) -> Any:
    """Compile the graph. `invoke(AgentState(...))` returns the final state as a dict."""
    nodes = Nodes(deps)
    graph = StateGraph(AgentState)
    graph.add_node("lint", nodes.lint)
    graph.add_node("group", nodes.group)
    graph.add_node("next_group", nodes.next_group)
    graph.add_node("propose", nodes.propose)
    graph.add_node("guard", nodes.guard)
    graph.add_node("apply_verify", nodes.apply_verify)
    graph.add_node("record", nodes.record)
    graph.add_node("finalize", nodes.finalize)

    on_failure = {"record": "record", "retry": "propose"}
    graph.add_edge(START, "lint")
    graph.add_edge("lint", "group")
    graph.add_edge("group", "next_group")
    graph.add_conditional_edges(
        "next_group",
        nodes.route_next_group,
        {"finalize": "finalize", "record": "record", "propose": "propose"},
    )
    graph.add_conditional_edges(
        "propose", route_after_attempt_step, {"next": "guard", **on_failure}
    )
    graph.add_conditional_edges(
        "guard", route_after_attempt_step, {"next": "apply_verify", **on_failure}
    )
    graph.add_conditional_edges(
        "apply_verify", route_after_attempt_step, {"next": "record", **on_failure}
    )
    graph.add_edge("record", "next_group")
    graph.add_edge("finalize", END)
    return graph.compile()
