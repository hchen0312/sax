# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/08_circuit.ipynb (unless otherwise specified).


from __future__ import annotations


__all__ = ['create_dag', 'draw_dag', 'find_root', 'find_leaves', 'circuit', 'CircuitInfo']

# Cell
#nbdev_comment from __future__ import annotations

import os
import shutil
import sys
from functools import partial
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, Union

import black
import networkx as nx
import numpy as np
from pydantic import ValidationError
from sax import reciprocal
from .backends import circuit_backends
from .multimode import multimode, singlemode
from .netlist import Netlist, RecursiveNetlist
from .typing_ import Model, Settings, SType
from .utils import _replace_kwargs, get_settings, merge_dicts

# Cell
def create_dag(
    netlist: RecursiveNetlist,
    models: Optional[Dict[str, Any]] = None,
):
    if models is None:
        models = {}
    assert isinstance(models, dict)

    all_models = {}
    g = nx.DiGraph()

    for model_name, netlist in netlist.dict()['__root__'].items():
        if not model_name in all_models:
            all_models[model_name] = models.get(model_name, netlist)
            g.add_node(model_name)
        if model_name in models:
            continue
        for instance in netlist['instances'].values():
            component = instance['component']
            if not component in all_models:
                all_models[component] = models.get(component, None)
                g.add_node(component)
            g.add_edge(model_name, component)

    return g

# Cell

def draw_dag(dag, with_labels=True, **kwargs):
    _patch_path()
    if shutil.which('dot'):
        return nx.draw(dag, nx.nx_pydot.pydot_layout(dag, prog='dot'), with_labels=with_labels, **kwargs)
    else:
        return nx.draw(dag, _my_dag_pos(dag), with_labels=with_labels, **kwargs)

def _patch_path():
    os_paths = {p: None for p in os.environ.get('PATH', '').split(os.pathsep)}
    sys_paths = {p: None for p in sys.path}
    other_paths = {os.path.dirname(sys.executable): None}
    os.environ['PATH'] = os.pathsep.join({**os_paths, **sys_paths, **other_paths})

def _my_dag_pos(dag):
    # inferior to pydot
    in_degree = {}
    for k, v in dag.in_degree():
        if v not in in_degree:
            in_degree[v] = []
        in_degree[v].append(k)

    widths = {k: len(vs) for k, vs in in_degree.items()}
    width = max(widths.values())
    height = max(widths) + 1

    horizontal_pos = {k: np.linspace(0, 1, w+2)[1:-1]*width for k, w in widths.items()}

    pos = {}
    for k, vs in in_degree.items():
        for x, v in zip(horizontal_pos[k], vs):
            pos[v] = (x, -k)
    return pos

# Cell
def find_root(g):
    nodes = [n for n, d in g.in_degree() if d == 0]
    return nodes

# Cell
def find_leaves(g):
    nodes = [n for n, d in g.out_degree() if d == 0]
    return nodes

# Cell
def _validate_models(models, dag):
    required_models = find_leaves(dag)
    missing_models = [m for m in required_models if m not in models]
    if missing_models:
        model_diff = {
            "Missing Models": missing_models,
            "Given Models": list(models),
            "Required Models": required_models,
        }
        raise ValueError(
            "Missing models. The following models are still missing to build the circuit:\n"
            f"{black.format_str(repr(model_diff), mode=black.Mode())}"
        )
    return {**models} # shallow copy

# Cell
def _flat_circuit(instances, connections, ports, models, backend):
    evaluate_circuit = circuit_backends[backend]

    inst2model = {k: models[inst.component] for k, inst in instances.items()}

    model_settings = {name: get_settings(model) for name, model in inst2model.items()}
    netlist_settings = {
        name: {k: v for k, v in inst.settings.items() if k in model_settings[name]}
        for name, inst in instances.items()
    }
    default_settings = merge_dicts(model_settings, netlist_settings)

    def _circuit(**settings: Settings) -> SType:
        settings = merge_dicts(model_settings, settings)
        instances: Dict[str, SType] = {}
        for inst_name, model in inst2model.items():
            instances[inst_name] = model(**settings.get(inst_name, {}))
        S = evaluate_circuit(instances, connections, ports)
        return S

    _replace_kwargs(_circuit, **default_settings)

    return _circuit

# Cell
def circuit(
    netlist: Union[Netlist, RecursiveNetlist],
    models: Dict[str, Model],
    modes: Optional[List[str]] = None,
    backend: str = "default",
) -> Tuple[Model, CircuitInfo]:

    recnet: RecursiveNetlist = _validate_net(netlist)
    dependency_dag: nx.DiGraph = _validate_dag(create_dag(recnet, models))  # directed acyclic graph
    models = _validate_models(models, dependency_dag)
    modes = _validate_modes(modes)
    backend = _validate_circuit_backend(backend)

    circuit = None
    new_models = {}
    current_models = {}
    model_names = list(nx.topological_sort(dependency_dag))[::-1]
    for model_name in model_names:
        if model_name in models:
            new_models[model_name] = models[model_name]
            continue

        flatnet = recnet.__root__[model_name]

        connections, ports, new_models = _make_singlemode_or_multimode(
            flatnet, modes, new_models
        )
        current_models.update(new_models)
        new_models = {}

        current_models[model_name] = circuit = _flat_circuit(
            flatnet.instances, connections, ports, current_models, backend
        )

    assert circuit is not None
    return circuit, CircuitInfo(dag=dependency_dag, models=current_models)

class CircuitInfo(NamedTuple):
    dag: nx.DiGraph
    models: Dict[str, Model]

def _validate_circuit_backend(backend):
    backend = backend.lower()
    # assert valid circuit_backend
    if backend not in circuit_backends:
        raise KeyError(
            f"circuit backend {backend} not found. Allowed circuit backends: "
            f"{', '.join(circuit_backends.keys())}."
        )
    return backend


def _validate_modes(modes) -> List[str]:
    if modes is None:
        return ["te"]
    elif not modes:
        return ["te"]
    elif isinstance(modes, str):
        return [modes]
    elif all(isinstance(m, str) for m in modes):
        return modes
    else:
        raise ValueError(f"Invalid modes given: {modes}")


def _validate_net(
    netlist: Union[Netlist, RecursiveNetlist]
) -> RecursiveNetlist:
    if isinstance(netlist, dict):
        try:
            netlist = Netlist.parse_obj(netlist)
        except ValidationError:
            netlist = RecursiveNetlist.parse_obj(netlist)
    if isinstance(netlist, Netlist):
        netlist = RecursiveNetlist(__root__={"top_level": netlist})
    return netlist


def _validate_dag(dag):
    nodes = find_root(dag)
    if len(nodes) > 1:
        raise ValueError(f"Multiple top_levels found in netlist: {nodes}")
    if len(nodes) < 1:
        raise ValueError(f"Netlist does not contain any nodes.")
    if not dag.is_directed():
        raise ValueError("Netlist dependency cycles detected!")
    return dag


def _make_singlemode_or_multimode(netlist, modes, models):
    if len(modes) == 1:
        connections, ports, models = _make_singlemode(netlist, modes[0], models)
    else:
        connections, ports, models = _make_multimode(netlist, modes, models)
    return connections, ports, models


def _make_singlemode(netlist, mode, models):
    models = {k: singlemode(m, mode=mode) for k, m in models.items()}
    return netlist.connections, netlist.ports, models


def _make_multimode(netlist, modes, models):
    models = {k: multimode(m, modes=modes) for k, m in models.items()}
    connections = {
        f"{p1}@{mode}": f"{p2}@{mode}"
        for p1, p2 in netlist.connections.items()
        for mode in modes
    }
    ports = {
        f"{p1}@{mode}": f"{p2}@{mode}" for p1, p2 in netlist.ports.items() for mode in modes
    }
    return connections, ports, models