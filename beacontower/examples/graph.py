"""The asset graph: its start nodes, and the tree below the first one."""

from _common import connect

from beacontower.metadata.datamodel import GraphTree

bt = connect()
starts = bt.get_graph_list_start_nodes()
print(f"{len(starts)} start nodes")


def show(node: GraphTree, depth: int = 0) -> None:
    """Print a node and the nodes below it, indented by depth."""
    print(f"{'  ' * depth}{node.display_name}  ({node.type}, {node.id})")
    for child in node.children or []:
        show(child, depth + 1)


if starts:
    show(bt.get_graph_tree(starts[0].id))
