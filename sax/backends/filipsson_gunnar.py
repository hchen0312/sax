""" SAX Filipsson Gunnar Backend """

from __future__ import annotations

from typing import Any, Dict

import jax

from ..netlist import Component
from ..saxtypes import Model, SDict, SType, sdict


def analyze_instances_fg(
    instances: Dict[str, Component],
    models: Dict[str, Model],
) -> Dict[str, SDict]:
    instances, instances_old = {}, instances
    for k, v in instances_old.items():
        if not isinstance(v, Component):
            v = Component(**v)
        instances[k] = v
    model_names = set(str(i.component) for i in instances.values())
    dummy_models = {k: sdict(models[k]()) for k in model_names}
    dummy_instances = {k: dummy_models[str(i.component)] for k, i in instances.items()}
    return dummy_instances


def analyze_circuit_fg(
    analyzed_instances: Dict[str, SDict],
    connections: Dict[str, str],
    ports: Dict[str, str],
) -> Any:
    # skip analysis for now
    return connections, ports


def evaluate_circuit_fg(
    analyzed: Any,
    instances: Dict[str, SType],
) -> SDict:
    """evaluate a circuit for the given sdicts."""
    connections, ports = analyzed

    # it's actually easier working w reverse:
    reversed_ports = {v: k for k, v in ports.items()}

    block_diag = {}
    for name, S in instances.items():
        block_diag.update(
            {(f"{name},{p1}", f"{name},{p2}"): v for (p1, p2), v in sdict(S).items()}
        )

    sorted_connections = sorted(connections.items(), key=_connections_sort_key)
    all_connected_instances = {k: {k} for k in instances}

    for k, l in sorted_connections:  # noqa: E741
        name1, _ = k.split(",")
        name2, _ = l.split(",")

        connected_instances = (
            all_connected_instances[name1] | all_connected_instances[name2]
        )
        for name in connected_instances:
            all_connected_instances[name] = connected_instances

        current_ports = tuple(
            p
            for instance in connected_instances
            for p in set([p for p, _ in block_diag] + [p for _, p in block_diag])
            if p.startswith(f"{instance},")
        )

        block_diag.update(_interconnect_ports(block_diag, current_ports, k, l))

        for i, j in list(block_diag.keys()):
            is_connected = i == k or i == l or j == k or j == l
            is_in_output_ports = i in reversed_ports and j in reversed_ports
            if is_connected and not is_in_output_ports:
                del block_diag[
                    i, j
                ]  # we're no longer interested in these port combinations

    circuit_sdict: SDict = {
        (reversed_ports[i], reversed_ports[j]): v
        for (i, j), v in block_diag.items()
        if i in reversed_ports and j in reversed_ports
    }
    return circuit_sdict


def _connections_sort_key(connection):
    """sort key for sorting a connection dictionary"""
    part1, part2 = connection
    name1, _ = part1.split(",")
    name2, _ = part2.split(",")
    return (min(name1, name2), max(name1, name2))


def _interconnect_ports(block_diag, current_ports, k, l):  # noqa: E741
    """interconnect two ports in a given model

    > Note: the interconnect algorithm is based on equation 6 of 'Filipsson, Gunnar.
      "A new general computer algorithm for S-matrix calculation of interconnected
      multiports." 11th European Microwave Conference. IEEE, 1981.'
    """
    current_block_diag = {}
    for i in current_ports:
        for j in current_ports:
            vij = _calculate_interconnected_value(
                vij=block_diag.get((i, j), 0.0),
                vik=block_diag.get((i, k), 0.0),
                vil=block_diag.get((i, l), 0.0),
                vkj=block_diag.get((k, j), 0.0),
                vkk=block_diag.get((k, k), 0.0),
                vkl=block_diag.get((k, l), 0.0),
                vlj=block_diag.get((l, j), 0.0),
                vlk=block_diag.get((l, k), 0.0),
                vll=block_diag.get((l, l), 0.0),
            )
            current_block_diag[i, j] = vij
    return current_block_diag


@jax.jit
def _calculate_interconnected_value(vij, vik, vil, vkj, vkk, vkl, vlj, vlk, vll):
    """Calculate an interconnected S-parameter value

    Note:
        The interconnect algorithm is based on equation 6 in the paper below::

          Filipsson, Gunnar. "A new general computer algorithm for S-matrix calculation
          of interconnected multiports." 11th European Microwave Conference. IEEE, 1981.
    """
    result = vij + (
        vkj * vil * (1 - vlk)
        + vlj * vik * (1 - vkl)
        + vkj * vll * vik
        + vlj * vkk * vil
    ) / ((1 - vkl) * (1 - vlk) - vkk * vll)
    return result
