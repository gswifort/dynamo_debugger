from __future__ import annotations

import collections.abc as c
import json
import os
import shutil
import tempfile
import typing as t
from copy import deepcopy
from pathlib import Path

if t.TYPE_CHECKING:
    import _typeshed as _t

BASE_DIR = Path(__file__).parent
TEMP_NODE_TEMPLATE = BASE_DIR / "temp_node.py.template"
VSCODE_LAUNCH_JSON_TEMPLATE = BASE_DIR / "launch.json.template"
START_DEBUG_DYN_TEMPLATE = BASE_DIR / "start_debug.dyn.template"


class PythonScriptNode(t.TypedDict):
    """Represents a PythonScriptNode in Dynamo."""

    ConcreteType: t.Required[str]
    NodeType: t.Required[str]
    Code: t.Required[str]
    Engine: t.Required[str]
    EngineName: t.Required[str]
    VariableInputPorts: t.Required[bool]
    Id: t.Required[str]
    Inputs: t.Required[list[dict[str, t.Any]]]
    Outputs: t.Required[list[dict[str, t.Any]]]
    Replication: t.Required[str]
    Description: t.Required[str]


class NodeView(t.TypedDict):
    """Represents the view configuration of a node in Dynamo."""

    Name: str
    ShowGeometry: bool
    Id: str
    IsSetAsInput: bool
    IsSetAsOutput: bool
    Excluded: bool
    X: float
    Y: float


def get_default_python_script_node() -> PythonScriptNode:
    """Returns a default PythonScriptNode configuration."""
    return PythonScriptNode(
        ConcreteType="PythonNodeModels.PythonNode, PythonNodeModels",
        NodeType="PythonScriptNode",
        Code="",
        Engine="CPython3",
        EngineName="CPython3",
        VariableInputPorts=True,
        Id="",
        Inputs=[],
        Outputs=[],
        Replication="Disabled",
        Description="Runs an embedded Python script.",
    )


def get_default_node_view() -> NodeView:
    """Returns a default NodeView configuration."""
    return NodeView(
        Name="",
        ShowGeometry=False,
        Id="",
        IsSetAsInput=False,
        IsSetAsOutput=False,
        Excluded=False,
        X=0.0,
        Y=0.0,
    )


def load_dynamo_src_from_file(dyn_file: _t.StrPath) -> dict:
    """Loads Dynamo source from a .dyn file."""
    return json.loads(Path(dyn_file).read_text(encoding="utf-8"))


def dump_dynamo_src_to_file(dyn_src: dict, dyn_file: _t.StrPath) -> None:
    """Dumps Dynamo source to a .dyn file."""
    Path(dyn_file).write_text(json.dumps(dyn_src, indent=4, ensure_ascii=False), encoding="utf-8")


def filter_python_nodes(
    dynamo_src: dict, only_cpython3: bool = True
) -> c.Generator[PythonScriptNode, None, None]:
    """
    Filters PythonScriptNodes from Dynamo source.

    Args:
        dynamo_src (dict): The Dynamo source dictionary.
        only_cpython3 (bool): If True, filters only nodes using CPython3.

    Yields:
        PythonScriptNode: Filtered PythonScriptNode objects.
    """
    nodes = dynamo_src.get("Nodes", None)
    if not nodes:
        return
    for node in nodes:
        node_type = node.get("NodeType", None)
        if node_type == "PythonScriptNode":
            if only_cpython3 and node.get("Engine", None) != "CPython3":
                continue
            yield node


def load_python_code(code: str) -> str:
    return "\n".join(code.split("\r\n"))


def dump_python_code(code: str) -> str:
    return "\r\n".join(code.splitlines(keepends=False))


class DynamoSrc:
    """Manages Dynamo source data."""

    def __init__(self, src: dict, file_path: _t.StrPath | None = None):
        self.src = src
        self.file_path = Path(file_path) if file_path else None

    @classmethod
    def load(cls, file_path: _t.StrPath) -> DynamoSrc:
        """Loads Dynamo source from a file."""
        src = load_dynamo_src_from_file(file_path)
        return cls(src, file_path)

    def dump(self, file_path: _t.StrPath) -> None:
        """Dumps the Dynamo source to a file."""
        dump_dynamo_src_to_file(self.src, file_path)

    @property
    def python_nodes(self) -> dict[str, PythonScriptNode]:
        """Returns PythonScriptNodes from the Dynamo source."""
        return dict((node["Id"], node) for node in filter_python_nodes(self.src))


class TempDynamoProj:
    def __init__(self, src: DynamoSrc, proj_path: _t.StrPath):
        self.src = src
        self.temp_src = DynamoSrc(deepcopy(src.src))
        self.proj_path = Path(proj_path)
        self.node_id_to_file_path = self.proj_path / "_node_id_to_file.json"

        self._temp_node_template = TEMP_NODE_TEMPLATE.read_text(encoding="utf-8")

    def create_temp_project(self) -> tuple[Path, Path]:
        """Creates a temporary debugging project."""
        try:
            self.proj_path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            try:
                next(self.proj_path.iterdir())
            except StopIteration:
                # Directory exists but is empty, we can use it
                pass
            else:
                raise RuntimeError(f"Project directory {self.proj_path} is not empty.")
        # Create temporary Python files for each Python node
        node_id_to_file: dict[str, str] = {}
        node_views = dict((node["Id"], node) for node in self.temp_src.src["View"]["NodeViews"])
        for e, (node_id, node) in enumerate(self.src.python_nodes.items()):
            code = load_python_code(node["Code"])
            number = str(e).zfill(3)
            file_name = f"python_node_{number}.py"
            file_path = self.proj_path / file_name
            file_path.write_text(code, encoding="utf-8")
            node_id_to_file[node_id] = file_name
            temp_node = self.temp_src.python_nodes[node_id]
            temp_node["Code"] = dump_python_code(self.get_temp_node_src(file_path))
            node_views[node_id]["Name"] = f"{node_views[node_id]['Name']} [{number}]"
        self.node_id_to_file_path.write_text(
            json.dumps(node_id_to_file, indent=4, ensure_ascii=False), encoding="utf-8"
        )
        if self.src.file_path:
            temp_dynamo_src_file = self.proj_path / self.src.file_path.name
        else:
            temp_dynamo_src_file = self.proj_path / "temp.dyn"
        start_debug_script_path = self.proj_path / "start_debug.dyn"
        self.write_debug_script(start_debug_script_path)
        self.temp_src.dump(temp_dynamo_src_file)
        self.write_vscode_launch_json()
        return temp_dynamo_src_file, start_debug_script_path

    def get_temp_node_src(self, file_path: _t.StrPath) -> str:
        """Generates the source code for a temporary node."""
        return self._temp_node_template.replace("{temp_python_node_path}", str(file_path))

    def write_vscode_launch_json(self) -> None:
        """Writes a VSCode launch.json file for debugging."""
        launch_json_path = self.proj_path / ".vscode" / "launch.json"
        launch_json_path.parent.mkdir(parents=True, exist_ok=True)
        launch_json_content = VSCODE_LAUNCH_JSON_TEMPLATE.read_text(encoding="utf-8")
        launch_json_path.write_text(launch_json_content, encoding="utf-8")

    def write_debug_script(self, path: _t.StrPath) -> None:
        """Copies the debug script to the temporary project."""
        shutil.copy(START_DEBUG_DYN_TEMPLATE, path)


def main(dyn_file: _t.StrPath) -> None:
    """
    Main function to load Dynamo source and create a temporary debugging project.

    Args:
        dyn_file (_t.StrPath): Path to the .dyn file to debug.
    """
    dyn_file_path = Path(dyn_file)
    dynamo_src = DynamoSrc.load(dyn_file)
    with tempfile.TemporaryDirectory(
        prefix=dyn_file_path.name, dir=dyn_file_path.parent, delete=False
    ) as temp_dir:
        temp_proj = TempDynamoProj(dynamo_src, Path(temp_dir))
        temp_dynamo_src_file, start_debug_script_path = temp_proj.create_temp_project()
        print(f"Temporary project created at {temp_proj.proj_path}")
        print(
            f"1. Open {start_debug_script_path} in Dynamo to start debugging "
            "(only once per Dynamo session)."
        )
        print(
            f"2. Open Visual Studio Code in {temp_proj.proj_path} and "
            "start debugging from 'Python Debugger: Remote Attach' profile."
        )
        print("Trying to open the project in VSCode...")
        if os.system(f"code {temp_proj.proj_path}"):
            print("Failed to open VSCode. Please open the project manually.")
        else:
            print("VSCode opened successfully. You can start debugging now.")
        print("3. Add breakpoints in the Python nodes in VSCode.")
        print(f"4. Open {temp_dynamo_src_file} in Dynamo and run script.")
